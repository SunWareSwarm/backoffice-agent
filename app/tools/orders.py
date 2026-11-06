"""Herramientas de pedidos y logística (solo lectura) y la MATRIZ DE GESTIÓN, escrita como código.

La matriz de gestión (qué hacer con cada pedido según su causa) vive aquí, no en el LLM:
  courier             -> avisar con nueva fecha (hoy + plazo) y compensar desde 2 días de atraso.
  falta de stock      -> con orden en camino: informar desde cuándo habrá stock y el cliente decide si espera
                         (compensa desde 2 días si espera); sin orden o si rechaza: se ofrece/gestiona el reembolso.
  dirección o datos   -> hasta 3 mensajes pidiendo el dato; sin respuesta, cancelar con reembolso. No compensa.
  capacidad           -> avisar con nueva fecha según la cola y compensar desde 2 días de atraso.
  sin causa registrada-> revisión humana; nunca se inventa una causa.
Las fechas las calcula este módulo. El LLM solo redacta con ellas.
"""
import math
import sqlite3
from datetime import timedelta

from app.schemas import (
    AccionPropuesta, CancelarConReembolso, CarteraEnEnvio, ColaPreparacion, DemandaVsCapacidad, DiaPico, EmitirCompensacion,
    EnviarAviso, EnviosEnCurso, Gestion, NuevaFecha, PedidoProblema,
)
from app.tools.comun import Parametros, elegible, fecha_de

_EN_COLA = ("solicitado", "ingresado")
_BLOQUEADOS = ("falta_stock", "cliente_sin_datos")  # no están en la cola de preparación: esperan algo externo


def _armar(con: sqlite3.Connection, p: Parametros, f: sqlite3.Row) -> PedidoProblema:
    pid = f["pedido_id"]
    avisos = dict(con.execute("SELECT tipo, COUNT(*) FROM bandeja_avisos WHERE pedido_id = ? GROUP BY tipo", (pid,)).fetchall())
    prometida = fecha_de(f["fecha_prometida"])
    return PedidoProblema(
        pedido_id=pid, estado=f["estado"], region=f["region_destino"], envio_pagado=bool(f["envio_pagado"]),
        fecha_prometida=prometida, dias_atraso=max(0, (p.hoy - prometida).days), vencido=p.hoy > prometida,
        causa=f["causa_atraso"],
        lineas=[(r["sku"], r["cantidad"]) for r in con.execute("SELECT sku, cantidad FROM lineas_pedido WHERE pedido_id = ? ORDER BY linea_id", (pid,))],
        avisos_atraso=avisos.get("atraso", 0), avisos_solicitud_direccion=avisos.get("solicitud_direccion", 0),
        avisos_solicitud_datos=avisos.get("solicitud_datos", 0),
        respuestas=[r[0] for r in con.execute("SELECT tipo FROM respuestas_cliente WHERE pedido_id = ? ORDER BY recibida_en", (pid,))],
        tiene_compensacion=con.execute("SELECT COUNT(*) FROM compensaciones WHERE pedido_id = ?", (pid,)).fetchone()[0] > 0,
    )


def pedidos_con_problema(con: sqlite3.Connection, p: Parametros) -> list[PedidoProblema]:
    """Pedidos no terminados que están vencidos o que tienen una causa de problema registrada (aunque aún no venzan)."""
    filas = con.execute(
        """SELECT * FROM pedidos WHERE estado NOT IN ('entregado', 'cancelado')
           AND (fecha_prometida < ? OR causa_atraso IS NOT NULL) ORDER BY pedido_id""", (p.hoy.isoformat(),)).fetchall()
    return [_armar(con, p, f) for f in filas]


def pedido_con_problema(con: sqlite3.Connection, p: Parametros, pedido_id: str) -> PedidoProblema | None:
    return next((x for x in pedidos_con_problema(con, p) if x.pedido_id == pedido_id), None)


def _cola(con: sqlite3.Connection, p: Parametros) -> list[str]:
    filas = con.execute(
        f"""SELECT pedido_id, fecha_ingreso FROM pedidos WHERE estado IN {_EN_COLA}
            AND COALESCE(causa_atraso, '') NOT IN {_BLOQUEADOS}""").fetchall()
    return [f["pedido_id"] for f in sorted(filas, key=lambda f: (elegible(f["fecha_ingreso"], p.hora_corte), f["fecha_ingreso"]))]


