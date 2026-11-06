"""BI y estrategia: el único especialista con ruta abierta (un agente de verdad).

El LLM decide qué herramienta llamar en cada paso (hasta `max_pasos_bi`) y luego dictamina. Pero:
  - las cifras NUNCA las pone el LLM: el código las agrega desde las herramientas;
  - las reglas del negocio las vuelve a aplicar el código: un producto con menos de 15 ventas o con menos
    de 4 semanas de historial no puede quedar como «incluir en el drop», aunque el LLM lo proponga.
"""
from pydantic import TypeAdapter

from app.agents.comun import Dependencias, a_json, con_base, pedir_json, seguro
from app.agents.prompts import BI_DICTAMEN, BI_PASO
from app.schemas import DictamenBI, PasoBI, PasoHerramienta, RecomendacionBI, ResultadoBI, ResumenVentas, VentasProducto
from app.tools import sales
from app.tools.knowledge import buscar_en_manuales

_PASO = TypeAdapter(PasoBI)


class _Datos:
    def __init__(self):
        self.ventas_semana: ResumenVentas | None = None
        self.productos: dict[str, VentasProducto] = {}
        self.observaciones: list[str] = []


def _compacto(v: VentasProducto) -> dict:
    return {"sku": v.sku, "nombre": v.nombre, "categoria": v.categoria, "unidades": v.unidades,
            "ventas_por_unidad_de_stock": v.ventas_por_unidad_de_stock, "margen_pct": v.margen_pct,
            "clasificacion": v.clasificacion, "evidencia_suficiente": v.evidencia_suficiente,
            "semanas_de_historial": v.semanas_de_historial}


async def _ejecutar(deps: Dependencias, paso: PasoHerramienta, datos: _Datos) -> str:
    """Corre la herramienta que pidió el LLM y devuelve lo que verá como observación."""
    if paso.nombre == "resumen_ventas":
        r = await con_base(deps, lambda con, p: sales.resumen_ventas(con, p, paso.dias or 7))
        datos.ventas_semana = r
        return a_json(r.model_dump(mode="json"))
    if paso.nombre == "ventas_por_producto":
        lista = await con_base(deps, lambda con, p: sales.ventas_por_producto(con, p, paso.dias or 84))
        datos.productos.update({v.sku: v for v in lista})
        return a_json([_compacto(v) for v in lista[:12]])  # los 12 con mejores ventas por unidad de stock
    if paso.nombre == "tendencia_ventas":
        if not await con_base(deps, lambda con, p: con.execute("SELECT 1 FROM productos WHERE sku = ?", (paso.sku,)).fetchone()):
            return f"Error: el producto {paso.sku} no existe"
        t = await con_base(deps, lambda con, p: sales.tendencia_semanal(con, p, paso.sku))
        return a_json(t.model_dump(mode="json"))
    if not deps.retriever:  # buscar_manuales
        return "No hay manuales disponibles"
    r = await buscar_en_manuales(deps.retriever, paso.consulta, k=3)
    return a_json([{"id": f.id, "seccion": f.seccion, "texto": f.texto[:400]} for f in r.fragmentos])


def _contexto(consulta: str, tarea: dict, datos: _Datos, manual: str, final: bool = False) -> str:
    partes = [f"Consulta del usuario: {consulta}", f"Tu tarea: {tarea['instruccion']}"]
    if manual:
        partes.append(f"Criterios de la guía de marca:\n{manual}")
    partes.append("Observaciones hasta ahora:\n" + ("\n\n".join(datos.observaciones) if datos.observaciones else "(ninguna todavía)"))
    partes.append("Redacta ahora tu dictamen." if final else "Decide el siguiente paso.")
    return "\n\n".join(partes)


async def _armar(deps: Dependencias, dictamen: DictamenBI, datos: _Datos, pasos: int) -> ResultadoBI:
    """Convierte el dictamen del LLM en un resultado: agrega las cifras y aplica las reglas del negocio."""
    if not datos.productos:  # el LLM nunca pidió los productos: se necesitan para poner las cifras
        datos.productos.update({v.sku: v for v in await con_base(deps, lambda con, p: sales.ventas_por_producto(con, p))})
    recomendaciones, limitaciones, vistos = [], [], set()
    for r in dictamen.recomendaciones:
        if r.sku in vistos:
            continue
        vistos.add(r.sku)
        v = datos.productos.get(r.sku)
        if v is None:
            limitaciones.append(f"{r.sku}: el producto no existe; se descartó la recomendación")
            continue
        accion, ajustada = r.accion, False
        if accion == "incluir_en_drop" and (v.clasificacion != "ganador" or not v.evidencia_suficiente):
            accion, ajustada = "observar", True
            limitaciones.append(f"{v.sku}: {v.unidades} ventas y {v.semanas_de_historial} semanas de historial; no se incluye como ganador")
        recomendaciones.append(RecomendacionBI(
            sku=v.sku, accion=accion, motivo=r.motivo, nombre=v.nombre, clasificacion=v.clasificacion, unidades=v.unidades,
            ventas_por_unidad_de_stock=v.ventas_por_unidad_de_stock, margen_pct=v.margen_pct,
            evidencia_suficiente=v.evidencia_suficiente, ajustada_por_reglas=ajustada))
    if not recomendaciones:
        limitaciones.append("No hay recomendaciones de productos con evidencia suficiente")
    return ResultadoBI(resumen=dictamen.resumen, ventas_semana=datos.ventas_semana, recomendaciones=recomendaciones,
                       limitaciones=limitaciones, pasos_usados=pasos)


def crear_nodo_bi(deps: Dependencias):
    @seguro("bi")
    async def nodo(estado):
        tarea = next(t for t in estado["tareas_pendientes"] if t["agente"] == "bi")
        consulta, datos, usados, manual = estado["solicitud"]["texto"], _Datos(), 0, ""
        if deps.retriever:
            f = await buscar_en_manuales(deps.retriever, "criterios para elegir los productos de un drop", k=3)
            manual = "\n".join(f"- {x.texto[:350]}" for x in f.fragmentos)
        for _ in range(deps.max_pasos_bi):
            paso = await pedir_json(deps.chat, _PASO, BI_PASO, _contexto(consulta, tarea, datos, manual))
            if paso.paso == "terminar":
                break
            usados += 1
            datos.observaciones.append(f"[{paso.nombre}] {await _ejecutar(deps, paso, datos)}")
        dictamen = await pedir_json(deps.chat, DictamenBI, BI_DICTAMEN, _contexto(consulta, tarea, datos, manual, final=True))
        resultado = await _armar(deps, dictamen, datos, usados)
        return {"resultados": [resultado.model_dump(mode="json")], "pasos": 1,
                "tareas_pendientes": [t for t in estado["tareas_pendientes"] if t is not tarea]}

    return nodo
