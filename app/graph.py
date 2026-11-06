"""El grafo: orquestador -> especialistas (orden fijo) -> compuerta de aprobación -> ejecución -> redactor.

                          +--> aclarar (pausa: pregunta al usuario, máximo 2 veces) --+
                          |                                                           |
   inicio -> orquestador -+--> fuera de alcance ------------------------------------> redactor -> fin
                          |                                                           ^
                          +--> bi -> inventario -> logistica -+--> compuerta -> ejecutar
                                (solo los que la consulta necesita)    (pausa: aprueba el usuario)

Cualquier error, o superar el límite de pasos, lleva a `cierre`: un cierre controlado con el error a la vista.
El checkpointer (Redis en producción) guarda el estado en cada pausa, así la aprobación puede llegar mucho después.
"""
import asyncio

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from pydantic import TypeAdapter

from app.agents.bi import crear_nodo_bi
from app.agents.comun import Dependencias, seguro
from app.agents.inventario import crear_nodo_inventario
from app.agents.logistica import crear_nodo_logistica
from app.agents.orquestador import crear_nodo_aclarar, crear_nodo_orquestador
from app.agents.redactor import crear_nodo_cierre, crear_nodo_redactor
from app.schemas import AccionPropuesta, DecisionHumana
from app.state import Estado
from app.tools.actions import ejecutar_lote

_ACCION = TypeAdapter(AccionPropuesta)


def crear_nodo_compuerta():
    @seguro("compuerta")
    async def nodo(estado):
        acciones = estado["acciones"]
        # SIN efectos antes del interrupt: al reanudar, este nodo se ejecuta de nuevo desde su primera línea.
        respuesta = interrupt({"tipo": "aprobacion", "acciones": [{"posicion": i, **a} for i, a in enumerate(acciones)]})
        decision = DecisionHumana.model_validate(respuesta)
        if any(i >= len(acciones) for i in decision.aprobadas):
            raise ValueError("la decisión aprueba una posición que no existe")
        return {"decision_humana": decision.model_dump(), "pasos": 1}

    return nodo


def crear_nodo_ejecutar(deps: Dependencias):
    @seguro("ejecutar")
    async def nodo(estado):
        aprobadas = [_ACCION.validate_python(estado["acciones"][i]) for i in sorted(estado["decision_humana"]["aprobadas"])]
        resultados = await asyncio.to_thread(ejecutar_lote, deps.ruta_db, aprobadas) if aprobadas else []
        return {"ejecucion": [r.model_dump() for r in resultados], "pasos": 1}

    return nodo


def construir_grafo(deps: Dependencias, checkpointer=None):
    g = StateGraph(Estado)
    g.add_node("orquestador", crear_nodo_orquestador(deps))
    g.add_node("aclarar", crear_nodo_aclarar(deps))
    g.add_node("bi", crear_nodo_bi(deps))
    g.add_node("inventario", crear_nodo_inventario(deps))
    g.add_node("logistica", crear_nodo_logistica(deps))
    g.add_node("compuerta", crear_nodo_compuerta())
    g.add_node("ejecutar", crear_nodo_ejecutar(deps))
    g.add_node("redactor", crear_nodo_redactor(deps))
    g.add_node("cierre", crear_nodo_cierre())

    def destino(estado) -> str:
        """Después de orquestar o de un especialista: ¿qué sigue?"""
        if estado.get("error") or estado.get("pasos", 0) >= deps.max_pasos:
            return "cierre"
        decision = (estado.get("ruta") or {}).get("decision")
        if decision == "aclarar":
            return "aclarar"
        if decision == "fuera_de_alcance":
            return "redactor"
        pendientes = estado.get("tareas_pendientes") or []
        if pendientes:
            return pendientes[0]["agente"]  # el orden fijo ya viene validado en la decisión de ruta
        return "compuerta" if estado.get("acciones") else "redactor"

    def o_cierre(si_todo_bien: str):
        return lambda estado: "cierre" if estado.get("error") else si_todo_bien

    # Cada nodo solo puede avanzar: el orden fijo (bi -> inventario -> logistica) lo garantiza también la
    # ESTRUCTURA del grafo, no solo el contrato de la decisión. Un destino no permitido cierra de forma controlada.
    siguientes = {
        "orquestador": ("aclarar", "bi", "inventario", "logistica", "compuerta", "redactor", "cierre"),
        "bi": ("inventario", "logistica", "compuerta", "redactor", "cierre"),
        "inventario": ("logistica", "compuerta", "redactor", "cierre"),
        "logistica": ("compuerta", "redactor", "cierre"),
    }
    g.add_edge(START, "orquestador")
    for nodo, permitidos in siguientes.items():
        g.add_conditional_edges(nodo, lambda estado, ok=permitidos: destino(estado) if destino(estado) in ok else "cierre",
                                {n: n for n in permitidos})
    g.add_conditional_edges("aclarar", o_cierre("orquestador"), {"orquestador": "orquestador", "cierre": "cierre"})
    g.add_conditional_edges("compuerta", o_cierre("ejecutar"), {"ejecutar": "ejecutar", "cierre": "cierre"})
    g.add_conditional_edges("ejecutar", o_cierre("redactor"), {"redactor": "redactor", "cierre": "cierre"})
    g.add_conditional_edges("redactor", o_cierre("fin"), {"fin": END, "cierre": "cierre"})
    g.add_edge("cierre", END)
    return g.compile(checkpointer=checkpointer)
