"""Los agentes por separado: lo que decide el LLM se acota, y lo que importa lo vuelve a comprobar el código."""
import json
from datetime import date

import pytest

from app.agents.bi import crear_nodo_bi
from app.agents.comun import Dependencias, fecha_larga
from app.agents.inventario import crear_nodo_inventario
from app.agents.logistica import aviso_de_plantilla, crear_nodo_logistica, rellenar, validar_aviso
from app.agents.orquestador import MOTIVO_SIN_ENTENDER, crear_nodo_orquestador
from app.agents.redactor import construir_hechos, redactar, respuesta_determinista, validar_respuesta
from app.clients import ModelConfig
from app.schemas import EnviarAviso, ResultadoBI, ResultadoInventario, ResultadoLogistica
from app.state import estado_inicial
from app.tools.comun import cargar_parametros
from tests.fakes import ChatFalso, TextoFalso, aviso_valido, ruta

CFG = ModelConfig(model_name="modelo-de-prueba")


def deps(bd, chat=None, texto=None, **kw):
    return Dependencias(ruta_db=str(bd), chat=chat or ChatFalso([]), texto=texto or TextoFalso(lambda m: "x"), config_texto=CFG, **kw)


def estado(consulta="consulta de prueba", agentes=("bi",)):
    e = estado_inicial(consulta)
    e["tareas_pendientes"] = [{"agente": a, "instruccion": f"resuelve lo tuyo ({a})"} for a in agentes]
    return e


# ================================================================ orquestador
async def test_el_orquestador_deriva_y_prepara_la_cola_de_tareas(bd):
    chat = ChatFalso([ruta("derivar", "bi", "inventario")])
    r = await crear_nodo_orquestador(deps(bd, chat))(estado_inicial("¿Qué cartera debería ordenar según la tendencia?"))
    assert r["ruta"]["decision"] == "derivar" and [t["agente"] for t in r["tareas_pendientes"]] == ["bi", "inventario"] and "error" not in r
    assert "Consulta del usuario:\n¿Qué cartera" in chat.llamadas[0][1][1]


async def test_tras_el_maximo_de_aclaraciones_deja_de_preguntar(bd):
    chat = ChatFalso([ruta("aclarar", pregunta="¿Qué quieres revisar?")] * 2)
    e = estado_inicial("¿Cómo vamos?")
    e["aclaraciones"] = 0
    assert (await crear_nodo_orquestador(deps(bd, chat))(e))["ruta"]["decision"] == "aclarar"
    e["aclaraciones"] = 2
    r = await crear_nodo_orquestador(deps(bd, chat))(e)
    assert r["ruta"]["decision"] == "fuera_de_alcance" and r["ruta"]["motivo"] == MOTIVO_SIN_ENTENDER and r["tareas_pendientes"] == []


async def test_una_decision_invalida_se_corrige_una_vez_y_luego_cierra_con_error(bd):
    chat = ChatFalso([ruta("derivar"), ruta("derivar", "inventario", "bi")])  # sin tareas, y luego en el orden equivocado
    r = await crear_nodo_orquestador(deps(bd, chat))(estado_inicial("consulta cualquiera"))
    assert r["error"].startswith("orquestador: SalidaInvalida") and "ruta" not in r and len(chat.llamadas) == 2
    assert "al menos una tarea" in chat.llamadas[1][-1][1]  # el segundo intento incluye el error del primero


# ================================================================ BI
def chat_bi(pasos, dictamen):
    """Un LLM de BI de mentira: da los pasos en orden y luego el dictamen."""
    pasos = list(pasos)

    def responder(mensajes):
        sistema = mensajes[0][1]
        if sistema.startswith("Eres el analista de BI") and "Decide el siguiente paso" in mensajes[1][1]:
            return json.dumps(pasos.pop(0) if pasos else {"paso": "terminar", "motivo": "ya está"})
        return json.dumps(dictamen)
    return ChatFalso(responder)


PASOS = [{"paso": "herramienta", "nombre": "resumen_ventas", "dias": 7}, {"paso": "herramienta", "nombre": "ventas_por_producto"},
         {"paso": "terminar", "motivo": "tengo lo necesario"}]


