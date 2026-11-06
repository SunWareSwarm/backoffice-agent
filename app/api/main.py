"""API REST: recibe consultas, las encola y permite consultar su estado. No ejecuta nada pesado.

    uvicorn app.api.main:app --reload

POST /tasks → 202 + job_id al instante · GET /tasks/{id} → estado, resultado o lo que espera de ti ·
POST /tasks/{id}/clarify · POST /tasks/{id}/approve (ver hitl.py) · GET /health.
"""
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from redis.asyncio import Redis

from app.agents.comun import ChatResiliente, Dependencias
from app.api import jobs
from app.api.deps import AjustesDep, GraphDep, RedisDep, get_ajustes, get_graph, get_redis  # noqa: F401 (los tests los reemplazan)
from app.api.hitl import router as hitl_router
from app.api.jobs import JobStatus
from app.api.models import TaskAccepted, TaskCreate, TaskStatus
from app.api.worker import run_job
from app.clients import AsyncLLMManager, ClienteConRespaldo, ModelConfig
from app.config import get_settings
from app.graph import construir_grafo
from app.llm import get_chat_model
from app.observability import flush_traces, init_observability
from app.rag.retriever import ErrorRecuperacion, Retriever

log = logging.getLogger("uvicorn.error")
CAMPOS_JSON = ("approval_request", "clarification_request", "execution")  # en Redis se guardan como texto JSON


@asynccontextmanager
async def lifespan(app: FastAPI):
    ajustes = get_settings()  # falla de inmediato y con claridad si falta algo en el .env
    app.state.settings = ajustes
    init_observability()  # informa en el log si LangSmith quedó activo

    if not Path(ajustes.sqlite_path).exists():
        raise RuntimeError(f"No existe la base simulada en {ajustes.sqlite_path}. Créala con: python -m scripts.seed_db")

    # ARRANQUE 1: conexión a Redis para el estado de los jobs.
    app.state.redis = Redis.from_url(ajustes.redis_url, decode_responses=True)
    await app.state.redis.ping()  # si Redis no está disponible, falla de inmediato y con claridad

    # ARRANQUE 2: los manuales (RAG). Si todavía no se hizo la ingesta, el sistema funciona igual, sin los manuales.
    try:
        retriever = await Retriever.abrir(ajustes)
    except ErrorRecuperacion as e:
        log.warning("RAG no disponible, el sistema seguirá sin los manuales: %s", e)
        retriever = None
    app.state.rag_disponible = retriever is not None

    # ARRANQUE 3: las dependencias del grafo. Texto libre con poca temperatura: los avisos y la respuesta deben ser estables.
    # Resiliencia: ante un 503 (modelo saturado) o un 429 (cuota) se reintenta con espera creciente y, si sigue igual, se usa el
    # modelo de respaldo (LLM_FALLBACK_MODEL, opcional). Esto cubre al modelo de chat y al cliente de texto.
    respaldo = ajustes.llm_fallback_model
    chat = ChatResiliente(
        get_chat_model(ajustes), get_chat_model(ajustes, modelo=respaldo) if respaldo else None,
        reintentos=ajustes.llm_max_retries, espera_base=ajustes.llm_retry_base_seconds,
    )
    log.info("Modelo de chat: %s | respaldo: %s", ajustes.llm_model, respaldo or "ninguno")
    deps = Dependencias(
        ruta_db=ajustes.sqlite_path, chat=chat, texto=ClienteConRespaldo(AsyncLLMManager.get_client(ajustes), respaldo),
        config_texto=ModelConfig.from_settings(ajustes, temperature=0.3, max_tokens=1500), retriever=retriever,
    )

    # ARRANQUE 4: checkpointer de LangGraph sobre Redis + grafo compilado (una sola vez).
    # `async with` mantiene abierta la conexión del checkpointer mientras la API vive.
    # Al entrar, crea los índices que necesita en Redis (requiere redis-stack).
    async with AsyncRedisSaver.from_conn_string(ajustes.redis_url) as checkpointer:
        app.state.graph = construir_grafo(deps, checkpointer)
        yield  # <- aquí la API queda funcionando
    # APAGADO: el `async with` ya cerró el checkpointer; falta cerrar nuestra conexión.
    await app.state.redis.aclose()
    flush_traces()  # envía a LangSmith las trazas que aún estén en cola


app = FastAPI(title="API del agente de back office", lifespan=lifespan)
app.include_router(hitl_router)  # POST /tasks/{id}/clarify y /approve


@app.post("/tasks", status_code=202, response_model=TaskAccepted)
async def create_task(body: TaskCreate, background: BackgroundTasks, redis: RedisDep, graph: GraphDep, ajustes: AjustesDep):
    job_id = await jobs.create_job(redis, body.query, ajustes.job_ttl_seconds)
    background.add_task(run_job, redis, graph, job_id, ajustes)  # se ejecuta DESPUÉS de enviar la respuesta
    return TaskAccepted(job_id=job_id, status=JobStatus.PENDING)


@app.get("/tasks/{job_id}", response_model=TaskStatus)
async def read_task(job_id: str, redis: RedisDep):
    job = await jobs.get_job(redis, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado (o expirado)")
    job.pop("pausas", None)  # contador interno de pausas
    for campo in CAMPOS_JSON:
        if campo in job:
            job[campo] = json.loads(job[campo])
    # Una petición pendiente solo es relevante mientras el job la espera.
    if job["status"] != JobStatus.WAITING_APPROVAL:
        job.pop("approval_request", None)
    if job["status"] != JobStatus.WAITING_CLARIFICATION:
        job.pop("clarification_request", None)
    return TaskStatus(job_id=job_id, **job)


@app.get("/health")
async def health(redis: RedisDep):
    await redis.ping()
    return {"status": "ok", "rag_disponible": getattr(app.state, "rag_disponible", False)}
