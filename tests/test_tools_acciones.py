"""Acciones simuladas: solo se ejecuta lo que las reglas recomiendan hoy, de forma atómica y sin duplicar."""
import sqlite3

import pytest

from app.db.connection import conectar
from app.schemas import CancelarConReembolso, CrearOrdenCompra, EmitirCompensacion, EnviarAviso
from app.tools.actions import ejecutar_accion, ejecutar_lote
from app.tools.comun import cargar_parametros


@pytest.fixture
def w(bd):
    """Conexión de escritura sobre una copia de la base, con los parámetros ya cargados."""
    c = conectar(bd, solo_lectura=False)
    yield c, cargar_parametros(c)
    c.close()


def orden(sku="BAG-001", cantidad=54):
    return CrearOrdenCompra(accion="crear_orden_compra", sku=sku, cantidad=cantidad, motivo="quiebre próximo")


def aviso(pid, tipo, intento, texto="Hola, necesitamos tu dato."):
    return EnviarAviso(accion="enviar_aviso", pedido_id=pid, tipo=tipo, intento=intento, texto=texto)


def contar(con, tabla, **donde):
    cond = " AND ".join(f"{k} = ?" for k in donde) or "1=1"
    return con.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {cond}", tuple(donde.values())).fetchone()[0]


# ---------------------------------------------------------------- orden de compra
def test_crear_la_orden_registra_proveedor_costo_y_fechas(w):
    con, p = w
    r = ejecutar_accion(con, p, orden())
    assert r.ok and r.id_registro
    o = con.execute("SELECT * FROM ordenes_compra WHERE orden_id = ?", (r.id_registro,)).fetchone()
    assert (o["sku"], o["proveedor_id"], o["cantidad"], o["costo_total"], o["estado"]) == ("BAG-001", 1, 54, 54 * 11000, "creada")
    assert o["fecha_entrega_esperada"] == "2026-11-13" and o["creada_en"].startswith("2026-11-03")  # hoy + lead time de 10 días


def test_crear_dos_veces_la_misma_orden_no_la_duplica(w):
    con, p = w
    a, b = ejecutar_accion(con, p, orden()), ejecutar_accion(con, p, orden())
    assert a.id_registro == b.id_registro and b.ok
    assert contar(con, "ordenes_compra", sku="BAG-001", estado="creada") == 1


@pytest.mark.parametrize("sku", ["BAG-003", "BAG-002", "BAG-012", "NO-EXISTE"])
def test_no_se_crea_una_orden_donde_no_hay_necesidad(w, sku):
    con, p = w
    antes = contar(con, "ordenes_compra")
    r = ejecutar_accion(con, p, orden(sku, 10))
    assert not r.ok and r.detalle.startswith("Rechazada") and contar(con, "ordenes_compra") == antes


# ---------------------------------------------------------------- avisos
def test_el_aviso_correcto_se_envia_y_el_repetido_se_rechaza(w):
    con, p = w
    assert contar(con, "bandeja_avisos", pedido_id="PED-C007") == 1
    assert ejecutar_accion(con, p, aviso("PED-C007", "solicitud_direccion", 2)).ok
    assert contar(con, "bandeja_avisos", pedido_id="PED-C007") == 2
    repetido = ejecutar_accion(con, p, aviso("PED-C007", "solicitud_direccion", 2))
    assert not repetido.ok and contar(con, "bandeja_avisos", pedido_id="PED-C007") == 2  # ya no toca el 2.º: toca el 3.º


@pytest.mark.parametrize("a", [
    aviso("PED-C007", "solicitud_direccion", 3),   # se saltaría un mensaje
    aviso("PED-C007", "solicitud_datos", 1),       # tipo que no corresponde a la causa
    aviso("PED-C008", "solicitud_direccion", 4),   # más de 3 mensajes
    aviso("PED-C013", "atraso", 1),                # pedido sin problema
    aviso("PED-C007", "solicitud_direccion", 2, texto=None),  # sin texto redactado
    aviso("PED-C007", "solicitud_direccion", 2, texto="   "),
])
def test_avisos_que_no_corresponden_se_rechazan(w, a):
    con, p = w
    antes = contar(con, "bandeja_avisos")
    assert not ejecutar_accion(con, p, a).ok and contar(con, "bandeja_avisos") == antes


# ---------------------------------------------------------------- compensaciones
def test_la_compensacion_correcta_se_emite_una_sola_vez(w):
    con, p = w
    a = EmitirCompensacion(accion="emitir_compensacion", pedido_id="PED-C001", tipo="reembolso_envio")
    assert ejecutar_accion(con, p, a).ok
    assert not ejecutar_accion(con, p, a).ok  # ya emitida
    assert contar(con, "compensaciones", pedido_id="PED-C001") == 1