async def test_bi_aplica_las_reglas_del_negocio_aunque_el_llm_las_ignore(bd):
    dictamen = {"resumen": "Hay dos candidatos a revisar para el próximo drop.", "recomendaciones": [
        {"sku": "BAG-004", "accion": "incluir_en_drop", "motivo": "vendió todo su stock rápidamente"},
        {"sku": "BAG-003", "accion": "incluir_en_drop", "motivo": "tiene un gran volumen de ventas"},
        {"sku": "NO-EXISTE", "accion": "observar", "motivo": "producto inventado por el modelo"},
        {"sku": "BAG-004", "accion": "observar", "motivo": "repetido: debe ignorarse"}]}
    r = await crear_nodo_bi(deps(bd, chat_bi(PASOS, dictamen)))(estado())
    res = ResultadoBI(**r["resultados"][0])
    por_sku = {x.sku: x for x in res.recomendaciones}
    assert set(por_sku) == {"BAG-004", "BAG-003"}  # el inexistente y el repetido no entran
    b4, b3 = por_sku["BAG-004"], por_sku["BAG-003"]
    assert (b4.accion, b4.ajustada_por_reglas, b4.clasificacion, b4.evidencia_suficiente) == ("observar", True, "prospecto", False)
    assert (b3.accion, b3.ajustada_por_reglas, b3.clasificacion) == ("incluir_en_drop", False, "ganador")
    assert (b4.unidades, b4.ventas_por_unidad_de_stock, b3.unidades) == (12, 1.0, 60)  # las cifras las puso el código
    assert any("NO-EXISTE" in x for x in res.limitaciones) and any("BAG-004" in x for x in res.limitaciones)
    assert res.ventas_semana.unidades > 0 and res.pasos_usados == 2 and r["tareas_pendientes"] == []


async def test_bi_se_detiene_en_su_tope_de_pasos_aunque_el_llm_no_termine(bd):
    sin_fin = [{"paso": "herramienta", "nombre": "resumen_ventas"}] * 10
    chat = chat_bi(sin_fin, {"resumen": "Resumen con lo que se alcanzó a ver.", "recomendaciones": []})
    r = await crear_nodo_bi(deps(bd, chat, max_pasos_bi=3))(estado())
    res = ResultadoBI(**r["resultados"][0])
    assert res.pasos_usados == 3 and res.recomendaciones == [] and res.limitaciones  # sin recomendaciones, lo dice


async def test_bi_responde_con_error_a_un_producto_que_no_existe_y_sigue(bd):
    pasos = [{"paso": "herramienta", "nombre": "tendencia_ventas", "sku": "NO-EXISTE"}, {"paso": "terminar", "motivo": "nada más"}]
    chat = chat_bi(pasos, {"resumen": "No hay datos de ese producto.", "recomendaciones": []})
    r = await crear_nodo_bi(deps(bd, chat))(estado())
    assert "error" not in r
    assert any("el producto NO-EXISTE no existe" in m[1][1] for m in chat.llamadas if "Redacta ahora" in m[1][1])


async def test_bi_rechaza_un_paso_con_argumentos_incompletos(bd):
    pasos = [{"paso": "herramienta", "nombre": "tendencia_ventas"}, {"paso": "terminar", "motivo": "nada más"}]  # falta el sku
    chat = chat_bi(pasos, {"resumen": "Sin datos suficientes para opinar.", "recomendaciones": []})
    r = await crear_nodo_bi(deps(bd, chat))(estado())
    assert "error" not in r  # el contrato rechazó el paso, se le explicó al LLM y el siguiente fue válido


# ================================================================ inventario
async def test_inventario_propone_ordenes_solo_donde_corresponde(bd):
    r = await crear_nodo_inventario(deps(bd))(estado(agentes=("inventario",)))
    res = ResultadoInventario(**r["resultados"][0])
    skus_alerta = [a.estado.sku for a in res.alertas]
    assert set(skus_alerta) == {"BAG-001", "BAG-002", "BAG-004", "BAG-005", "BAG-006", "BAG-007"}
    ordenes = {a["sku"]: a["cantidad"] for a in r["acciones"]}
    assert ordenes["BAG-001"] == 54 and "BAG-005" not in ordenes and "BAG-002" not in ordenes  # la orden en camino cubre BAG-005
    assert skus_alerta[0] in ("BAG-004", "BAG-005", "BAG-006")  # lo agotado va primero
    assert next(a for a in res.alertas if a.estado.sku == "BAG-001").tendencia.tendencia == "sube"
    assert r["tareas_pendientes"] == []


