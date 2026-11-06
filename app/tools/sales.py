"""Herramientas de ventas (solo lectura). Las usan BI e Inventario.

Definiciones decididas:
  - Ventas por unidad de stock = unidades vendidas / (unidades vendidas + stock actual). Corrige el
    sesgo de disponibilidad: un producto con mucho stock vende más aunque guste menos.
  - Ganador desde 15 ventas; con menos, prospecto. Con menos de 4 semanas de historial no hay
    evidencia suficiente para recomendar.
  - Un pedido cancelado no es una venta (la vista `ventas` ya lo excluye).
"""
import sqlite3
from datetime import timedelta

from app.schemas import ResumenVentas, SemanaVentas, TendenciaVentas, VentasProducto
from app.tools.comun import Parametros, fecha_de


def resumen_ventas(con: sqlite3.Connection, p: Parametros, dias: int = 7) -> ResumenVentas:
    """Ventas de los últimos `dias` días completos (de hoy-dias a ayer; hoy no se cuenta, está incompleto)."""
    u, ing = con.execute(
        "SELECT COALESCE(SUM(cantidad), 0), COALESCE(SUM(cantidad * precio_unitario), 0) FROM ventas WHERE fecha >= ? AND fecha < ?",
        (p.hace(dias), p.inicio_hoy),
    ).fetchone()
    return ResumenVentas(desde=p.hoy - timedelta(days=dias), hasta=p.hoy - timedelta(days=1), unidades=u, ingresos=ing)


def ventas_por_producto(con: sqlite3.Connection, p: Parametros, dias: int = 84, skus: list[str] | None = None) -> list[VentasProducto]:
    """Una fila por producto, de mayor a menor ventas por unidad de stock."""
    filas = con.execute(
        """SELECT pr.sku, pr.nombre, pr.categoria, pr.precio, pr.costo, i.stock_actual,
                  COALESCE((SELECT SUM(cantidad) FROM ventas v WHERE v.sku = pr.sku AND v.fecha >= :desde AND v.fecha < :hoy), 0) AS u,
                  COALESCE((SELECT SUM(cantidad * precio_unitario) FROM ventas v WHERE v.sku = pr.sku AND v.fecha >= :desde AND v.fecha < :hoy), 0) AS ing,
                  (SELECT MIN(fecha) FROM ventas v WHERE v.sku = pr.sku) AS primera
           FROM productos pr JOIN inventario i ON i.sku = pr.sku""",
        {"desde": p.hace(dias), "hoy": p.inicio_hoy},
    ).fetchall()
    out = []
    for f in filas:
        if skus is not None and f["sku"] not in skus:
            continue
        semanas = (p.hoy - fecha_de(f["primera"])).days / 7 if f["primera"] else 0.0
        total = f["u"] + f["stock_actual"]
        out.append(VentasProducto(
            sku=f["sku"], nombre=f["nombre"], categoria=f["categoria"], unidades=f["u"], ingresos=f["ing"],
            margen_pct=round((f["precio"] - f["costo"]) / f["precio"], 4), stock_actual=f["stock_actual"],
            ventas_por_unidad_de_stock=round(f["u"] / total, 4) if total else 0.0,
            semanas_de_historial=round(semanas, 1),
            clasificacion="ganador" if f["u"] >= p.minimo_ganador else "prospecto",
            evidencia_suficiente=semanas >= p.historial_min_semanas,
        ))
    return sorted(out, key=lambda v: (-v.ventas_por_unidad_de_stock, v.sku))


def tendencia_semanal(con: sqlite3.Connection, p: Parametros, sku: str, semanas: int = 4) -> TendenciaVentas:
    """Unidades por semana, de la más antigua a la más reciente.

    La tendencia compara la mitad reciente de la serie con la mitad anterior (±20 %). Comparar solo las dos
    últimas semanas pasaría por alto un salto que ocurrió antes y se mantiene.
    """
    serie = []
    for i in range(semanas, 0, -1):
        desde, hasta = p.hoy - timedelta(days=7 * i), p.hoy - timedelta(days=7 * (i - 1))
        u = con.execute("SELECT COALESCE(SUM(cantidad), 0) FROM ventas WHERE sku = ? AND fecha >= ? AND fecha < ?",
                        (sku, f"{desde} 00:00:00", f"{hasta} 00:00:00")).fetchone()[0]
        serie.append(SemanaVentas(desde=desde, unidades=u))
    mitad = max(1, len(serie) // 2)
    previa, ultima = sum(s.unidades for s in serie[:mitad]), sum(s.unidades for s in serie[-mitad:])
    if ultima > previa * 1.2 or (previa == 0 and ultima > 0):
        tendencia = "sube"
    elif ultima < previa * 0.8:
        tendencia = "baja"
    else:
        tendencia = "estable"
    return TendenciaVentas(sku=sku, semanas=serie, tendencia=tendencia)
