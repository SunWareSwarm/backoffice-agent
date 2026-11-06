"""Logística y matriz de gestión, caso por caso contra la base simulada (reloj fijo: 2026-11-03)."""
import math
import sqlite3
from datetime import date, timedelta

import pytest

from app.tools import orders
from app.tools.comun import cargar_parametros

HOY = date(2026, 11, 3)
CASOS = [f"PED-C{n:03d}" for n in range(1, 12)]  # C012 está cancelado y C013-C016 no tienen problema


@pytest.fixture(scope="module")
def pedidos(con_ro, params):
    return {x.pedido_id: x for x in orders.pedidos_con_problema(con_ro, params)}


@pytest.fixture(scope="module")
def gestion(con_ro, params, pedidos):
    return {pid: orders.decidir_gestion(con_ro, params, x) for pid, x in pedidos.items()}


def resumen(g):
    """Las acciones de una gestión como tuplas fáciles de comparar."""
    out = []
    for a in g.acciones:
        if a.accion == "enviar_aviso":
            out.append(("aviso", a.tipo, a.intento, a.ofrece_reembolso))
        elif a.accion == "emitir_compensacion":
            out.append(("compensar", a.tipo))
        else:
            out.append(("cancelar", a.motivo))
    return out


# ---------------------------------------------------------------- qué pedidos tienen problema
def test_los_casos_a_mano_con_problema_son_estos(pedidos):
    assert {p for p in pedidos if p.startswith("PED-C")} == set(CASOS)
    assert "PED-C012" not in pedidos  # cancelado: ya no hay nada que gestionar
    assert not {"PED-C013", "PED-C014", "PED-C015", "PED-C016"} & set(pedidos)  # por preparar, sin problema


def test_dias_de_atraso_y_vencimiento(pedidos):
    assert (pedidos["PED-C001"].dias_atraso, pedidos["PED-C002"].dias_atraso, pedidos["PED-C008"].dias_atraso) == (2, 3, 4)
    assert pedidos["PED-C004"].vencido is False and pedidos["PED-C004"].dias_atraso == 0  # causa registrada, aún no vence
    assert all(x.vencido or x.causa for x in pedidos.values())


def test_los_datos_de_cada_pedido_se_leen_bien(pedidos):
    c8, c9, c3 = pedidos["PED-C008"], pedidos["PED-C009"], pedidos["PED-C003"]
    assert c8.avisos_solicitud_direccion == 3 and c8.respuestas == []
    assert c9.respuestas == ["envia_direccion"] and c3.respuestas == ["acepta_espera"] and c3.avisos_atraso == 1
    assert pedidos["PED-C005"].lineas == [("BAG-006", 1)] and pedidos["PED-C004"].lineas == [("BAG-005", 2)]
    assert pedidos["PED-C001"].envio_pagado is True and pedidos["PED-C002"].envio_pagado is False


# ---------------------------------------------------------------- nueva fecha
@pytest.mark.parametrize("pid,fecha,condicional", [
    ("PED-C001", date(2026, 11, 8), False),   # courier, regiones: hoy + 5
    ("PED-C002", date(2026, 11, 5), False),   # courier, RM: hoy + 2
    ("PED-C003", date(2026, 11, 11), False),  # stock: llega la orden el 7/11 + 2 + plazo RM (2)
    ("PED-C004", date(2026, 11, 14), False),  # stock: 7/11 + 2 + plazo regiones (5)
    ("PED-C005", None, False),                # stock sin orden en camino: no hay fecha, se ofrece reembolso
    ("PED-C006", None, False),
    ("PED-C007", None, True),                 # dirección sin respuesta: la fecha depende del cliente
    ("PED-C008", None, True),
    ("PED-C009", date(2026, 11, 7), False),   # respondió hoy 08:00 (antes del corte): hoy + 2 + plazo RM (2)
    ("PED-C010", None, True),
    ("PED-C011", None, True),
])
def test_nueva_fecha_segun_la_causa(con_ro, params, pedidos, pid, fecha, condicional):
    nf = orders.nueva_fecha(con_ro, params, pedidos[pid])
    assert (nf.fecha, nf.condicional) == (fecha, condicional) and nf.regla


