"""State del grafo: la carpeta compartida que viaja entre los nodos.

Todo se guarda como diccionarios simples (cada nodo valida con los contratos de schemas.py al leer y al
escribir): así el checkpointer los serializa sin depender de clases propias, y los contratos siguen siendo
la única definición de la forma de los datos.

Los campos con reducer (`operator.add`) se acumulan: cada nodo agrega su parte sin pisar las demás.
"""
import operator
from typing import Annotated, TypedDict


class Estado(TypedDict, total=False):
    solicitud: dict  # Solicitud: lo que pidió el usuario (se amplía con las aclaraciones)
    ruta: dict | None  # DecisionRuta: lo que decidió el orquestador
    tareas_pendientes: list[dict]  # Tarea: los especialistas que faltan, en orden fijo
    resultados: Annotated[list[dict], operator.add]  # ResultadoEspecialista, uno por especialista
    acciones: Annotated[list[dict], operator.add]  # AccionPropuesta: todo lo que espera aprobación
    decision_humana: dict | None  # DecisionHumana
    ejecucion: list[dict] | None  # ResultadoAccion: qué pasó con lo aprobado
    aclaraciones: Annotated[int, operator.add]  # turnos de aclaración usados (máximo 2)
    pasos: Annotated[int, operator.add]  # nodos ejecutados: sirve para el límite y para evaluar
    error: str | None  # si algo falló: el grafo cierra de forma controlada
    respuesta: dict | None  # RespuestaFinal


def estado_inicial(texto: str) -> Estado:
    from app.schemas import Solicitud

    return {"solicitud": Solicitud(texto=texto).model_dump(), "ruta": None, "tareas_pendientes": [], "resultados": [],
            "acciones": [], "decision_humana": None, "ejecucion": None, "aclaraciones": 0, "pasos": 0, "error": None,
            "respuesta": None}
