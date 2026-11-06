"""Pruebas de la base simulada: cada caso sembrado debe estar, y estar bien.

Las consultas SQL de aquí son una segunda implementación, independiente del generador: si el
generador y la consulta no coinciden, una de las dos está mal.
"""
import sqlite3
import statistics
from datetime import date, datetime, timedelta

import pytest

from app.db.connection import conectar
from app.db.seed import generar

HOY = date(2026, 11, 3)
HOY_S = HOY.isoformat()
SEMILLAS = (42, 7, 123, 5)


@pytest.fixture(scope="module")
def ruta(tmp_path_factory):
    r = tmp_path_factory.mktemp("bd") / "tienda.db"
    generar(r, HOY, 42)
    return r


@pytest.fixture
def con(ruta):
    c = conectar(ruta)
    yield c
    c.close()


def dias(a: str) -> date:
    return date.fromisoformat(a[:10])


def uno(con, sql, *args):
    return con.execute(sql, args).fetchone()[0]


# ---------------------------------------------------------------- estructura y volumen
def test_volumen_y_catalogo(con):
    assert uno(con, "SELECT COUNT(*) FROM productos") == 30
    assert uno(con, "SELECT COUNT(*) FROM proveedores") == 4
    assert 500 <= uno(con, "SELECT COUNT(*) FROM pedidos") <= 800
    assert uno(con, "SELECT MIN(precio) FROM productos") >= 15000 and uno(con, "SELECT MAX(precio) FROM productos") <= 40000
    assert uno(con, "SELECT COUNT(*) FROM inventario") == 30  # todo producto tiene su inventario


def test_los_tamanos_tienen_variedad(con):
    assert {r[0] for r in con.execute("SELECT DISTINCT tamano FROM productos")} == {"mini", "mediano", "grande"}


def test_la_base_no_tiene_referencias_rotas(con):
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_historial_de_12_semanas(con):
    primera = dias(con.execute("SELECT MIN(fecha) FROM ventas").fetchone()[0])
    assert 80 <= (HOY - primera).days <= 84
    assert uno(con, "SELECT COUNT(*) FROM ventas WHERE fecha >= ?", f"{HOY_S} 12:00:01") == 0  # nada en el futuro


def test_los_parametros_decididos_estan_en_la_base(con):
    p = dict(con.execute("SELECT clave, valor FROM parametros").fetchall())
    assert p["fecha_referencia"] == HOY_S
    esperados = {"lead_time_proveedor_dias": "10", "hora_corte": "15:00", "capacidad_diaria": "10",
                 "plazo_entrega_rm_dias": "2", "plazo_entrega_regiones_dias": "5", "compensacion_desde_dias": "2",
                 "cupon_porcentaje": "30", "espera_stock_dias": "3", "max_mensajes_datos": "3",
                 "stock_estancado_dias": "14", "minimo_ventas_ganador": "15", "historial_minimo_semanas": "4"}
    assert esperados.items() <= p.items()


def test_toda_transaccion_tiene_fecha_prometida(con):
    assert uno(con, "SELECT COUNT(*) FROM pedidos WHERE fecha_prometida IS NULL OR fecha_prometida = ''") == 0
    with pytest.raises(sqlite3.IntegrityError):
        c = sqlite3.connect(":memory:")
        c.executescript(open("app/db/schema.sql", encoding="utf-8").read())
        c.execute("INSERT INTO pedidos VALUES ('X','2026-01-01 10:00:00','RM','ingresado',1,NULL,NULL)")


# ---------------------------------------------------------------- caso 1: quiebre próximo
def cobertura(con):
    """Días de cobertura = stock ÷ ventas diarias de las últimas 2 semanas (ventana: hoy-14 a ayer)."""
    filas = con.execute("""
        SELECT i.sku, i.stock_actual AS stock,
               COALESCE((SELECT SUM(cantidad) FROM ventas v WHERE v.sku = i.sku AND v.fecha >= ? AND v.fecha < ?), 0) AS u
        FROM inventario i""", (f"{HOY - timedelta(days=14)} 00:00:00", f"{HOY_S} 00:00:00")).fetchall()
    return {f["sku"]: (f["stock"], f["u"] / 14) for f in filas}