# ================================================================ logística
PARAMS = None


@pytest.fixture
def p(bd):
    import sqlite3
    return cargar_parametros(sqlite3.connect(bd))


def av(**kw):
    base = dict(accion="enviar_aviso", pedido_id="PED-C001", tipo="atraso", intento=1)
    return EnviarAviso(**{**base, **kw})


def test_validar_aviso_acepta_un_texto_correcto_y_rechaza_cada_infraccion(p):
    a = av(nueva_fecha=date(2026, 11, 8), compensacion="reembolso_envio")
    ok = "Hola, tu pedido {numero_pedido} se demora. La nueva fecha es el {nueva_fecha}. Como el atraso supera los dos días, {compensacion}."
    assert validar_aviso(ok, a, p) == []
    casos = {
        "Hola, tu pedido {numero_pedido} llegará el 8 de noviembre. {nueva_fecha} {compensacion}": "dígitos",
        "Hola, tu pedido {numero_pedido} se demora y llegará pronto. {compensacion}": "falta {nueva_fecha}",
        "Hola, tu pedido {numero_pedido} se demora. Llegará el {nueva_fecha}. Te daremos algo.": "falta {compensacion}",
        "Hola, tu pedido se demora. Llegará el {nueva_fecha}. Te daremos {compensacion} por las molestias.": "falta {numero_pedido}",
        "Hola, tu pedido {numero_pedido} se demora. Llegará el {nueva_fecha}. {compensacion} {nombre}": "desconocidos",
        "Tu pedido {numero_pedido} se demora. Llegará el {nueva_fecha}. Te daremos {compensacion} de regalo.": "Hola",
    }
    for texto, fragmento in casos.items():
        assert any(fragmento in e for e in validar_aviso(texto, a, p)), (fragmento, validar_aviso(texto, a, p))


def test_los_marcadores_se_rellenan_con_datos_calculados_por_el_codigo(p):
    a = av(nueva_fecha=date(2026, 11, 8), compensacion="cupon_30")
    t = rellenar("Hola, {numero_pedido} llega el {nueva_fecha}; {compensacion}.", a, p)
    assert t == "Hola, PED-C001 llega el 8 de noviembre de 2026; te dejamos un cupón de 30 % de descuento para tu próxima compra."
    assert fecha_larga(date(2026, 1, 1)) == "1 de enero de 2026"


def test_el_ultimo_mensaje_debe_avisar_de_la_cancelacion(p):
    a = av(tipo="solicitud_direccion", intento=3)
    assert any("cancel" in e for e in validar_aviso("Hola, tu pedido {numero_pedido} necesita tu dirección completa, por favor.", a, p))
    assert "cancelaremos" in aviso_de_plantilla(a, "direccion_erronea", p)


async def test_logistica_gestiona_los_pedidos_mas_atrasados_y_redacta_con_el_llm(bd):
    texto = TextoFalso(aviso_valido)
    r = await crear_nodo_logistica(deps(bd, texto=texto, max_pedidos_logistica=6))(estado(agentes=("logistica",)))
    res = ResultadoLogistica(**r["resultados"][0])
    assert len(res.gestiones) == 6 and res.pedidos_omitidos > 0
    avisos = [a for a in r["acciones"] if a["accion"] == "enviar_aviso"]
    assert avisos and all(a["texto"] and "{" not in a["texto"] for a in avisos)  # ningún marcador sin rellenar
    assert all("PED-" in a["texto"] for a in avisos) and len(texto.llamadas) == len(avisos)
    courier = next(a for a in avisos if a["pedido_id"] == "PED-C001")
    assert "8 de noviembre de 2026" in courier["texto"] and "te reembolsamos el costo del envío" in courier["texto"]


