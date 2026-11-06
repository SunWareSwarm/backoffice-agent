"""Piezas comunes de las herramientas: los parámetros de negocio salen de la base, no del código."""
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class Parametros:
    hoy: date  # reloj simulado: parametros.fecha_referencia
    lead_time: int
    hora_corte: int
    capacidad: int
    plazo_rm: int
    plazo_regiones: int
    compensacion_desde: int
    cupon_porcentaje: int
    espera_stock: int
    max_mensajes: int
    estancado_dias: int
    minimo_ganador: int
    historial_min_semanas: int
    cobertura_objetivo: int

    def plazo(self, region: str) -> int:
        return self.plazo_rm if region == "RM" else self.plazo_regiones

    @property
    def inicio_hoy(self) -> str:
        return f"{self.hoy} 00:00:00"

    def hace(self, dias: int) -> str:
        """Inicio del día que fue `dias` antes de hoy, como texto comparable con las fechas de la base."""
        return f"{self.hoy - timedelta(days=dias)} 00:00:00"


def cargar_parametros(con: sqlite3.Connection) -> Parametros:
    v = dict(con.execute("SELECT clave, valor FROM parametros").fetchall())
    return Parametros(
        hoy=date.fromisoformat(v["fecha_referencia"]),
        lead_time=int(v["lead_time_proveedor_dias"]),
        hora_corte=int(v["hora_corte"].split(":")[0]),
        capacidad=int(v["capacidad_diaria"]),
        plazo_rm=int(v["plazo_entrega_rm_dias"]),
        plazo_regiones=int(v["plazo_entrega_regiones_dias"]),
        compensacion_desde=int(v["compensacion_desde_dias"]),
        cupon_porcentaje=int(v["cupon_porcentaje"]),
        espera_stock=int(v["espera_stock_dias"]),
        max_mensajes=int(v["max_mensajes_datos"]),
        estancado_dias=int(v["stock_estancado_dias"]),
        minimo_ganador=int(v["minimo_ventas_ganador"]),
        historial_min_semanas=int(v["historial_minimo_semanas"]),
        cobertura_objetivo=int(v["cobertura_objetivo_dias"]),
    )


def fecha_de(texto: str) -> date:
    return date.fromisoformat(texto[:10])


def elegible(ingreso: str, hora_corte: int) -> date:
    """Un pedido ingresado antes del corte entra a preparación ese día; después, al siguiente."""
    t = datetime.fromisoformat(ingreso)
    return t.date() + timedelta(days=0 if t.hour < hora_corte else 1)