def test_bag001_esta_por_quebrarse(con):
    stock, tasa = cobertura(con)["BAG-001"]
    assert (stock, tasa) == (18, 3.0)
    assert stock / tasa == 6 < 10  # menos que el lead time del proveedor


def test_solo_los_productos_de_los_casos_estan_en_riesgo_de_quiebre(con):
    riesgo = {sku for sku, (stock, tasa) in cobertura(con).items() if stock > 0 and tasa > 0 and stock / tasa < 10}
    # BAG-001 por la tendencia (pregunta 1) y BAG-007 por sus pedidos por preparar (pregunta 5).
    # Ningún producto sano da una falsa alarma.
    assert riesgo == {"BAG-001", "BAG-007"}
    assert round(cobertura(con)["BAG-007"][0] / cobertura(con)["BAG-007"][1], 1) == 5.2


# ---------------------------------------------------------------- caso 2: stock estancado
def test_bag002_esta_estancado_y_es_el_unico(con):
    filas = con.execute("""SELECT i.sku, MAX(v.fecha) AS ultima FROM inventario i
                           JOIN ventas v ON v.sku = i.sku WHERE i.stock_actual > 0 GROUP BY i.sku""").fetchall()
    estancados = {f["sku"]: (HOY - dias(f["ultima"])).days for f in filas if (HOY - dias(f["ultima"])).days >= 14}
    assert estancados == {"BAG-002": 21}
    assert uno(con, "SELECT stock_actual FROM inventario WHERE sku = 'BAG-002'") == 40


# ---------------------------------------------------------------- caso 3: falso ganador
def ratio(con, sku):
    ventas = uno(con, "SELECT COALESCE(SUM(cantidad), 0) FROM ventas WHERE sku = ?", sku)
    stock = uno(con, "SELECT stock_actual FROM inventario WHERE sku = ?", sku)
    return ventas, ventas / (ventas + stock)  # ventas por unidad de stock disponible


def test_el_producto_de_mucho_volumen_no_es_el_mejor_por_unidad_de_stock(con):
    v3, r3 = ratio(con, "BAG-003")
    v4, r4 = ratio(con, "BAG-004")
    assert v3 == 60 and round(r3, 2) == 0.30
    assert v4 == 12 and r4 == 1.0
    assert r4 > r3  # corrige el sesgo de disponibilidad


def test_bag004_es_prospecto_y_no_ganador(con):
    minimo = int(uno(con, "SELECT valor FROM parametros WHERE clave = 'minimo_ventas_ganador'"))
    assert ratio(con, "BAG-004")[0] < minimo <= ratio(con, "BAG-003")[0]


def test_bag004_tiene_menos_de_4_semanas_de_historial(con):
    primera = dias(uno(con, "SELECT MIN(fecha) FROM ventas WHERE sku = 'BAG-004'"))
    assert (HOY - primera).days < 28
    assert (HOY - dias(uno(con, "SELECT MIN(fecha) FROM ventas WHERE sku = 'BAG-003'"))).days >= 28


def test_las_ventas_no_incluyen_pedidos_cancelados(con):
    en_lineas = uno(con, "SELECT COUNT(*) FROM lineas_pedido l JOIN pedidos p USING (pedido_id) WHERE p.estado = 'cancelado'")
    assert en_lineas > 0
    assert uno(con, "SELECT COUNT(*) FROM ventas") == uno(con, "SELECT COUNT(*) FROM lineas_pedido") - en_lineas


# ---------------------------------------------------------------- caso 4: atrasos con cada causa
def atrasados(con):
    return con.execute("""SELECT pedido_id, causa_atraso, envio_pagado, fecha_prometida, estado FROM pedidos
                          WHERE estado NOT IN ('entregado', 'cancelado') AND fecha_prometida < ?""", (HOY_S,)).fetchall()


def test_hay_atrasos_de_las_cinco_causas(con):
    assert {a["causa_atraso"] for a in atrasados(con)} == {
        "courier", "falta_stock", "direccion_erronea", "cliente_sin_datos", "capacidad"}


