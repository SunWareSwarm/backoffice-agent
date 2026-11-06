"""Inventario: un workflow de pasos fijos, sin LLM (no hay nada que juzgar: son reglas).

estado del stock -> alertas -> tendencia de cada producto en alerta -> orden de compra sugerida.
Las órdenes quedan como PROPUESTAS: solo se crean si el usuario las aprueba.
"""
from app.agents.comun import Dependencias, con_base, seguro
from app.schemas import AlertaInventario, CrearOrdenCompra, ResultadoInventario
from app.tools import inventory, sales


def _calcular(con, p) -> ResultadoInventario:
    alertas, acciones = [], []
    for e in inventory.estado_inventario(con, p):
        if not e.alertas:
            continue
        sugerencia = inventory.sugerir_orden(e, p)
        alertas.append(AlertaInventario(estado=e, tendencia=sales.tendencia_semanal(con, p, e.sku), sugerencia=sugerencia))
        if sugerencia:
            acciones.append(CrearOrdenCompra(accion="crear_orden_compra", sku=e.sku, cantidad=sugerencia.cantidad, motivo=sugerencia.motivo))
    # lo más urgente primero: menos días de cobertura (los productos sin ventas, al final)
    alertas.sort(key=lambda a: (a.estado.cobertura_dias if a.estado.cobertura_dias is not None else 1e9, a.estado.sku))
    return ResultadoInventario(alertas=alertas, acciones=acciones)


def crear_nodo_inventario(deps: Dependencias):
    @seguro("inventario")
    async def nodo(estado):
        resultado = await con_base(deps, _calcular)
        return {"resultados": [resultado.model_dump(mode="json")],
                "acciones": [a.model_dump(mode="json") for a in resultado.acciones],
                "tareas_pendientes": [t for t in estado["tareas_pendientes"] if t["agente"] != "inventario"], "pasos": 1}

    return nodo