def test_las_fechas_de_capacidad_respetan_la_capacidad_diaria(bd):
    """Escenario armado a propósito: todos los pedidos que siguen en la cola quedan atrasados por capacidad."""
    con = sqlite3.connect(bd)
    con.row_factory = sqlite3.Row
    p = cargar_parametros(con)
    cola = orders._cola(con, p)
    restante = orders._restante_hoy(con, p)
    assert len(cola) > restante, "para que la prueba valga, la cola debe ser más larga que la capacidad que queda hoy"
    con.executemany("UPDATE pedidos SET causa_atraso = 'capacidad' WHERE pedido_id = ?", [(pid,) for pid in cola])
    con.commit()
    por_id = {x.pedido_id: x for x in orders.pedidos_con_problema(con, p)}

    # cuántos pedidos se preparan cada día, deducido de las fechas que entrega la herramienta
    por_dia: dict = {}
    for pid in cola:
        x = por_id[pid]
        fecha = orders.nueva_fecha(con, p, x).fecha
        dia_prep = fecha - timedelta(days=2 + p.plazo(x.region))
        por_dia[dia_prep] = por_dia.get(dia_prep, 0) + 1
    assert por_dia[p.hoy] <= restante if p.hoy in por_dia else True
    assert all(n <= p.capacidad for dia, n in por_dia.items() if dia != p.hoy)  # ningún día se prepara más que la capacidad
    assert sum(por_dia.values()) == len(cola) and min(por_dia) >= p.hoy
    # el último de la cola se prepara en el día que dice cola_de_preparacion
    c = orders.cola_de_preparacion(con, p)
    assert max(por_dia) == p.hoy + timedelta(days=c.dias_para_vaciar)
    assert (c.capacidad_diaria, c.capacidad_restante_hoy, c.en_cola) == (10, restante, len(cola))
    assert c.dias_para_vaciar == math.ceil((c.en_cola - restante) / 10)
    # a igual región, la fecha nunca baja al avanzar en la cola
    for region in ("RM", "regiones"):
        fechas = [orders.nueva_fecha(con, p, por_id[pid]).fecha for pid in cola if por_id[pid].region == region]
        assert fechas == sorted(fechas)


# ---------------------------------------------------------------- la matriz de gestión
@pytest.mark.parametrize("pid,esperado", [
    ("PED-C001", [("aviso", "atraso", 1, False), ("compensar", "reembolso_envio")]),   # pagó el envío: se reembolsa
    ("PED-C002", [("aviso", "atraso", 1, False), ("compensar", "cupon_30")]),          # no lo pagó: cupón de 30 %
    ("PED-C003", []),                                                                  # aceptó esperar y lleva 1 día: nada que hacer
    ("PED-C004", [("aviso", "atraso", 1, False)]),                                     # con orden en camino: informar la fecha
    ("PED-C005", [("aviso", "atraso", 1, True)]),                                      # sin orden: ofrecer reembolso
    ("PED-C006", [("aviso", "atraso", 1, True)]),
    ("PED-C007", [("aviso", "solicitud_direccion", 2, False)]),                        # ya hubo 1: toca el 2.º de 3
    ("PED-C008", [("cancelar", "direccion")]),                                         # 3 sin respuesta: cancelar y reembolsar
    ("PED-C009", []),                                                                  # respondió: se reprograma
    ("PED-C010", [("aviso", "solicitud_datos", 3, False)]),                            # ya hubo 2: toca el 3.º y último
    ("PED-C011", [("cancelar", "datos")]),
])
def test_la_matriz_de_gestion_resuelve_cada_caso(gestion, pid, esperado):
    assert resumen(gestion[pid]) == esperado
    assert gestion[pid].explicacion and not gestion[pid].requiere_revision


def test_el_aviso_de_courier_trae_la_compensacion_y_la_fecha(gestion):
    a = gestion["PED-C001"].acciones[0]
    assert (a.compensacion, a.nueva_fecha) == ("reembolso_envio", date(2026, 11, 8)) and a.texto is None


def test_la_compensacion_solo_corresponde_donde_las_reglas_lo_dicen(pedidos, gestion, params):
    for pid, g in gestion.items():
        comp = [a for a in g.acciones if a.accion == "emitir_compensacion"]
        if not comp:
            continue
        x = pedidos[pid]
        assert x.dias_atraso >= params.compensacion_desde, pid
        assert x.causa in ("courier", "capacidad", "falta_stock"), pid  # nunca en dirección ni datos
        assert comp[0].tipo == ("reembolso_envio" if x.envio_pagado else "cupon_30"), pid


def test_nunca_se_cancela_sin_haber_agotado_los_mensajes(pedidos, gestion):
    for pid, g in gestion.items():
        for a in g.acciones:
            if a.accion == "cancelar_con_reembolso" and a.motivo in ("direccion", "datos"):
                enviados = pedidos[pid].avisos_solicitud_direccion if a.motivo == "direccion" else pedidos[pid].avisos_solicitud_datos
                assert enviados >= 3, pid