def test_los_atrasos_de_los_casos_a_mano_son_exactamente_estos(con):
    manuales = {a["pedido_id"] for a in atrasados(con) if a["pedido_id"].startswith("PED-C")}
    assert manuales == {"PED-C001", "PED-C002", "PED-C003", "PED-C005", "PED-C007", "PED-C008", "PED-C009",
                        "PED-C010", "PED-C011"}


def test_dos_casos_de_courier_cubren_las_dos_compensaciones(con):
    c1 = con.execute("SELECT * FROM pedidos WHERE pedido_id = 'PED-C001'").fetchone()
    c2 = con.execute("SELECT * FROM pedidos WHERE pedido_id = 'PED-C002'").fetchone()
    assert (HOY - dias(c1["fecha_prometida"])).days == 2 and c1["envio_pagado"] == 1  # reembolso del envío
    assert (HOY - dias(c2["fecha_prometida"])).days == 3 and c2["envio_pagado"] == 0  # cupón de 30 %
    assert c1["region_destino"] == "regiones" and c2["region_destino"] == "RM"


def test_hay_atrasos_actuales_por_capacidad(con):
    assert sum(1 for a in atrasados(con) if a["causa_atraso"] == "capacidad") >= 1


# ---------------------------------------------------------------- caso 5: falta de stock
def test_falta_de_stock_con_y_sin_orden_en_camino(con):
    for sku in ("BAG-005", "BAG-006"):
        assert uno(con, "SELECT stock_actual FROM inventario WHERE sku = ?", sku) == 0
    assert uno(con, "SELECT COUNT(*) FROM ordenes_compra WHERE sku = 'BAG-005' AND estado = 'en_camino'") == 1
    assert uno(con, "SELECT COUNT(*) FROM ordenes_compra WHERE sku = 'BAG-006' AND estado IN ('creada','en_camino')") == 0
    oc = con.execute("SELECT * FROM ordenes_compra WHERE sku = 'BAG-005' AND estado = 'en_camino'").fetchone()
    assert dias(oc["fecha_entrega_esperada"]) == dias(oc["creada_en"]) + timedelta(days=10)


def test_la_demanda_no_atendida_por_falta_de_stock_se_puede_contar(con):
    q = """SELECT SUM(l.cantidad) FROM lineas_pedido l JOIN pedidos p USING (pedido_id)
           WHERE p.causa_atraso = 'falta_stock' AND l.sku = ?"""
    assert (uno(con, q, "BAG-005"), uno(con, q, "BAG-006")) == (3, 2)


# ---------------------------------------------------------------- caso 6 y 7: avisos y respuestas
def test_avisos_y_respuestas_de_los_casos(con):
    def avisos(pid, tipo):
        return uno(con, "SELECT COUNT(*) FROM bandeja_avisos WHERE pedido_id = ? AND tipo = ?", pid, tipo)

    def respuestas(pid):
        return [r[0] for r in con.execute("SELECT tipo FROM respuestas_cliente WHERE pedido_id = ?", (pid,))]

    assert avisos("PED-C007", "solicitud_direccion") == 1
    assert avisos("PED-C008", "solicitud_direccion") == 3 and respuestas("PED-C008") == []  # 3 sin respuesta: cancelar
    assert respuestas("PED-C009") == ["envia_direccion"]
    assert avisos("PED-C010", "solicitud_datos") == 2
    assert avisos("PED-C011", "solicitud_datos") == 3 and respuestas("PED-C011") == []
    assert respuestas("PED-C003") == ["acepta_espera"]  # el cliente decidió esperar el stock


def test_pedido_cancelado_con_su_reembolso(con):
    assert uno(con, "SELECT estado FROM pedidos WHERE pedido_id = 'PED-C012'") == "cancelado"
    assert uno(con, "SELECT motivo FROM reembolsos WHERE pedido_id = 'PED-C012'") == "datos"
    assert uno(con, "SELECT COUNT(*) FROM bandeja_avisos WHERE pedido_id = 'PED-C012'") == 3


def test_hay_una_entrega_fallida(con):
    assert uno(con, "SELECT COUNT(*) FROM pedidos WHERE estado = 'entrega_fallida'") == 3  # C007, C008, C009


