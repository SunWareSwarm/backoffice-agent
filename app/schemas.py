"""Contratos de datos (Pydantic): la forma de lo que entra y sale de cada frontera del sistema.

Reglas que se siguen en todo el archivo (ver docs/decisiones.md):
  - `extra="forbid"`: un campo que nadie declaró se rechaza (por ejemplo, uno inventado por un LLM).
  - Vocabularios cerrados (Literal) en todo lo que decide o enruta.
  - Reglas entre campos con validadores: los estados inválidos no pueden existir.
  - Cada campo tiene un dueño: lo calcula el CÓDIGO (herramientas) o lo llena el LLM. El LLM nunca
    calcula cifras ni fechas: las recibe ya calculadas en estos contratos.

Secciones: solicitud y ruta · resultados de las herramientas · acciones propuestas.
"""
from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

Agente = Literal["bi", "inventario", "logistica"]
ORDEN_AGENTES: tuple[str, ...] = ("bi", "inventario", "logistica")  # orden fijo cuando participa más de uno
Causa = Literal["courier", "falta_stock", "direccion_erronea", "cliente_sin_datos", "capacidad"]
TipoCompensacion = Literal["reembolso_envio", "cupon_30"]
TipoAviso = Literal["atraso", "solicitud_direccion", "solicitud_datos"]
MotivoReembolso = Literal["sin_stock", "direccion", "datos", "otro"]


