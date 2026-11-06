"""El grafo completo, con modelos falsos: ruteo, pausas (aclaración y aprobación), ejecución y cierres controlados."""
import json
import sqlite3

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.agents.comun import Dependencias
from app.clients import ModelConfig
from app.graph import construir_grafo
from app.state import estado_inicial
from tests.fakes import ChatFalso, TextoFalso, aviso_valido, ruta

CFG = ModelConfig(model_name="modelo-de-prueba")
TEXTO_OK = "Revisé lo que pediste y dejé el detalle de lo hecho y de lo pendiente en esta respuesta, para que lo revises con calma."


def redactor_y_avisos(mensajes):
    """Texto falso: avisos válidos para el redactor de avisos y una respuesta final sin cifras para el redactor."""
    sistema = mensajes[0].content
    return aviso_valido(mensajes) if sistema.startswith("Redactas avisos") else TEXTO_OK


def montar(bd, chat, texto=None, **kw):
    deps = Dependencias(ruta_db=str(bd), chat=chat, texto=texto or TextoFalso(redactor_y_avisos), config_texto=CFG, **kw)
    return construir_grafo(deps, checkpointer=InMemorySaver()), deps


async def correr(grafo, entrada, hilo="h1"):
    """Corre (o reanuda) el grafo. Devuelve (estado, lo que quedó esperando, nodos recorridos)."""
    cfg = {"configurable": {"thread_id": hilo}}
    pausas, nodos = [], []
    async for chunk in grafo.astream(entrada, cfg, stream_mode="updates"):
        if "__interrupt__" in chunk:
            pausas.append(chunk["__interrupt__"][0].value)
        else:
            nodos.extend(chunk)
    return (await grafo.aget_state(cfg)).values, pausas, nodos


def contar(bd, tabla, **donde):
    con = sqlite3.connect(bd)
    cond = " AND ".join(f"{k} = ?" for k in donde) or "1=1"
    try:
        return con.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {cond}", tuple(donde.values())).fetchone()[0]
    finally:
        con.close()