async def test_si_el_llm_no_cumple_las_reglas_el_aviso_lo_arma_el_codigo(bd):
    texto = TextoFalso(lambda m: "Hola, tu pedido llegará el 12/11 sin falta, te lo aseguro 100 %.")  # dígitos y sin marcadores
    r = await crear_nodo_logistica(deps(bd, texto=texto, max_pedidos_logistica=3))(estado(agentes=("logistica",)))
    avisos = [a for a in r["acciones"] if a["accion"] == "enviar_aviso"]
    assert avisos and all("12/11" not in a["texto"] and a["texto"].startswith("Hola, ") for a in avisos)
    assert len(texto.llamadas) == 2 * len(avisos)  # cada aviso: el intento y un reintento con los errores


# ================================================================ redactor
def test_la_validacion_detecta_cifras_e_identificadores_inventados():
    hechos = {"tipo": "resultado", "inventario": {"alertas": [{"sku": "BAG-001", "stock": "18"}]}, "fecha": "8 de noviembre de 2026"}
    assert validar_respuesta("BAG-001 tiene 18 unidades y llega el 8 de noviembre de 2026.", hechos) == []
    sobran = validar_respuesta("BAG-001 tiene 19 unidades y BAG-999 también.", hechos)
    assert any("19" in s for s in sobran) and any("BAG-999" in s for s in sobran)
    assert validar_respuesta("El producto 1.234 se vende.", hechos) != []  # 1.234 no está en los hechos


async def test_el_redactor_acepta_un_texto_valido_y_cae_a_la_plantilla_si_no(bd):
    e = estado_inicial("consulta")
    e["ruta"] = {"decision": "derivar"}
    e["resultados"] = [{"agente": "logistica", "cola": {"en_cola": 18, "capacidad_diaria": 10, "capacidad_restante_hoy": 0, "dias_para_vaciar": 2},
                        "envios": {"total_en_transito": 43, "por_estado": {"en_ruta": 29, "proximo_a_llegar": 14}, "carteras": []},
                        "demanda_vs_capacidad": {"capacidad_diaria": 10, "dias_analizados": 84, "dias_sobre_capacidad": 8, "pedidos_dia_maximo": 24,
                                                 "promedio_pedidos_dia": 7.1, "espera_promedio_dias_normales": 0.5, "espera_promedio_dias_pico": 1.9,
                                                 "entregas_totales": 511, "entregas_con_atraso": 59, "dias_pico": []},
                        "gestiones": [], "pedidos_omitidos": 0, "revision_humana": []}]
    hechos = construir_hechos(e)
    bueno = TextoFalso(lambda m: "Hay dieciocho pedidos en la cola de preparación y el sistema los va a despachar en orden de llegada.")
    assert (await redactar(deps(bd, texto=bueno), hechos))[1] == "llm"
    malo = TextoFalso(lambda m: "Hay 77 pedidos en la cola de preparación y se despachan hoy mismo, sin ninguna duda.")
    texto, origen = await redactar(deps(bd, texto=malo), hechos)
    assert origen == "plantilla" and "18 en la cola" in texto and "77" not in texto and len(malo.llamadas) == 2


async def test_fuera_de_alcance_y_no_entiendo_no_gastan_una_llamada_al_llm(bd):
    texto = TextoFalso(lambda m: pytest.fail("no debía llamarse al LLM"))
    for motivo, frase in (("no es del negocio", "fuera de lo que puedo hacer"), (MOTIVO_SIN_ENTENDER, "No logré entender")):
        e = estado_inicial("clima")
        e["ruta"] = {"decision": "fuera_de_alcance", "motivo": motivo}
        t, origen = await redactar(deps(bd, texto=texto), construir_hechos(e))
        assert frase in t and origen == "plantilla" and "revisar el inventario" in t


