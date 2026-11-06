"""Estado de los trabajos en Redis: ciclo de vida, expiración y el candado POR PAUSA."""
import json

from app.api import jobs
from app.api.jobs import JobStatus


async def test_un_job_nace_pendiente_y_expira_solo(redis):
    job = await jobs.create_job(redis, "consulta de prueba", ttl=120)
    datos = await jobs.get_job(redis, job)
    assert datos["status"] == "PENDING" and datos["query"] == "consulta de prueba" and datos["created_at"] == datos["updated_at"]
    assert 0 < await redis.ttl(f"job:{job}") <= 120


async def test_actualizar_cambia_el_estado_y_la_fecha(redis):
    job = await jobs.create_job(redis, "consulta")
    await jobs.update_job(redis, job, JobStatus.RUNNING, current_node="orquestador")
    datos = await jobs.get_job(redis, job)
    assert datos["status"] == "RUNNING" and datos["current_node"] == "orquestador" and datos["updated_at"] >= datos["created_at"]
    assert await jobs.get_job(redis, "no-existe") is None


async def test_dejar_esperando_guarda_lo_que_se_espera_y_cuenta_la_pausa(redis):
    job = await jobs.create_job(redis, "consulta")
    await jobs.mark_waiting(redis, job, JobStatus.WAITING_CLARIFICATION, {"tipo": "aclaracion", "pregunta": "¿Qué quieres revisar?"})
    datos = await jobs.get_job(redis, job)
    assert datos["status"] == "WAITING_CLARIFICATION" and datos["pausas"] == "1"
    assert json.loads(datos["clarification_request"])["pregunta"] == "¿Qué quieres revisar?"  # tildes y signos intactos
    await jobs.mark_waiting(redis, job, JobStatus.WAITING_APPROVAL, {"tipo": "aprobacion", "acciones": []})
    assert (await jobs.get_job(redis, job))["pausas"] == "2"


async def test_el_candado_es_por_pausa_asi_la_pausa_siguiente_no_queda_bloqueada(redis):
    job = await jobs.create_job(redis, "consulta")
    await jobs.mark_waiting(redis, job, JobStatus.WAITING_CLARIFICATION, {"tipo": "aclaracion"})
    assert await jobs.claim_resume(redis, job) is True
    assert await jobs.claim_resume(redis, job) is False  # la misma pausa no se reanuda dos veces
    await jobs.mark_waiting(redis, job, JobStatus.WAITING_APPROVAL, {"tipo": "aprobacion", "acciones": []})
    assert await jobs.claim_resume(redis, job) is True  # una pausa nueva tiene su propio candado
    assert await jobs.claim_resume(redis, job) is False


async def test_los_candados_de_distintos_jobs_no_se_pisan(redis):
    a, b = await jobs.create_job(redis, "uno"), await jobs.create_job(redis, "dos")
    assert await jobs.claim_resume(redis, a) is True and await jobs.claim_resume(redis, b) is True
