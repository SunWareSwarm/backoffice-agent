"""Orquestador: decide a qué especialistas va la consulta, o si pregunta o la rechaza.

Dos nodos:
  - `orquestador`: el LLM propone una DecisionRuta (JSON validado). El CÓDIGO aplica los límites: tras el
    máximo de aclaraciones deja de preguntar y cierra con «no entiendo».
  - `aclarar`: pausa el grafo (interrupt) y le hace UNA pregunta al usuario; su respuesta amplía la consulta y
    el flujo vuelve al orquestador.
"""
from langgraph.types import interrupt

from app.agents.comun import Dependencias, pedir_json, seguro
from app.agents.prompts import ORQUESTADOR
from app.schemas import DecisionRuta, Solicitud

MOTIVO_SIN_ENTENDER = "No se pudo entender la consulta tras las aclaraciones"


def crear_nodo_orquestador(deps: Dependencias):
    @seguro("orquestador")
    async def nodo(estado):
        solicitud = Solicitud(**estado["solicitud"])
        decision = await pedir_json(deps.chat, DecisionRuta, ORQUESTADOR, f"Consulta del usuario:\n{solicitud.texto}")
        if decision.decision == "aclarar" and estado.get("aclaraciones", 0) >= deps.max_aclaraciones:
            decision = DecisionRuta(decision="fuera_de_alcance", motivo=MOTIVO_SIN_ENTENDER)
        return {
            "ruta": decision.model_dump(mode="json"),
            "tareas_pendientes": [t.model_dump() for t in decision.tareas],
            "pasos": 1,
        }

    return nodo


def crear_nodo_aclarar(deps: Dependencias):
    @seguro("aclarar")
    async def nodo(estado):
        intento = estado.get("aclaraciones", 0) + 1
        # Sin efectos antes del interrupt: al reanudar, este nodo se ejecuta de nuevo desde la primera línea.
        respuesta = interrupt({"tipo": "aclaracion", "pregunta": estado["ruta"]["pregunta_aclaratoria"],
                               "intento": intento, "maximo": deps.max_aclaraciones})
        extra = Solicitud(texto=str(respuesta)).texto
        combinado = f"{estado['solicitud']['texto']} | Aclaración: {extra}"[:500]
        return {"solicitud": {"texto": combinado}, "aclaraciones": 1, "pasos": 1}

    return nodo