def test_los_hechos_distinguen_ejecutadas_rechazadas_y_no_aprobadas():
    e = estado_inicial("consulta")
    e["ruta"] = {"decision": "derivar"}
    e["acciones"] = [{"accion": "crear_orden_compra", "sku": "BAG-001", "cantidad": 54, "motivo": "x y z"},
                     {"accion": "emitir_compensacion", "pedido_id": "PED-C001", "tipo": "cupon_30"},
                     {"accion": "cancelar_con_reembolso", "pedido_id": "PED-C008", "motivo": "direccion"}]
    e["decision_humana"] = {"aprobadas": [0, 1]}
    e["ejecucion"] = [{"accion": "crear_orden_compra", "ok": True, "detalle": "Orden 9 creada"},
                      {"accion": "emitir_compensacion", "ok": False, "detalle": "Rechazada: tipo equivocado"}]
    a = construir_hechos(e)["acciones"]
    assert a["ejecutadas"] == ["Orden 9 creada"] and a["rechazadas_por_las_reglas"] == ["Rechazada: tipo equivocado"]
    assert a["no_aprobadas"] == ["cancelación con reembolso del pedido PED-C008"] and a["propuestas"] == "3"
    assert "No aprobada" in respuesta_determinista({"tipo": "resultado", "acciones": a})


async def test_logistica_entrega_tambien_el_panorama_de_envios_y_de_demanda(bd):
    r = await crear_nodo_logistica(deps(bd, texto=TextoFalso(aviso_valido), max_pedidos_logistica=2))(estado(agentes=("logistica",)))
    res = ResultadoLogistica(**r["resultados"][0])
    assert res.envios.total_en_transito > 0 and res.envios.carteras and res.envios.por_estado
    assert res.demanda_vs_capacidad.dias_sobre_capacidad > 0 and res.demanda_vs_capacidad.dias_pico


def test_el_redactor_recibe_los_envios_y_la_demanda_con_las_cifras_ya_formateadas():
    e = estado_inicial("consulta")
    e["ruta"] = {"decision": "derivar"}
    e["resultados"] = [{"agente": "logistica", "cola": {"en_cola": 18, "capacidad_diaria": 10, "capacidad_restante_hoy": 0, "dias_para_vaciar": 2},
                        "envios": {"total_en_transito": 43, "por_estado": {"en_ruta": 29, "proximo_a_llegar": 14},
                                   "carteras": [{"sku": "BAG-001", "nombre": "Baguette Cherry", "unidades": 13, "pedidos": 13}]},
                        "demanda_vs_capacidad": {"capacidad_diaria": 10, "dias_analizados": 84, "dias_sobre_capacidad": 8, "pedidos_dia_maximo": 24,
                                                 "promedio_pedidos_dia": 7.1, "espera_promedio_dias_normales": 0.54, "espera_promedio_dias_pico": 1.9,
                                                 "entregas_totales": 511, "entregas_con_atraso": 59,
                                                 "dias_pico": [{"fecha": "2026-10-18", "pedidos": 24, "espera_promedio_dias": 0.86}]},
                        "gestiones": [], "pedidos_omitidos": 0, "revision_humana": []}]
    h = construir_hechos(e)["logistica"]
    assert h["envios_en_curso"]["en_transito"] == "43" and h["envios_en_curso"]["carteras_en_transito"][0]["sku"] == "BAG-001"
    assert h["demanda_vs_capacidad"]["espera_dias_pico"] == "1,9" and h["demanda_vs_capacidad"]["dias_pico"][0]["fecha"] == "18 de octubre de 2026"
    assert validar_respuesta("Hay 43 pedidos en tránsito y 8 días sobre la capacidad; el 18 de octubre de 2026 hubo 24 pedidos.", {"logistica": h}) == []
    texto = respuesta_determinista({"tipo": "resultado", "logistica": h})
    assert "43 pedidos en tránsito" in texto and "8 días por sobre ella" in texto and "59 de 511" in texto


async def test_con_el_tope_por_defecto_logistica_muestra_las_cinco_causas_y_prioriza_por_atraso(bd):
    r = await crear_nodo_logistica(deps(bd, texto=TextoFalso(aviso_valido)))(estado(agentes=("logistica",)))
    res = ResultadoLogistica(**r["resultados"][0])
    assert len(res.gestiones) == 10 and res.pedidos_omitidos == 12
    assert {g.causa for g in res.gestiones} == {"courier", "falta_stock", "direccion_erronea", "cliente_sin_datos", "capacidad"}
    assert [g.pedido_id for g in res.gestiones][:3] == ["PED-C008", "PED-C002", "PED-C001"]  # 4, 3 y 2 días de atraso