def test_un_pedido_sin_causa_pasa_a_revision_humana_y_no_se_inventa_nada(bd):
    con = sqlite3.connect(bd)
    con.row_factory = sqlite3.Row
    con.execute("UPDATE pedidos SET causa_atraso = NULL WHERE pedido_id = 'PED-C001'")
    con.commit()
    p = cargar_parametros(con)
    g = orders.decidir_gestion(con, p, orders.pedido_con_problema(con, p, "PED-C001"))
    assert g.requiere_revision and g.acciones == [] and g.causa is None and g.nueva_fecha.fecha is None


def test_si_ya_se_aviso_no_se_repite_el_aviso(bd):
    con = sqlite3.connect(bd)
    con.row_factory = sqlite3.Row
    con.execute("INSERT INTO bandeja_avisos (pedido_id, tipo, texto, estado) VALUES ('PED-C001', 'atraso', 'aviso previo', 'enviado')")
    con.commit()
    p = cargar_parametros(con)
    g = orders.decidir_gestion(con, p, orders.pedido_con_problema(con, p, "PED-C001"))
    assert resumen(g) == [("compensar", "reembolso_envio")]  # el aviso ya salió; la compensación sigue pendiente


# ---------------------------------------------------------------- panorama: envíos en curso y demanda frente a capacidad
def test_los_envios_en_curso_coinciden_con_una_consulta_independiente(con_ro, params, bd_base):
    e = orders.envios_en_curso(con_ro, params, max_carteras=100)
    con = sqlite3.connect(bd_base)
    por_estado = dict(con.execute("SELECT estado, COUNT(*) FROM pedidos WHERE estado NOT IN ('entregado', 'cancelado') GROUP BY estado"))
    assert e.por_estado == por_estado and list(e.por_estado.values()) == sorted(e.por_estado.values(), reverse=True)
    transito = ("espera_despacho", "en_ruta", "proximo_a_llegar")
    assert e.total_en_transito == sum(por_estado.get(k, 0) for k in transito) > 0
    unidades = con.execute("SELECT SUM(l.cantidad) FROM lineas_pedido l JOIN pedidos p ON p.pedido_id = l.pedido_id "
                           "WHERE p.estado IN ('espera_despacho','en_ruta','proximo_a_llegar')").fetchone()[0]
    assert sum(c.unidades for c in e.carteras) == unidades  # cada unidad en tránsito está en alguna cartera
    assert [c.unidades for c in e.carteras] == sorted((c.unidades for c in e.carteras), reverse=True)
    assert len(orders.envios_en_curso(con_ro, params, max_carteras=3).carteras) == 3


def test_la_demanda_frente_a_la_capacidad_muestra_el_cuello_de_botella_sembrado(con_ro, params, bd_base):
    d = orders.demanda_vs_capacidad(con_ro, params)
    con = sqlite3.connect(bd_base)
    por_dia = con.execute("SELECT substr(fecha_ingreso, 1, 10), COUNT(*) FROM pedidos WHERE estado <> 'cancelado' "
                          "AND fecha_ingreso >= '2026-08-11 00:00:00' AND fecha_ingreso < '2026-11-03 00:00:00' GROUP BY 1").fetchall()
    assert d.dias_analizados == len(por_dia) and d.dias_sobre_capacidad == sum(1 for _, n in por_dia if n > 10) > 0
    assert d.pedidos_dia_maximo == max(n for _, n in por_dia) and d.promedio_pedidos_dia == round(sum(n for _, n in por_dia) / len(por_dia), 1)
    assert d.espera_promedio_dias_pico > d.espera_promedio_dias_normales  # en los días pico, los pedidos esperan más
    assert [x.pedidos for x in d.dias_pico] == sorted((x.pedidos for x in d.dias_pico), reverse=True) and len(d.dias_pico) == 3
    assert all(x.fecha >= date(2026, 10, 14) for x in d.dias_pico)  # los picos grandes sembrados están en octubre
    tarde = sum(1 for p, e in con.execute("SELECT pe.fecha_prometida, h.fecha_hora FROM pedidos pe JOIN historial_estados h "
                                          "ON h.pedido_id = pe.pedido_id AND h.estado = 'entregado' WHERE pe.fecha_ingreso >= '2026-08-11 00:00:00' "
                                          "AND pe.fecha_ingreso < '2026-11-03 00:00:00'") if e[:10] > p[:10])
    assert d.entregas_con_atraso == tarde > 0 and d.entregas_totales > d.entregas_con_atraso


def test_acotar_el_periodo_reduce_lo_que_se_analiza(con_ro, params):
    todo, mes = orders.demanda_vs_capacidad(con_ro, params), orders.demanda_vs_capacidad(con_ro, params, dias=28)
    assert mes.dias_analizados < todo.dias_analizados and mes.entregas_totales < todo.entregas_totales