def _restante_hoy(con: sqlite3.Connection, p: Parametros) -> int:
    usados = con.execute("SELECT COUNT(*) FROM historial_estados WHERE estado = 'en_preparacion' AND fecha_hora >= ? AND fecha_hora < ?",
                         (p.inicio_hoy, f"{p.hoy + timedelta(days=1)} 00:00:00")).fetchone()[0]
    return max(0, p.capacidad - usados)


def _dias_hasta_preparar(posicion: int, restante: int, capacidad: int) -> int:
    return 0 if posicion <= restante else math.ceil((posicion - restante) / capacidad)


def cola_de_preparacion(con: sqlite3.Connection, p: Parametros) -> ColaPreparacion:
    n, restante = len(_cola(con, p)), _restante_hoy(con, p)
    return ColaPreparacion(en_cola=n, capacidad_diaria=p.capacidad, capacidad_restante_hoy=restante,
                           dias_para_vaciar=_dias_hasta_preparar(n, restante, p.capacidad) if n else 0)


def nueva_fecha(con: sqlite3.Connection, p: Parametros, x: PedidoProblema) -> NuevaFecha:
    """Nueva fecha de entrega según la causa. Devuelve fecha=None cuando no hay una (con su razón)."""
    plazo = p.plazo(x.region)
    if x.causa is None:
        return NuevaFecha(fecha=None, condicional=False, regla="sin causa registrada: no se calcula una fecha")
    if x.causa == "courier":
        return NuevaFecha(fecha=p.hoy + timedelta(days=plazo), condicional=False, regla=f"hoy + plazo de entrega ({plazo} días)")
    if x.causa == "falta_stock":
        llegadas = []
        for sku, _ in x.lineas:
            r = con.execute("SELECT MIN(fecha_entrega_esperada) FROM ordenes_compra WHERE sku = ? AND estado IN ('creada','en_camino')", (sku,)).fetchone()[0]
            if r is None:
                return NuevaFecha(fecha=None, condicional=False, regla="sin orden de compra en camino: se ofrece el reembolso")
            llegadas.append(fecha_de(r))
        llegada = max(llegadas)
        return NuevaFecha(fecha=llegada + timedelta(days=2 + plazo), condicional=False,
                          regla=f"llegada de la orden ({llegada}) + despacho (corte de las {p.hora_corte}:00) + plazo de entrega ({plazo} días)")
    if x.causa in ("direccion_erronea", "cliente_sin_datos"):
        r = con.execute("SELECT MAX(recibida_en) FROM respuestas_cliente WHERE pedido_id = ? AND tipo IN ('envia_direccion','envia_datos')",
                        (x.pedido_id,)).fetchone()[0]
        if r is None:
            return NuevaFecha(fecha=None, condicional=True, regla="no hay fecha hasta que el cliente responda")
        base = elegible(r, p.hora_corte)
        return NuevaFecha(fecha=base + timedelta(days=2 + plazo), condicional=False,
                          regla=f"respuesta del cliente ({r[:10]}) + corte de las {p.hora_corte}:00 + plazo de entrega ({plazo} días)")
    # capacidad
    cola, restante = _cola(con, p), _restante_hoy(con, p)
    if x.pedido_id in cola:
        dias = _dias_hasta_preparar(cola.index(x.pedido_id) + 1, restante, p.capacidad)
        return NuevaFecha(fecha=p.hoy + timedelta(days=dias + 2 + plazo), condicional=False,
                          regla=f"posición {cola.index(x.pedido_id) + 1} en la cola, a {p.capacidad} por día: se prepara en {dias} día(s); + plazo de entrega ({plazo} días)")
    ya_avanzado = {"en_preparacion": 2 + plazo, "espera_despacho": 1 + plazo}.get(x.estado, plazo)
    return NuevaFecha(fecha=p.hoy + timedelta(days=ya_avanzado), condicional=False, regla=f"ya salió de la cola (estado {x.estado}): hoy + {ya_avanzado} días")


