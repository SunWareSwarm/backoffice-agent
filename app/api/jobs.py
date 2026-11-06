"""Estado de los trabajos (jobs) en Redis.

Cada job es un "hash" de Redis (un mini-diccionario) guardado bajo la clave `job:<job_id>`.
Este módulo es la ÚNICA parte del código que sabe cómo se guarda un job: la API y el worker solo llaman a estas funciones.

Un trabajo puede pausarse varias veces (hasta 2 aclaraciones y una aprobación). Cada pausa lleva un número, y el
candado que evita que dos clientes reanuden la misma pausa a la vez es POR PAUSA: así la pausa siguiente no queda bloqueada.
"""
import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from redis.asyncio import Redis

TTL_POR_DEFECTO = 60 * 60 * 24


class JobStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_CLARIFICATION = "WAITING_CLARIFICATION"  # el grafo está pausado esperando una aclaración del usuario
    WAITING_APPROVAL = "WAITING_APPROVAL"  # el grafo está pausado esperando que el usuario apruebe acciones
    DONE = "DONE"
    FAILED = "FAILED"


# En qué campo del job se guarda lo que espera cada pausa
CAMPO_DE_PAUSA = {JobStatus.WAITING_CLARIFICATION: "clarification_request", JobStatus.WAITING_APPROVAL: "approval_request"}


def _key(job_id: str) -> str:
    return f"job:{job_id}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def create_job(redis: Redis, query: str, ttl: int = TTL_POR_DEFECTO) -> str:
    job_id = uuid.uuid4().hex
    now = _now()
    fields = {"query": query, "status": JobStatus.PENDING.value, "created_at": now, "updated_at": now}
    # Pipeline: manda los dos comandos juntos y de forma atómica (todo o nada).
    async with redis.pipeline(transaction=True) as pipe:
        pipe.hset(_key(job_id), mapping=fields)
        pipe.expire(_key(job_id), ttl)
        await pipe.execute()
    return job_id


async def update_job(redis: Redis, job_id: str, status: JobStatus | None = None, **fields: str) -> None:
    if status is not None:
        fields["status"] = status.value
    fields["updated_at"] = _now()
    await redis.hset(_key(job_id), mapping=fields)


async def get_job(redis: Redis, job_id: str) -> dict[str, str] | None:
    data = await redis.hgetall(_key(job_id))
    return data or None  # hgetall devuelve {} si la clave no existe


async def mark_waiting(redis: Redis, job_id: str, status: JobStatus, pedido: dict) -> None:
    """Deja el job esperando a un humano: guarda qué se le pide y cuenta una pausa más."""
    await redis.hincrby(_key(job_id), "pausas", 1)
    await update_job(redis, job_id, status, **{CAMPO_DE_PAUSA[status]: json.dumps(pedido, ensure_ascii=False)})


async def claim_resume(redis: Redis, job_id: str, ttl: int = TTL_POR_DEFECTO) -> bool:
    """Candado atómico por pausa: si dos clientes responden a la vez, solo el primero reanuda el grafo."""
    pausa = int(await redis.hget(_key(job_id), "pausas") or 0)
    return bool(await redis.set(f"resume-lock:{job_id}:{pausa}", "1", nx=True, ex=ttl))
