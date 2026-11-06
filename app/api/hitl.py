"""Los dos puntos donde el sistema espera a un humano: la aclaración y la aprobación.

  POST /tasks/{id}/clarify   responde la pregunta aclaratoria del orquestador (máximo 2 veces por consulta).
  POST /tasks/{id}/approve   aprueba, por posición, las acciones propuestas (orden de compra, aviso, compensación,
                             cancelación). Lo que no se aprueba no se ejecuta. Las acciones se vuelven a validar al ejecutarse.

Las dos reanudan el grafo con el MISMO thread_id (= job_id) y con un candado atómico por pausa: si dos clientes
responden a la vez, solo el primero reanuda; el segundo recibe 409.
"""
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.api import jobs
from app.api.deps import AjustesDep, GraphDep, RedisDep
from app.api.jobs import JobStatus
from app.api.models import ApprovalBody, ClarifyBody, TaskAccepted
from app.api.worker import resume_job
from app.schemas import DecisionHumana

router = APIRouter()


async def _job_en_pausa(redis, job_id: str, esperado: JobStatus) -> dict:
    job = await jobs.get_job(redis, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job no encontrado (o expirado)")
    if job["status"] != esperado:
        raise HTTPException(status_code=409, detail=f"El job está en {job['status']}, no en {esperado.value}")
    return job


async def _reanudar(redis, graph, ajustes, background: BackgroundTasks, job_id: str, valor) -> TaskAccepted:
    if not await jobs.claim_resume(redis, job_id, ajustes.job_ttl_seconds):
        raise HTTPException(status_code=409, detail="Esta pausa ya recibió una respuesta")
    await jobs.update_job(redis, job_id, JobStatus.RUNNING)  # el polling ya ve que avanzó
    background.add_task(resume_job, redis, graph, job_id, valor, ajustes)
    return TaskAccepted(job_id=job_id, status=JobStatus.RUNNING)


@router.post("/tasks/{job_id}/clarify", status_code=202, response_model=TaskAccepted)
async def clarify_task(job_id: str, body: ClarifyBody, background: BackgroundTasks, redis: RedisDep, graph: GraphDep, ajustes: AjustesDep):
    await _job_en_pausa(redis, job_id, JobStatus.WAITING_CLARIFICATION)
    return await _reanudar(redis, graph, ajustes, background, job_id, body.texto)


@router.post("/tasks/{job_id}/approve", status_code=202, response_model=TaskAccepted)
async def approve_task(job_id: str, body: ApprovalBody, background: BackgroundTasks, redis: RedisDep, graph: GraphDep, ajustes: AjustesDep):
    job = await _job_en_pausa(redis, job_id, JobStatus.WAITING_APPROVAL)
    propuestas = len(json.loads(job["approval_request"])["acciones"])
    aprobadas = list(range(propuestas)) if body.aprobar_todo else body.aprobadas
    if any(i < 0 or i >= propuestas for i in aprobadas):
        raise HTTPException(status_code=422, detail=f"Posiciones válidas: 0 a {propuestas - 1}")
    try:
        decision = DecisionHumana(aprobadas=aprobadas, comentario=body.comentario)
    except ValidationError as e:  # red de seguridad: lo que el contrato interno rechace es un error del cliente, no un 500
        raise HTTPException(status_code=422, detail=str(e.errors()[0]["msg"])) from e
    return await _reanudar(redis, graph, ajustes, background, job_id, decision.model_dump())
