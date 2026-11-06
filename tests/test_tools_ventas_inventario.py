"""Herramientas de ventas e inventario, contra los casos sembrados de la base simulada."""
import sqlite3
from datetime import timedelta

import pytest

from app.tools import inventory, sales

NORMALES = [f"BAG-{n:03d}" for n in range(9, 31)]


# ---------------------------------------------------------------- ventas
def test_resumen_de_la_ultima_semana_coincide_con_una_consulta_independiente(con_ro, params, bd_base):
    r = sales.resumen_ventas(con_ro, params, dias=7)
    # segunda implementación, sobre las tablas originales y sin usar la vista `ventas`
    u, ing = sqlite3.connect(bd_base).execute(
        """SELECT SUM(l.cantidad), SUM(l.cantidad * l.precio_unitario) FROM lineas_pedido l JOIN pedidos p USING (pedido_id)
           WHERE p.estado <> 'cancelado' AND p.fecha_ingreso >= ? AND p.fecha_ingreso < ?""",
        (f"{params.hoy - timedelta(days=7)} 00:00:00", f"{params.hoy} 00:00:00")).fetchone()
    assert (r.unidades, r.ingresos) == (u, ing) and r.unidades > 0
    assert (r.desde, r.hasta) == (params.hoy - timedelta(days=7), params.hoy - timedelta(days=1))


def test_el_falso_ganador_se_corrige_con_ventas_por_unidad_de_stock(con_ro, params):
    v = {x.sku: x for x in sales.ventas_por_producto(con_ro, params)}
    assert (v["BAG-003"].unidades, v["BAG-003"].ventas_por_unidad_de_stock) == (60, 0.3)
    assert (v["BAG-004"].unidades, v["BAG-004"].ventas_por_unidad_de_stock) == (12, 1.0)
    assert v["BAG-003"].clasificacion == "ganador" and v["BAG-003"].evidencia_suficiente
    assert v["BAG-004"].clasificacion == "prospecto" and not v["BAG-004"].evidencia_suficiente  # 12 ventas y menos de 4 semanas
    assert v["BAG-004"].margen_pct > v["BAG-003"].margen_pct  # alto margen y pocas ventas, frente a bajo margen y mucho volumen


def test_el_resultado_viene_ordenado_y_acotado(con_ro, params):
    todos = sales.ventas_por_producto(con_ro, params)
    razones = [x.ventas_por_unidad_de_stock for x in todos]
    assert len(todos) == 30 and razones == sorted(razones, reverse=True)
    assert [x.sku for x in sales.ventas_por_producto(con_ro, params, skus=["BAG-001"])] == ["BAG-001"]
    assert sum(x.unidades for x in sales.ventas_por_producto(con_ro, params, dias=7)) == sales.resumen_ventas(con_ro, params, 7).unidades


def test_la_tendencia_detecta_un_salto_que_se_mantiene(con_ro, params):
    t1 = sales.tendencia_semanal(con_ro, params, "BAG-001")
    assert [s.unidades for s in t1.semanas] == [7, 7, 21, 21] and t1.tendencia == "sube"  # triplicó hace 2 semanas
    t2 = sales.tendencia_semanal(con_ro, params, "BAG-002")
    assert t2.tendencia == "baja" and t2.semanas[-1].unidades == 0  # dejó de venderse


# ---------------------------------------------------------------- inventario
def estados(con, p):
    return {e.sku: e for e in inventory.estado_inventario(con, p)}


def con_alerta(est, alerta):
    return {sku for sku, e in est.items() if alerta in e.alertas}


def test_las_alertas_son_exactamente_las_de_los_casos_sembrados(con_ro, params):
    est = estados(con_ro, params)
    assert con_alerta(est, "quiebre_proximo") == {"BAG-001", "BAG-007"}
    assert con_alerta(est, "estancado") == {"BAG-002"}
    assert con_alerta(est, "agotado") == {"BAG-004", "BAG-005", "BAG-006"}
    assert {"BAG-005", "BAG-006", "BAG-007"} <= con_alerta(est, "stock_insuficiente_para_pedidos")


def test_ningun_producto_sano_da_una_falsa_alarma(con_ro, params):
    est = estados(con_ro, params)
    for sku in NORMALES + ["BAG-003", "BAG-008"]:
        assert est[sku].alertas == [], (sku, est[sku].alertas)


def test_los_numeros_de_los_casos(con_ro, params):
    est = estados(con_ro, params)
    b1 = est["BAG-001"]
    assert (b1.stock_actual, b1.ventas_diarias, b1.cobertura_dias, b1.punto_reorden) == (18, 3.0, 6.0, 36.0)  # 3 × 10 + 6
    assert est["BAG-002"].dias_sin_ventas == 21 and est["BAG-002"].cobertura_dias is None
    assert (est["BAG-005"].en_camino, est["BAG-005"].demanda_no_atendida) == (25, 3)
    assert (est["BAG-006"].en_camino, est["BAG-006"].demanda_no_atendida) == (0, 2)
    assert (est["BAG-007"].stock_actual, est["BAG-007"].unidades_por_preparar) == (3, 8)


def test_cantidad_sugerida_de_la_orden_de_compra(con_ro, params):
    est = estados(con_ro, params)
    s1 = inventory.sugerir_orden(est["BAG-001"], params)
    assert s1.cantidad == 3 * (10 + 14) - 18 == 54  # ventas diarias × (lead time + cobertura objetivo) − stock
    assert inventory.sugerir_orden(est["BAG-005"], params) is None  # la orden en camino (25) ya cubre la necesidad
    assert inventory.sugerir_orden(est["BAG-002"], params) is None  # estancado: no se repone
    assert inventory.sugerir_orden(est["BAG-007"], params).cantidad == 11
    assert all(inventory.sugerir_orden(est[s], params) is None for s in NORMALES)


def test_la_demanda_no_atendida_suma_a_la_cantidad(con_ro, params):
    e = estados(con_ro, params)["BAG-006"]
    base = e.ventas_diarias * (params.lead_time + params.cobertura_objetivo) - e.stock_actual - e.en_camino
    assert inventory.sugerir_orden(e, params).cantidad == -(-(base + e.demanda_no_atendida) // 1)  # techo
    assert inventory.sugerir_orden(e, params).cantidad > -(-base // 1)  # con la demanda no atendida pide más