# ================================================================ derivar -> aprobar -> ejecutar -> responder
async def test_una_consulta_de_inventario_pausa_en_la_compuerta_y_ejecuta_solo_lo_aprobado(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario")]))
    estado, pausas, nodos = await correr(grafo, estado_inicial("¿Qué cartera deberíamos ordenar pronto?"))
    assert nodos == ["orquestador", "inventario"] and len(pausas) == 1 and pausas[0]["tipo"] == "aprobacion"
    propuestas = pausas[0]["acciones"]
    assert [a["posicion"] for a in propuestas] == list(range(len(propuestas))) and all(a["accion"] == "crear_orden_compra" for a in propuestas)
    antes = contar(bd, "ordenes_compra")
    assert antes == 7  # lo propuesto todavía NO se ejecutó

    pos = next(a["posicion"] for a in propuestas if a["sku"] == "BAG-001")
    estado, pausas, nodos = await correr(grafo, Command(resume={"aprobadas": [pos]}))
    assert nodos == ["compuerta", "ejecutar", "redactor"] and pausas == []
    assert contar(bd, "ordenes_compra") == antes + 1 and contar(bd, "ordenes_compra", sku="BAG-001", estado="creada") == 1
    assert estado["respuesta"]["texto"] == TEXTO_OK and estado["respuesta"]["origen"] == "llm" and not estado.get("error")
    assert [r["ok"] for r in estado["ejecucion"]] == [True] and "BAG-001" in estado["ejecucion"][0]["detalle"]


async def test_si_el_usuario_no_aprueba_nada_no_se_escribe_nada(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario")]))
    await correr(grafo, estado_inicial("¿Qué ordenar?"))
    estado, _, _ = await correr(grafo, Command(resume={"aprobadas": [], "comentario": "no por ahora"}))
    assert estado["ejecucion"] == [] and contar(bd, "ordenes_compra") == 7
    assert estado["respuesta"]["origen"] == "llm"


async def test_una_consulta_cruzada_recorre_los_especialistas_en_orden_fijo(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario", "logistica")]), max_pedidos_logistica=4)
    estado, pausas, nodos = await correr(grafo, estado_inicial("¿Tenemos stock para cubrir los pedidos por preparar?"))
    assert nodos == ["orquestador", "inventario", "logistica"]
    assert [r["agente"] for r in estado["resultados"]] == ["inventario", "logistica"]
    tipos = {a["accion"] for a in pausas[0]["acciones"]}
    assert "crear_orden_compra" in tipos and "enviar_aviso" in tipos  # propuestas de los dos especialistas, en una sola compuerta


async def test_aprobar_todo_ejecuta_cada_accion_y_deja_los_datos_consistentes(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario", "logistica")]), max_pedidos_logistica=10)
    _, pausas, _ = await correr(grafo, estado_inicial("revisa inventario y pedidos"))
    todas = [a["posicion"] for a in pausas[0]["acciones"]]
    avisos_antes = contar(bd, "bandeja_avisos")
    estado, _, nodos = await correr(grafo, Command(resume={"aprobadas": todas}))
    assert nodos == ["compuerta", "ejecutar", "redactor"]
    assert all(r["ok"] for r in estado["ejecucion"]), [r["detalle"] for r in estado["ejecucion"] if not r["ok"]]
    assert len(estado["ejecucion"]) == len(todas)
    assert contar(bd, "bandeja_avisos") > avisos_antes and contar(bd, "compensaciones") > 2 and contar(bd, "reembolsos") > 1
    assert contar(bd, "pedidos", estado="cancelado") >= 3  # C008, C011 y los cancelados de antes
    con = sqlite3.connect(bd)
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert estado["respuesta"]["origen"] == "llm"


async def test_una_accion_que_quedo_vieja_se_rechaza_aunque_el_usuario_la_apruebe(bd):
    """Entre la propuesta y la aprobación el mundo cambia: las reglas se vuelven a comprobar al ejecutar."""
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "logistica")]), max_pedidos_logistica=10)
    _, pausas, _ = await correr(grafo, estado_inicial("revisa los pedidos atrasados"))
    prop = pausas[0]["acciones"]
    comp = next(a for a in prop if a["accion"] == "emitir_compensacion" and a["pedido_id"] == "PED-C001")
    con = sqlite3.connect(bd)  # alguien más ya emitió esa compensación mientras la propuesta esperaba
    con.execute("INSERT INTO compensaciones (pedido_id, tipo, estado, creada_en) VALUES ('PED-C001', 'reembolso_envio', 'emitida', '2026-11-03 11:00:00')")
    con.commit()
    estado, _, _ = await correr(grafo, Command(resume={"aprobadas": [comp["posicion"]]}))
    assert [r["ok"] for r in estado["ejecucion"]] == [False] and "Rechazada" in estado["ejecucion"][0]["detalle"]
    assert contar(bd, "compensaciones", pedido_id="PED-C001") == 1  # no se duplicó


async def test_una_decision_de_aprobacion_invalida_cierra_de_forma_controlada(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario")]))
    await correr(grafo, estado_inicial("¿Qué ordenar?"))
    estado, _, nodos = await correr(grafo, Command(resume={"aprobadas": [999]}))
    assert nodos[-1] == "cierre" and estado["error"].startswith("compuerta:") and contar(bd, "ordenes_compra") == 7
    assert "problema interno" in estado["respuesta"]["texto"]


# ================================================================ aclaración y fuera de alcance
async def test_una_consulta_ambigua_pregunta_espera_y_continua_con_la_respuesta(bd):
    def decidir(mensajes):
        consulta = mensajes[1][1]
        return ruta("derivar", "inventario") if "Aclaración: stock" in consulta else ruta("aclarar", pregunta="¿Qué quieres revisar: ventas, inventario o pedidos?")
    chat = ChatFalso(decidir)
    grafo, _ = montar(bd, chat)
    estado, pausas, nodos = await correr(grafo, estado_inicial("¿Cómo vamos?"))
    assert nodos == ["orquestador"] and pausas == [{"tipo": "aclaracion", "pregunta": "¿Qué quieres revisar: ventas, inventario o pedidos?", "intento": 1, "maximo": 2}]
    estado, pausas, nodos = await correr(grafo, Command(resume="stock"))
    assert nodos[:3] == ["aclarar", "orquestador", "inventario"] and pausas[0]["tipo"] == "aprobacion"
    assert estado["aclaraciones"] == 1 and estado["solicitud"]["texto"] == "¿Cómo vamos? | Aclaración: stock"


async def test_tras_dos_aclaraciones_sin_exito_responde_que_no_entiende(bd):
    chat = ChatFalso(lambda m: ruta("aclarar", pregunta="¿Puedes darme más detalle?"))
    grafo, _ = montar(bd, chat, texto=TextoFalso(lambda m: pytest.fail("no debía llamarse al LLM de texto")))
    _, pausas, _ = await correr(grafo, estado_inicial("asdf"))
    assert pausas[0]["intento"] == 1
    _, pausas, _ = await correr(grafo, Command(resume="no sé"))
    assert pausas[0]["intento"] == 2
    estado, pausas, nodos = await correr(grafo, Command(resume="tampoco sé"))
    assert pausas == [] and nodos[-2:] == ["orquestador", "redactor"] and len(chat.llamadas) == 3
    assert "No logré entender" in estado["respuesta"]["texto"] and estado["respuesta"]["origen"] == "plantilla"


async def test_fuera_de_alcance_no_toca_herramientas_ni_modelos_de_texto(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("fuera_de_alcance", motivo="no es del negocio")]),
                      texto=TextoFalso(lambda m: pytest.fail("no debía llamarse al LLM de texto")))
    estado, pausas, nodos = await correr(grafo, estado_inicial("¿Qué tiempo hará mañana?"))
    assert nodos == ["orquestador", "redactor"] and pausas == [] and estado["resultados"] == [] and estado["acciones"] == []
    assert "fuera de lo que puedo hacer" in estado["respuesta"]["texto"]