class _Contrato(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ======================================================================= solicitud y ruta
class Solicitud(_Contrato):
    """Lo que escribe el usuario. Se valida en el borde de la API."""
    texto: str = Field(min_length=3, max_length=500, description="Consulta del usuario en lenguaje natural")

    @model_validator(mode="before")
    @classmethod
    def sin_espacios_sobrantes(cls, datos):
        if isinstance(datos, dict) and isinstance(datos.get("texto"), str):
            datos = {**datos, "texto": datos["texto"].strip()}
        return datos


class Tarea(_Contrato):
    agente: Agente = Field(description="Especialista que ejecuta la tarea")
    instruccion: str = Field(min_length=5, description="Qué debe hacer, en una frase")


class DecisionRuta(_Contrato):
    """Lo que decide el orquestador (lo llena el LLM; el código valida)."""
    decision: Literal["derivar", "aclarar", "fuera_de_alcance"]
    tareas: list[Tarea] = Field(default_factory=list, description="Solo con 'derivar'; en el orden fijo de los especialistas")
    pregunta_aclaratoria: str | None = Field(default=None, description="Solo con 'aclarar'")
    motivo: str = Field(min_length=3, description="Por qué decidió así; sirve para depurar y evaluar")

    @model_validator(mode="after")
    def coherencia(self):
        if self.decision == "derivar":
            if not self.tareas:
                raise ValueError("derivar exige al menos una tarea")
            agentes = [t.agente for t in self.tareas]
            if len(set(agentes)) != len(agentes):
                raise ValueError("un especialista no puede aparecer dos veces")
            if agentes != sorted(agentes, key=ORDEN_AGENTES.index):
                raise ValueError(f"los especialistas deben ir en el orden fijo {ORDEN_AGENTES}")
        elif self.tareas:
            raise ValueError("solo 'derivar' lleva tareas")
        if self.decision == "aclarar" and not self.pregunta_aclaratoria:
            raise ValueError("aclarar exige una pregunta")
        if self.decision != "aclarar" and self.pregunta_aclaratoria:
            raise ValueError("solo 'aclarar' lleva pregunta aclaratoria")
        return self


# ======================================================================= resultados de las herramientas: ventas
class ResumenVentas(_Contrato):
    desde: date
    hasta: date = Field(description="Último día incluido")
    unidades: int = Field(ge=0)
    ingresos: float = Field(ge=0)


class VentasProducto(_Contrato):
    sku: str
    nombre: str
    categoria: str
    unidades: int = Field(ge=0, description="Unidades vendidas en el período")
    ingresos: float = Field(ge=0)
    margen_pct: float = Field(description="(precio - costo) / precio")
    stock_actual: int = Field(ge=0)
    ventas_por_unidad_de_stock: float = Field(ge=0, le=1, description="unidades / (unidades + stock actual)")
    semanas_de_historial: float = Field(ge=0, description="Desde su primera venta registrada hasta hoy")
    clasificacion: Literal["ganador", "prospecto"] = Field(description="Ganador desde el mínimo de ventas; si no, prospecto")
    evidencia_suficiente: bool = Field(description="False si tiene menos semanas de historial que el mínimo")


class SemanaVentas(_Contrato):
    desde: date
    unidades: int = Field(ge=0)


class TendenciaVentas(_Contrato):
    sku: str
    semanas: list[SemanaVentas] = Field(description="De la más antigua a la más reciente")
    tendencia: Literal["sube", "baja", "estable"] = Field(description="Mitad reciente de la serie frente a la mitad anterior")


# ======================================================================= resultados de las herramientas: inventario
Alerta = Literal["agotado", "quiebre_proximo", "bajo_punto_reorden", "estancado", "stock_insuficiente_para_pedidos"]


class EstadoStock(_Contrato):
    sku: str
    nombre: str
    stock_actual: int = Field(ge=0)
    stock_seguridad: int = Field(ge=0)
    ventas_diarias: float = Field(ge=0, description="Promedio de las últimas 2 semanas")
    cobertura_dias: float | None = Field(description="stock / ventas diarias; None si no hubo ventas")
    punto_reorden: float = Field(ge=0, description="ventas diarias × lead time + stock de seguridad")
    dias_sin_ventas: int | None = Field(description="None si nunca se vendió")
    en_camino: int = Field(ge=0, description="Unidades en órdenes de compra creadas o en camino")
    demanda_no_atendida: int = Field(ge=0, description="Unidades de pedidos detenidos por falta de stock")
    unidades_por_preparar: int = Field(ge=0, description="Unidades de pedidos aún sin despachar")
    alertas: list[Alerta]


class SugerenciaOrden(_Contrato):
    sku: str
    cantidad: int = Field(gt=0)
    motivo: str


# ======================================================================= resultados de las herramientas: logística
class PedidoProblema(_Contrato):
    pedido_id: str
    estado: str
    region: Literal["RM", "regiones"]
    envio_pagado: bool
    fecha_prometida: date
    dias_atraso: int = Field(ge=0, description="0 si aún no vence")
    vencido: bool
    causa: Causa | None
    lineas: list[tuple[str, int]] = Field(description="(sku, cantidad)")
    avisos_atraso: int = Field(ge=0, description="Avisos de atraso ya enviados")
    avisos_solicitud_direccion: int = Field(ge=0)
    avisos_solicitud_datos: int = Field(ge=0)
    respuestas: list[str]
    tiene_compensacion: bool


class NuevaFecha(_Contrato):
    fecha: date | None
    condicional: bool = Field(description="True si depende de una respuesta del cliente")
    regla: str = Field(description="Cómo se calculó, para poder explicarlo")


class ColaPreparacion(_Contrato):
    en_cola: int = Field(ge=0)
    capacidad_diaria: int = Field(gt=0)
    capacidad_restante_hoy: int = Field(ge=0)
    dias_para_vaciar: int = Field(ge=0)


class CarteraEnEnvio(_Contrato):
    sku: str
    nombre: str
    unidades: int = Field(ge=0)
    pedidos: int = Field(ge=0)


class EnviosEnCurso(_Contrato):
    """Foto de los pedidos que aún no terminan: cuántos hay en cada estado y qué carteras van en los que están en tránsito."""
    total_en_transito: int = Field(ge=0, description="Pedidos ya preparados: esperan al courier, van en ruta o están por llegar")
    por_estado: dict[str, int] = Field(description="Todos los pedidos no terminados, por estado")
    carteras: list[CarteraEnEnvio] = Field(description="Las carteras que van en pedidos en tránsito, de más a menos unidades")


class DiaPico(_Contrato):
    fecha: date
    pedidos: int = Field(ge=0)
    espera_promedio_dias: float = Field(ge=0, description="Días entre que el pedido se ingresa y empieza a prepararse")


class DemandaVsCapacidad(_Contrato):
    """La demanda histórica frente a la capacidad de preparación: dónde está el cuello de botella."""
    capacidad_diaria: int = Field(gt=0)
    dias_analizados: int = Field(ge=0, description="Días con pedidos dentro del período")
    dias_sobre_capacidad: int = Field(ge=0, description="Días con más pedidos que la capacidad de preparación")
    pedidos_dia_maximo: int = Field(ge=0)
    promedio_pedidos_dia: float = Field(ge=0)
    espera_promedio_dias_normales: float | None = Field(description="Espera promedio en los días dentro de la capacidad")
    espera_promedio_dias_pico: float | None = Field(description="Espera promedio en los días sobre la capacidad")
    entregas_totales: int = Field(ge=0)
    entregas_con_atraso: int = Field(ge=0, description="Pedidos entregados después de su fecha prometida")
    dias_pico: list[DiaPico] = Field(description="Los 3 días con más pedidos")


# ======================================================================= acciones propuestas (todas pasan por la aprobación)
class CrearOrdenCompra(_Contrato):
    accion: Literal["crear_orden_compra"]
    sku: str
    cantidad: int = Field(gt=0)
    motivo: str = Field(min_length=3)


class EnviarAviso(_Contrato):
    accion: Literal["enviar_aviso"]
    pedido_id: str
    tipo: TipoAviso
    intento: int = Field(ge=1, description="Nº del mensaje de ese tipo para el pedido (máximo 3 al pedir datos)")
    nueva_fecha: date | None = None
    ofrece_reembolso: bool = False
    compensacion: TipoCompensacion | None = None
    texto: str | None = Field(default=None, description="Lo redacta el agente después; sin texto no se puede ejecutar")


class EmitirCompensacion(_Contrato):
    accion: Literal["emitir_compensacion"]
    pedido_id: str
    tipo: TipoCompensacion


class CancelarConReembolso(_Contrato):
    accion: Literal["cancelar_con_reembolso"]
    pedido_id: str
    motivo: MotivoReembolso


AccionPropuesta = Annotated[
    Union[CrearOrdenCompra, EnviarAviso, EmitirCompensacion, CancelarConReembolso], Field(discriminator="accion")
]


class Gestion(_Contrato):
    """Qué hay que hacer con un pedido con problema, según la matriz de gestión (la calcula el código)."""
    pedido_id: str
    causa: Causa | None
    nueva_fecha: NuevaFecha | None
    acciones: list[AccionPropuesta]
    requiere_revision: bool = Field(description="True si el sistema no debe decidir solo")
    explicacion: str = Field(description="La regla aplicada, en una frase")


class ResultadoAccion(_Contrato):
    accion: str
    ok: bool
    detalle: str
    id_registro: int | None = None


# ======================================================================= conocimiento
class FragmentoRecuperado(_Contrato):
    id: str
    seccion: str
    texto: str
    puntaje: float


class ResultadoConocimiento(_Contrato):
    consulta: str
    fragmentos: list[FragmentoRecuperado]


# ======================================================================= agentes: pasos y resultados
class PasoHerramienta(_Contrato):
    """Un paso de BI: llamar a una herramienta (lo elige el LLM; el código valida y ejecuta)."""
    paso: Literal["herramienta"]
    nombre: Literal["resumen_ventas", "ventas_por_producto", "tendencia_ventas", "buscar_manuales"]
    dias: int | None = Field(default=None, ge=1, le=365)
    sku: str | None = None
    consulta: str | None = None

    @model_validator(mode="after")
    def argumentos_completos(self):
        if self.nombre == "tendencia_ventas" and not self.sku:
            raise ValueError("tendencia_ventas exige un sku")
        if self.nombre == "buscar_manuales" and not self.consulta:
            raise ValueError("buscar_manuales exige una consulta")
        return self


class PasoTerminar(_Contrato):
    paso: Literal["terminar"]
    motivo: str = Field(min_length=3)


PasoBI = Annotated[Union[PasoHerramienta, PasoTerminar], Field(discriminator="paso")]


class RecomendacionProducto(_Contrato):
    """Lo que decide el LLM sobre un producto. Las cifras NO las pone el LLM: las agrega el código."""
    sku: str
    accion: Literal["incluir_en_drop", "observar", "descartar"]
    motivo: str = Field(min_length=5)


class DictamenBI(_Contrato):
    resumen: str = Field(min_length=10)
    recomendaciones: list[RecomendacionProducto] = Field(max_length=8)


class RecomendacionBI(RecomendacionProducto):
    nombre: str
    clasificacion: Literal["ganador", "prospecto"]
    unidades: int
    ventas_por_unidad_de_stock: float
    margen_pct: float
    evidencia_suficiente: bool
    ajustada_por_reglas: bool = Field(description="True si el código corrigió la acción que propuso el LLM")


class ResultadoBI(_Contrato):
    agente: Literal["bi"] = "bi"
    resumen: str
    ventas_semana: ResumenVentas | None
    recomendaciones: list[RecomendacionBI]
    limitaciones: list[str]
    pasos_usados: int


class AlertaInventario(_Contrato):
    estado: EstadoStock
    tendencia: TendenciaVentas | None
    sugerencia: SugerenciaOrden | None


class ResultadoInventario(_Contrato):
    agente: Literal["inventario"] = "inventario"
    alertas: list[AlertaInventario]
    acciones: list[CrearOrdenCompra]


class ResultadoLogistica(_Contrato):
    agente: Literal["logistica"] = "logistica"
    cola: ColaPreparacion
    envios: EnviosEnCurso
    demanda_vs_capacidad: DemandaVsCapacidad
    gestiones: list[Gestion]
    pedidos_omitidos: int = Field(ge=0, description="Pedidos con problema que no se gestionaron por el tope por consulta")
    revision_humana: list[str] = Field(description="Pedidos que el sistema no debe decidir solo")


ResultadoEspecialista = Annotated[Union[ResultadoBI, ResultadoInventario, ResultadoLogistica], Field(discriminator="agente")]


class DecisionHumana(_Contrato):
    """La respuesta del usuario en la compuerta de aprobación: qué acciones propuestas aprueba (por posición)."""
    aprobadas: list[int]
    comentario: str = ""

    @model_validator(mode="after")
    def indices_validos(self):
        if any(i < 0 for i in self.aprobadas) or len(set(self.aprobadas)) != len(self.aprobadas):
            raise ValueError("las posiciones aprobadas deben ser positivas y no repetirse")
        return self


class RespuestaFinal(_Contrato):
    texto: str = Field(min_length=10)
    origen: Literal["llm", "plantilla"] = Field(description="'plantilla' si el texto lo armó el código, sin LLM")
