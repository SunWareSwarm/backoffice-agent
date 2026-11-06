"""Acciones con efecto (simuladas). SOLO se ejecutan después de la aprobación del usuario.

Defensa en profundidad: aunque el usuario apruebe una acción, antes de escribir se vuelve a comprobar que
la acción sigue siendo la que recomiendan las reglas (matriz de gestión y alertas de inventario). Si el
pedido cambió desde que se propuso, o si la acción es inventada o está repetida, se rechaza con el motivo.

Cada acción es atómica (todo o nada) y se puede repetir sin duplicar efectos.
"""
import sqlite3
from datetime import datetime, time, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from app.db.connection import conectar
from app.schemas import (
    AccionPropuesta, CancelarConReembolso, CrearOrdenCompra, EmitirCompensacion, EnviarAviso, ResultadoAccion,
)
from app.tools.comun import Parametros, cargar_parametros
from app.tools.inventory import estado_inventario
from app.tools.orders import decidir_gestion, pedido_con_problema

_ALERTAS_DE_REPOSICION = {"agotado", "quiebre_proximo", "bajo_punto_reorden"}
_ADAPTADOR = TypeAdapter(AccionPropuesta)


def _ahora(p: Parametros) -> str:
    return datetime.combine(p.hoy, time(12, 0)).isoformat(sep=" ")


def _rechazo(accion, motivo: str) -> ResultadoAccion:
    return ResultadoAccion(accion=accion.accion, ok=False, detalle=f"Rechazada: {motivo}")


def _vigente(con, p, accion) -> bool:
    """¿La matriz de gestión recomienda hoy exactamente esta acción para el pedido?"""
    pedido = pedido_con_problema(con, p, accion.pedido_id)
    if pedido is None:
        return False
    for v in decidir_gestion(con, p, pedido).acciones:
        if v.accion != accion.accion:
            continue
        if isinstance(accion, EnviarAviso) and (v.tipo, v.intento) == (accion.tipo, accion.intento):
            return True
        if isinstance(accion, EmitirCompensacion) and v.tipo == accion.tipo:
            return True
        if isinstance(accion, CancelarConReembolso) and v.motivo == accion.motivo:
            return True
    return False


def _crear_orden(con, p, a: CrearOrdenCompra) -> ResultadoAccion:
    estado = next(iter(estado_inventario(con, p, [a.sku])), None)
    if estado is None:
        return _rechazo(a, f"el producto {a.sku} no existe")
    if not _ALERTAS_DE_REPOSICION & set(estado.alertas):
        return _rechazo(a, "no hay una necesidad de reposición registrada para ese producto")
    previa = con.execute("SELECT orden_id FROM ordenes_compra WHERE sku = ? AND estado = 'creada' AND cantidad = ? AND creada_en >= ?",
                         (a.sku, a.cantidad, p.inicio_hoy)).fetchone()
    if previa:
        return ResultadoAccion(accion=a.accion, ok=True, detalle="La orden ya había sido creada hoy: no se duplica", id_registro=previa[0])
    costo, proveedor = con.execute("SELECT costo, proveedor_id FROM productos WHERE sku = ?", (a.sku,)).fetchone()
    with con:
        cur = con.execute(
            "INSERT INTO ordenes_compra (sku, proveedor_id, cantidad, costo_total, estado, creada_en, fecha_entrega_esperada) VALUES (?,?,?,?,?,?,?)",
            (a.sku, proveedor, a.cantidad, a.cantidad * costo, "creada", _ahora(p), (p.hoy + timedelta(days=p.lead_time)).isoformat()))
    return ResultadoAccion(accion=a.accion, ok=True, detalle=f"Orden {cur.lastrowid} creada: {a.cantidad} unidades de {a.sku}, "
                           f"llegada esperada el {p.hoy + timedelta(days=p.lead_time)}", id_registro=cur.lastrowid)


def _enviar_aviso(con, p, a: EnviarAviso) -> ResultadoAccion:
    if not a.texto or not a.texto.strip():
        return _rechazo(a, "el aviso todavía no tiene texto redactado")
    if not _vigente(con, p, a):
        return _rechazo(a, f"este aviso ({a.tipo}, mensaje {a.intento}) no corresponde según las reglas en este momento")
    with con:
        cur = con.execute("INSERT INTO bandeja_avisos (pedido_id, tipo, texto, estado) VALUES (?,?,?,?)", (a.pedido_id, a.tipo, a.texto.strip(), "enviado"))
    return ResultadoAccion(accion=a.accion, ok=True, detalle=f"Aviso de tipo {a.tipo} enviado (mensaje {a.intento})", id_registro=cur.lastrowid)


def _compensar(con, p, a: EmitirCompensacion) -> ResultadoAccion:
    if not _vigente(con, p, a):
        return _rechazo(a, "la compensación no corresponde (atraso menor al mínimo, causa que no compensa, tipo equivocado o ya emitida)")
    with con:
        cur = con.execute("INSERT INTO compensaciones (pedido_id, tipo, estado, creada_en) VALUES (?,?,?,?)", (a.pedido_id, a.tipo, "emitida", _ahora(p)))
    return ResultadoAccion(accion=a.accion, ok=True, detalle=f"Compensación {a.tipo} emitida", id_registro=cur.lastrowid)


def _cancelar(con, p, a: CancelarConReembolso) -> ResultadoAccion:
    if not _vigente(con, p, a):
        return _rechazo(a, "la cancelación no corresponde según las reglas (por ejemplo, aún no se agotaron los mensajes)")
    with con:
        con.execute("UPDATE pedidos SET estado = 'cancelado' WHERE pedido_id = ?", (a.pedido_id,))
        con.execute("INSERT INTO historial_estados (pedido_id, estado, fecha_hora) VALUES (?,?,?)", (a.pedido_id, "cancelado", _ahora(p)))
        cur = con.execute("INSERT INTO reembolsos (pedido_id, motivo, estado, creado_en) VALUES (?,?,?,?)", (a.pedido_id, a.motivo, "procesado", _ahora(p)))
    return ResultadoAccion(accion=a.accion, ok=True, detalle=f"Pedido {a.pedido_id} cancelado y reembolsado (motivo: {a.motivo})", id_registro=cur.lastrowid)


_EJECUTORES = {CrearOrdenCompra: _crear_orden, EnviarAviso: _enviar_aviso, EmitirCompensacion: _compensar, CancelarConReembolso: _cancelar}


def ejecutar_accion(con: sqlite3.Connection, p: Parametros, accion: AccionPropuesta) -> ResultadoAccion:
    """Ejecuta UNA acción aprobada con una conexión de escritura."""
    accion = _ADAPTADOR.validate_python(accion) if isinstance(accion, dict) else accion
    return _EJECUTORES[type(accion)](con, p, accion)


def ejecutar_lote(ruta_db: str | Path, acciones: list[AccionPropuesta]) -> list[ResultadoAccion]:
    """Ejecuta las acciones aprobadas, en orden. Un rechazo no detiene a las demás."""
    con = conectar(ruta_db, solo_lectura=False)
    try:
        p = cargar_parametros(con)
        return [ejecutar_accion(con, p, a) for a in acciones]
    finally:
        con.close()