@pytest.mark.parametrize("pid,tipo", [
    ("PED-C001", "cupon_30"),        # pagó el envío: corresponde reembolsarlo, no el cupón
    ("PED-C002", "reembolso_envio"), # no pagó el envío: no hay qué reembolsar
    ("PED-C003", "reembolso_envio"), # solo 1 día de atraso
    ("PED-C007", "cupon_30"),        # dirección errónea: no compensa
    ("PED-C010", "cupon_30"),        # cliente sin datos: no compensa
])
def test_compensaciones_que_no_corresponden_se_rechazan(w, pid, tipo):
    con, p = w
    antes = contar(con, "compensaciones")
    r = ejecutar_accion(con, p, EmitirCompensacion(accion="emitir_compensacion", pedido_id=pid, tipo=tipo))
    assert not r.ok and contar(con, "compensaciones") == antes


# ---------------------------------------------------------------- cancelaciones
def test_cancelar_tras_3_mensajes_deja_todo_consistente(w):
    con, p = w
    r = ejecutar_accion(con, p, CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id="PED-C008", motivo="direccion"))
    assert r.ok
    assert con.execute("SELECT estado FROM pedidos WHERE pedido_id = 'PED-C008'").fetchone()[0] == "cancelado"
    assert con.execute("SELECT estado FROM historial_estados WHERE pedido_id = 'PED-C008' ORDER BY evento_id DESC LIMIT 1").fetchone()[0] == "cancelado"
    reembolso = con.execute("SELECT motivo, estado FROM reembolsos WHERE pedido_id = 'PED-C008'").fetchone()
    assert tuple(reembolso) == ("direccion", "procesado")
    assert contar(con, "ventas", sku="BAG-008") == contar(con, "lineas_pedido", sku="BAG-008") - contar(
        con, "lineas_pedido l JOIN pedidos p ON p.pedido_id = l.pedido_id", **{"l.sku": "BAG-008", "p.estado": "cancelado"})
    assert not ejecutar_accion(con, p, CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id="PED-C008", motivo="direccion")).ok


@pytest.mark.parametrize("pid,motivo", [
    ("PED-C007", "direccion"),  # solo 1 mensaje
    ("PED-C010", "datos"),      # solo 2 mensajes
    ("PED-C009", "direccion"),  # el cliente respondió
    ("PED-C001", "otro"),       # courier: no se cancela
    ("PED-C008", "datos"),      # motivo equivocado
])
def test_cancelaciones_que_no_corresponden_no_tocan_nada(w, pid, motivo):
    con, p = w
    estado = con.execute("SELECT estado FROM pedidos WHERE pedido_id = ?", (pid,)).fetchone()[0]
    antes = contar(con, "reembolsos")
    r = ejecutar_accion(con, p, CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id=pid, motivo=motivo))
    assert not r.ok and contar(con, "reembolsos") == antes
    assert con.execute("SELECT estado FROM pedidos WHERE pedido_id = ?", (pid,)).fetchone()[0] == estado


# ---------------------------------------------------------------- lote y conexiones
def test_un_lote_ejecuta_lo_valido_y_rechaza_lo_demas_sin_detenerse(bd):
    resultados = ejecutar_lote(bd, [
        orden(),
        EmitirCompensacion(accion="emitir_compensacion", pedido_id="PED-C003", tipo="cupon_30"),  # no corresponde
        CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id="PED-C011", motivo="datos"),
    ])
    assert [r.ok for r in resultados] == [True, False, True]
    con = sqlite3.connect(bd)
    assert contar(con, "ordenes_compra", sku="BAG-001", estado="creada") == 1
    assert con.execute("SELECT estado FROM pedidos WHERE pedido_id = 'PED-C011'").fetchone()[0] == "cancelado"
    assert contar(con, "compensaciones", pedido_id="PED-C003") == 0


def test_se_acepta_una_accion_como_diccionario_y_se_valida(w):
    con, p = w
    assert ejecutar_accion(con, p, {"accion": "crear_orden_compra", "sku": "BAG-001", "cantidad": 54, "motivo": "quiebre próximo"}).ok
    with pytest.raises(Exception):
        ejecutar_accion(con, p, {"accion": "crear_orden_compra", "sku": "BAG-001", "cantidad": -1, "motivo": "xxx"})


def test_con_una_conexion_de_solo_lectura_las_acciones_no_pueden_escribir(bd):
    con = conectar(bd)  # solo lectura
    with pytest.raises(sqlite3.OperationalError):
        ejecutar_accion(con, cargar_parametros(con), orden())
    con.close()