def _compensacion(x: PedidoProblema, p: Parametros):
    if x.tiene_compensacion or x.dias_atraso < p.compensacion_desde:
        return None
    return "reembolso_envio" if x.envio_pagado else "cupon_30"


def decidir_gestion(con: sqlite3.Connection, p: Parametros, x: PedidoProblema) -> Gestion:
    """La matriz de gestión: qué acciones se proponen para este pedido. Todas pasan por la aprobación del usuario."""
    nf = nueva_fecha(con, p, x)
    acciones: list[AccionPropuesta] = []

    def listo(explicacion, revision=False):
        return Gestion(pedido_id=x.pedido_id, causa=x.causa, nueva_fecha=nf, acciones=acciones, requiere_revision=revision,
                       explicacion=explicacion)

    def aviso(tipo="atraso", intento=1, **extra):
        acciones.append(EnviarAviso(accion="enviar_aviso", pedido_id=x.pedido_id, tipo=tipo, intento=intento, **extra))

    comp = _compensacion(x, p)

    def compensar():
        if comp:
            acciones.append(EmitirCompensacion(accion="emitir_compensacion", pedido_id=x.pedido_id, tipo=comp))

    if x.causa is None:
        return listo("Causa no determinada: no se inventa una causa; pasa a revisión humana", revision=True)

    if x.causa == "courier":
        if x.avisos_atraso == 0:
            aviso(nueva_fecha=nf.fecha, compensacion=comp)
        compensar()
        return listo("Courier: avisar con nueva fecha y compensar desde 2 días de atraso")

    if x.causa == "falta_stock":
        if "rechaza_espera" in x.respuestas:
            acciones.append(CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id=x.pedido_id, motivo="sin_stock"))
            return listo("Falta de stock: el cliente no quiere esperar, se cancela y se reembolsa")
        if "acepta_espera" in x.respuestas:
            compensar()
            return listo("Falta de stock: el cliente aceptó esperar; se compensa desde 2 días de atraso")
        if nf.fecha is None:
            if x.avisos_atraso == 0:
                aviso(ofrece_reembolso=True)
            return listo("Falta de stock sin orden de compra en camino: se ofrece el reembolso")
        if x.dias_atraso <= p.espera_stock:
            if x.avisos_atraso == 0:
                aviso(nueva_fecha=nf.fecha)
            return listo(f"Falta de stock con orden en camino: se informa desde cuándo habrá stock y el cliente decide (primeros {p.espera_stock} días)")
        if x.avisos_atraso <= 1:
            aviso(intento=x.avisos_atraso + 1, nueva_fecha=nf.fecha, ofrece_reembolso=True)
        return listo(f"Falta de stock: pasaron más de {p.espera_stock} días de atraso, se ofrece el reembolso")

    if x.causa in ("direccion_erronea", "cliente_sin_datos"):
        direccion = x.causa == "direccion_erronea"
        enviados = x.avisos_solicitud_direccion if direccion else x.avisos_solicitud_datos
        respondio = ("envia_direccion" if direccion else "envia_datos") in x.respuestas
        if respondio:
            return listo("El cliente respondió: se reprograma la entrega con la nueva fecha")
        if enviados >= p.max_mensajes:
            acciones.append(CancelarConReembolso(accion="cancelar_con_reembolso", pedido_id=x.pedido_id, motivo="direccion" if direccion else "datos"))
            return listo(f"Se enviaron {p.max_mensajes} mensajes sin respuesta: se cancela el pedido y se reembolsa")
        aviso("solicitud_direccion" if direccion else "solicitud_datos", intento=enviados + 1)
        return listo(f"Se pide el dato al cliente (mensaje {enviados + 1} de {p.max_mensajes}). Este caso no compensa")

    # capacidad
    if x.avisos_atraso == 0:
        aviso(nueva_fecha=nf.fecha, compensacion=comp)
    compensar()
    return listo("Capacidad de preparación: avisar con la nueva fecha de la cola y compensar desde 2 días de atraso")


