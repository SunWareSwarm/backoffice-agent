"""Worker: ejecuta el grafo en segundo plano y deja el estado del job en Redis.

El grafo puede pausarse de dos maneras (ambas con `interrupt`):
  - `aclaracion`: el orquestador necesita una aclaración  -> el job queda en WAITING_CLARIFICATION.
  - `aprobacion`: hay acciones propuestas por aprobar     -> el job queda en WAITING_APPROVAL.
Cualquier otra cosa que falle termina en FAILED con el motivo: sin esto, el cliente vería RUNNING para siempre.
"""
import asyncio
import json

from langgraph.types import Command
from redis.asyncio import Redis

from app.api.jobs import JobStatus, get_job, mark_waiting, update_job
from app.config import Settings
from app.observability import trace_config
from app.state import estado_inicial

_PAUSAS = {"aclaracion": JobStatus.WAITING_CLARIFICATION, "aprobacion": JobStatus.WAITING_APPROVAL}


def _config(job_id: str, ajustes: Settings, resumed: bool) -> dict:
    # thread_id = job_id: cada job es una "conversación" independiente del grafo, y el checkpointer guarda
    # su estado bajo ese identificador. Al reanudar se usa el MISMO thread_id: continúa donde quedó.
    return {"configurable": {"thread_id": job_id}, "recursion_limit": ajustes.recursion_limit, **trace_config(job_id, resumed)}


async def _drive(redis: Redis, graph, job_id: str, entrada, config: dict, ajustes: Settings) -> None:
    """Avanza el grafo hasta que termine o se pause, y refleja cada avance en Redis.

    Sirve para la primera ejecución (entrada = estado inicial) y para reanudar (entrada = Command(resume=...)).
    """
    pausa = None
    async with asyncio.timeout(ajustes.job_timeout_seconds):  # si se pasa, lanza TimeoutError
        # stream_mode="updates": emite lo que devuelve cada nodo, apenas termina.
        async for update in graph.astream(entrada, config=config, stream_mode="updates"):
            for nodo, salida in update.items():
                if nodo == "__interrupt__":  # el grafo se pausó en un interrupt()
                    pausa = salida[0].value
                    continue
                if isinstance(salida, dict):
                    await update_job(redis, job_id, current_node=nodo)

    valores = (await graph.aget_state(config)).values  # estado final (o de la pausa) desde el checkpointer

    if pausa is not None:
        # Una pausa no es un error ni un fin: se guarda qué se espera del humano y se libera al worker.
        estado = _PAUSAS.get(pausa.get("tipo"))
        if estado is None:
            raise RuntimeError(f"El grafo se pausó con un tipo desconocido: {pausa.get('tipo')!r}")
        await mark_waiting(redis, job_id, estado, pausa)
        return

    respuesta = valores.get("respuesta")
    if valores.get("error"):  # cierre controlado del grafo: falló algo y el error quedó a la vista
        extra = {"result": respuesta["texto"], "answer_origin": respuesta["origen"]} if respuesta else {}
        await update_job(redis, job_id, JobStatus.FAILED, error=valores["error"], steps=str(valores.get("pasos", 0)), **extra)
        return
    if not respuesta:
        raise RuntimeError("El grafo terminó sin generar una respuesta final")
    await update_job(
        redis, job_id, JobStatus.DONE, result=respuesta["texto"], answer_origin=respuesta["origen"],
        steps=str(valores.get("pasos", 0)), execution=json.dumps(valores.get("ejecucion") or [], ensure_ascii=False),
    )


def _fail_detail(exc: Exception) -> str:
    return f"{type(exc).__name__}: {str(exc) or '(sin detalle)'}"


async def run_job(redis: Redis, graph, job_id: str, ajustes: Settings) -> None:
    try:
        await update_job(redis, job_id, JobStatus.RUNNING)
        job = await get_job(redis, job_id)  # por la cola solo viaja el job_id; los datos se leen desde Redis
        await _drive(redis, graph, job_id, estado_inicial(job["query"]), _config(job_id, ajustes, resumed=False), ajustes)
    except Exception as exc:
        await update_job(redis, job_id, JobStatus.FAILED, error=_fail_detail(exc))


async def resume_job(redis: Redis, graph, job_id: str, valor, ajustes: Settings) -> None:
    """Reanuda un job pausado con lo que respondió el humano (una aclaración o una decisión de aprobación)."""
    try:
        await _drive(redis, graph, job_id, Command(resume=valor), _config(job_id, ajustes, resumed=True), ajustes)
    except Exception as exc:
        await update_job(redis, job_id, JobStatus.FAILED, error=_fail_detail(exc))