# ---------------------------------------------------------------- caso 8: stock insuficiente
def test_el_stock_no_alcanza_para_los_pedidos_por_preparar(con):
    stock = uno(con, "SELECT stock_actual FROM inventario WHERE sku = 'BAG-007'")
    por_preparar = uno(con, """SELECT SUM(l.cantidad) FROM lineas_pedido l JOIN pedidos p USING (pedido_id)
        WHERE l.sku = 'BAG-007' AND p.estado IN ('solicitado','ingresado','en_preparacion','espera_despacho')""")
    assert (stock, por_preparar) == (3, 8)


# ---------------------------------------------------------------- caso 9: pico de demanda y cuello de botella
def test_el_pico_de_demanda_se_ve_en_el_historial_de_estados(con):
    filas = con.execute("""
        SELECT substr(p.fecha_ingreso, 1, 10) AS dia, COUNT(*) AS pedidos,
               AVG(julianday(h2.fecha_hora) - julianday(h1.fecha_hora)) AS espera
        FROM pedidos p
        JOIN historial_estados h1 ON h1.pedido_id = p.pedido_id AND h1.estado = 'ingresado'
        JOIN historial_estados h2 ON h2.pedido_id = p.pedido_id AND h2.estado = 'en_preparacion'
        WHERE p.estado <> 'cancelado' GROUP BY dia""").fetchall()
    assert max(f["pedidos"] for f in filas) >= 16 > 10  # más pedidos por día que la capacidad
    esperas = [f["espera"] for f in filas]
    assert max(esperas) >= 2.0  # el cuello de botella: pedidos que esperan días para empezar a prepararse
    assert statistics.median(esperas) <= 1.0  # en un día normal casi no hay espera


def test_hay_compensaciones_historicas_coherentes_con_la_regla(con):
    filas = con.execute("""SELECT c.tipo, p.envio_pagado FROM compensaciones c JOIN pedidos p USING (pedido_id)""").fetchall()
    assert len(filas) >= 1
    assert all((f["tipo"] == "reembolso_envio") == bool(f["envio_pagado"]) for f in filas)


# ---------------------------------------------------------------- seguridad y reproducibilidad
def test_la_conexion_de_lectura_rechaza_escrituras(ruta):
    c = conectar(ruta)
    with pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO parametros VALUES ('x', 'y')")
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM pedidos")
    c.close()


def test_la_base_rechaza_estados_invalidos(ruta, tmp_path):
    copia = tmp_path / "copia.db"
    generar(copia, HOY, 1)
    c = conectar(copia, solo_lectura=False)
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("UPDATE pedidos SET estado = 'volando' WHERE pedido_id = 'PED-C001'")
    with pytest.raises(sqlite3.IntegrityError):
        c.execute("INSERT INTO productos VALUES ('X','x','x','mini',NULL,NULL,NULL,5000,1,1)")  # precio fuera de rango
    c.close()


def volcado(ruta):
    c = sqlite3.connect(ruta)
    texto = "\n".join(c.iterdump())
    c.close()
    return texto


def test_misma_semilla_misma_base_y_otra_semilla_otra_base(tmp_path):
    a, b, c = tmp_path / "a.db", tmp_path / "b.db", tmp_path / "c.db"
    generar(a, HOY, 42), generar(b, HOY, 42), generar(c, HOY, 7)
    assert volcado(a) == volcado(b)
    assert volcado(a) != volcado(c)


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_los_casos_sembrados_no_dependen_de_la_semilla(tmp_path, semilla):
    r = tmp_path / f"s{semilla}.db"
    generar(r, HOY, semilla)
    con = conectar(r)
    stock, tasa = cobertura(con)["BAG-001"]
    assert stock / tasa == 6
    riesgo = {s for s, (st, t) in cobertura(con).items() if st > 0 and t > 0 and st / t < 10}
    assert riesgo == {"BAG-001", "BAG-007"}
    assert ratio(con, "BAG-004")[0] == 12 and ratio(con, "BAG-003")[0] == 60
    causas = {a["causa_atraso"] for a in atrasados(con)}
    assert causas == {"courier", "falta_stock", "direccion_erronea", "cliente_sin_datos", "capacidad"}
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()