# ---------------------------------------------------------------- panorama: envíos en curso y demanda frente a capacidad
_EN_TRANSITO = ("espera_despacho", "en_ruta", "proximo_a_llegar")


def envios_en_curso(con: sqlite3.Connection, p: Parametros, max_carteras: int = 10) -> EnviosEnCurso:
    """Cuántos pedidos hay sin terminar en cada estado y qué carteras van en los que están en tránsito."""
    por_estado = {f["estado"]: f["n"] for f in con.execute(
        "SELECT estado, COUNT(*) AS n FROM pedidos WHERE estado NOT IN ('entregado', 'cancelado') GROUP BY estado ORDER BY n DESC, estado")}
    filas = con.execute(
        f"""SELECT l.sku, pr.nombre, SUM(l.cantidad) AS unidades, COUNT(DISTINCT pe.pedido_id) AS pedidos
            FROM lineas_pedido l JOIN pedidos pe ON pe.pedido_id = l.pedido_id JOIN productos pr ON pr.sku = l.sku
            WHERE pe.estado IN {_EN_TRANSITO} GROUP BY l.sku, pr.nombre ORDER BY unidades DESC, l.sku LIMIT ?""", (max_carteras,)).fetchall()
    return EnviosEnCurso(
        total_en_transito=sum(por_estado.get(e, 0) for e in _EN_TRANSITO), por_estado=por_estado,
        carteras=[CarteraEnEnvio(sku=f["sku"], nombre=f["nombre"], unidades=f["unidades"], pedidos=f["pedidos"]) for f in filas])


def demanda_vs_capacidad(con: sqlite3.Connection, p: Parametros, dias: int = 84) -> DemandaVsCapacidad:
    """Pedidos por día frente a la capacidad de preparación, y cuánto esperan los pedidos en los días pico."""
    filas = con.execute(
        """SELECT substr(pe.fecha_ingreso, 1, 10) AS dia, COUNT(*) AS pedidos,
                  AVG(julianday(h2.fecha_hora) - julianday(h1.fecha_hora)) AS espera
           FROM pedidos pe
           JOIN historial_estados h1 ON h1.pedido_id = pe.pedido_id AND h1.estado = 'ingresado'
           LEFT JOIN historial_estados h2 ON h2.pedido_id = pe.pedido_id AND h2.estado = 'en_preparacion'
           WHERE pe.estado <> 'cancelado' AND pe.fecha_ingreso >= ? AND pe.fecha_ingreso < ?
           GROUP BY dia ORDER BY dia""", (p.hace(dias), p.inicio_hoy)).fetchall()
    pico = [f for f in filas if f["pedidos"] > p.capacidad]
    normal = [f for f in filas if f["pedidos"] <= p.capacidad]

    def media(grupo):
        esperas = [f["espera"] for f in grupo if f["espera"] is not None]
        return round(sum(esperas) / len(esperas), 2) if esperas else None

    total, tarde = con.execute(
        """SELECT COUNT(*), COALESCE(SUM(CASE WHEN date(h.fecha_hora) > pe.fecha_prometida THEN 1 ELSE 0 END), 0)
           FROM pedidos pe JOIN historial_estados h ON h.pedido_id = pe.pedido_id AND h.estado = 'entregado'
           WHERE pe.fecha_ingreso >= ? AND pe.fecha_ingreso < ?""", (p.hace(dias), p.inicio_hoy)).fetchone()
    top = sorted(filas, key=lambda f: (-f["pedidos"], f["dia"]))[:3]
    return DemandaVsCapacidad(
        capacidad_diaria=p.capacidad, dias_analizados=len(filas), dias_sobre_capacidad=len(pico),
        pedidos_dia_maximo=max((f["pedidos"] for f in filas), default=0),
        promedio_pedidos_dia=round(sum(f["pedidos"] for f in filas) / len(filas), 1) if filas else 0.0,
        espera_promedio_dias_normales=media(normal), espera_promedio_dias_pico=media(pico), entregas_totales=total, entregas_con_atraso=tarde,
        dias_pico=[DiaPico(fecha=fecha_de(f["dia"]), pedidos=f["pedidos"], espera_promedio_dias=round(f["espera"] or 0, 2)) for f in top])