# ================================================================ cierres controlados
async def test_si_el_orquestador_no_logra_una_decision_valida_el_grafo_cierra_con_el_error_a_la_vista(bd):
    grafo, _ = montar(bd, ChatFalso(lambda m: "esto no es un JSON"))
    estado, pausas, nodos = await correr(grafo, estado_inicial("consulta cualquiera"))
    assert nodos == ["orquestador", "cierre"] and pausas == []
    assert estado["error"].startswith("orquestador: SalidaInvalida") and estado["respuesta"]["origen"] == "plantilla"


async def test_un_error_dentro_de_un_especialista_no_deja_el_grafo_colgado(bd, tmp_path):
    grafo, _ = montar(tmp_path / "no_existe.db", ChatFalso([ruta("derivar", "inventario")]))
    estado, _, nodos = await correr(grafo, estado_inicial("¿Qué ordenar?"))
    assert nodos[-1] == "cierre" and estado["error"].startswith("inventario:")


async def test_superar_el_limite_de_pasos_cierra_de_forma_controlada(bd):
    grafo, _ = montar(bd, ChatFalso([ruta("derivar", "inventario")]), max_pasos=1)
    estado, _, nodos = await correr(grafo, estado_inicial("¿Qué ordenar?"))
    assert nodos == ["orquestador", "cierre"] and "límite de pasos" in estado["error"]


async def test_dos_consultas_a_la_vez_no_se_mezclan(bd):
    import asyncio
    grafo, _ = montar(bd, ChatFalso(lambda m: ruta("derivar", "inventario") if "inventario" in m[1][1] else ruta("fuera_de_alcance", motivo="no es del negocio")))
    (e1, p1, _), (e2, p2, _) = await asyncio.gather(correr(grafo, estado_inicial("revisa el inventario"), "a"),
                                                     correr(grafo, estado_inicial("cuéntame un chiste"), "b"))
    assert len(p1) == 1 and p2 == [] and e1["resultados"] and e2["resultados"] == []
    assert "fuera de lo que puedo hacer" in e2["respuesta"]["texto"]


# ================================================================ la estructura del grafo
async def test_el_grafo_solo_puede_avanzar_en_el_orden_fijo(bd):
    grafo, _ = montar(bd, ChatFalso([]))
    aristas = {(e.source, e.target) for e in grafo.get_graph().edges}
    for atras in [("inventario", "bi"), ("logistica", "bi"), ("logistica", "inventario"), ("bi", "bi"), ("bi", "aclarar"),
                  ("inventario", "aclarar"), ("redactor", "compuerta"), ("ejecutar", "compuerta")]:
        assert atras not in aristas, atras
    assert {("bi", "inventario"), ("inventario", "logistica"), ("logistica", "compuerta"), ("compuerta", "ejecutar"),
            ("ejecutar", "redactor"), ("aclarar", "orquestador"), ("orquestador", "bi")} <= aristas
    assert all((n, "cierre") in aristas for n in ("orquestador", "bi", "inventario", "logistica", "aclarar", "compuerta", "ejecutar", "redactor"))
