"""Herramientas de inventario (solo lectura): estado del stock, alertas y cantidad sugerida de una orden.

Reglas decididas:
  - Cobertura (días) = stock / ventas diarias de las últimas 2 semanas. Quiebre próximo: menos que el lead time.
  - Punto de reorden = ventas diarias × lead time + stock de seguridad. Bajo el punto: stock estrictamente menor.
  - Stock estancado: 14 días sin ventas (con stock). No se repone: se liquida o promociona.
  - Las unidades de pedidos detenidos por falta de stock cuentan como demanda no atendida.
Propuesta por validar: cantidad = ventas diarias × (lead time + cobertura objetivo) − stock − en camino + demanda no atendida.
"""
import math
import sqlite3

from app.schemas import EstadoStock, SugerenciaOrden
from app.tools.comun import Parametros, fecha_de

_POR_PREPARAR = "('solicitado','ingresado','en_preparacion','espera_despacho')"

_SQL = f"""
SELECT pr.sku, pr.nombre, i.stock_actual, i.stock_seguridad,
  COALESCE((SELECT SUM(cantidad) FROM ventas v WHERE v.sku = pr.sku AND v.fecha >= :d14 AND v.fecha < :hoy), 0) AS u14,
  (SELECT MAX(fecha) FROM ventas v WHERE v.sku = pr.sku) AS ultima,
  COALESCE((SELECT SUM(cantidad) FROM ordenes_compra o WHERE o.sku = pr.sku AND o.estado IN ('creada','en_camino')), 0) AS en_camino,
  COALESCE((SELECT SUM(l.cantidad) FROM lineas_pedido l JOIN pedidos pe ON pe.pedido_id = l.pedido_id
            WHERE l.sku = pr.sku AND pe.causa_atraso = 'falta_stock' AND pe.estado NOT IN ('entregado','cancelado')), 0) AS no_atendida,
  COALESCE((SELECT SUM(l.cantidad) FROM lineas_pedido l JOIN pedidos pe ON pe.pedido_id = l.pedido_id
            WHERE l.sku = pr.sku AND pe.estado IN {_POR_PREPARAR}), 0) AS por_preparar
FROM productos pr JOIN inventario i ON i.sku = pr.sku
"""


def estado_inventario(con: sqlite3.Connection, p: Parametros, skus: list[str] | None = None) -> list[EstadoStock]:
    out = []
    for f in con.execute(_SQL, {"d14": p.hace(14), "hoy": p.inicio_hoy}).fetchall():
        if skus is not None and f["sku"] not in skus:
            continue
        stock, tasa = f["stock_actual"], f["u14"] / 14
        cobertura = stock / tasa if tasa > 0 else None
        reorden = tasa * p.lead_time + f["stock_seguridad"]
        sin_ventas = (p.hoy - fecha_de(f["ultima"])).days if f["ultima"] else None
        alertas = []
        if stock == 0:
            alertas.append("agotado")
        else:
            if cobertura is not None and cobertura < p.lead_time:
                alertas.append("quiebre_proximo")
            if stock < reorden:
                alertas.append("bajo_punto_reorden")
            if sin_ventas is not None and sin_ventas >= p.estancado_dias:
                alertas.append("estancado")
        if f["por_preparar"] > stock:
            alertas.append("stock_insuficiente_para_pedidos")
        out.append(EstadoStock(
            sku=f["sku"], nombre=f["nombre"], stock_actual=stock, stock_seguridad=f["stock_seguridad"],
            ventas_diarias=round(tasa, 3), cobertura_dias=round(cobertura, 1) if cobertura is not None else None,
            punto_reorden=round(reorden, 1), dias_sin_ventas=sin_ventas, en_camino=f["en_camino"],
            demanda_no_atendida=f["no_atendida"], unidades_por_preparar=f["por_preparar"], alertas=alertas))
    return sorted(out, key=lambda e: e.sku)


def sugerir_orden(e: EstadoStock, p: Parametros) -> SugerenciaOrden | None:
    """Cantidad sugerida, o None si no hace falta ordenar (sin alerta de reposición o ya cubierto por lo que viene en camino)."""
    if not {"agotado", "quiebre_proximo", "bajo_punto_reorden"} & set(e.alertas):
        return None
    necesidad = e.ventas_diarias * (p.lead_time + p.cobertura_objetivo) - e.stock_actual - e.en_camino + e.demanda_no_atendida
    cantidad = math.ceil(necesidad - 1e-9)
    if cantidad <= 0:
        return None
    motivo = (f"{', '.join(a.replace('_', ' ') for a in e.alertas if a != 'estancado')}: ventas de {e.ventas_diarias:g} por día, "
              f"{e.stock_actual} en stock, {e.en_camino} en camino y {e.demanda_no_atendida} de demanda no atendida")
    return SugerenciaOrden(sku=e.sku, cantidad=cantidad, motivo=motivo)
