"""La API por HTTP, con el grafo real, modelos falsos y Redis simulado.

Nota: en las pruebas el worker ya terminó cuando vuelve la respuesta (ASGITransport espera las tareas de fondo).
"""
import asyncio
import json
import sqlite3

import httpx
import pytest


def cliente(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def crear(c, query):
    r = await c.post("/tasks", json={"query": query})
    assert r.status_code == 202, r.text
    return r.json()["job_id"]


async def estado(c, job_id):
    r = await c.get(f"/tasks/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()


def contar(bd, tabla, **donde):
    con = sqlite3.connect(bd)
    cond = " AND ".join(f"{k} = ?" for k in donde) or "1=1"
    try:
        return con.execute(f"SELECT COUNT(*) FROM {tabla} WHERE {cond}", tuple(donde.values())).fetchone()[0]
    finally:
        con.close()


# ================================================================ el patrón ticket y consulta
async def test_post_devuelve_202_y_un_job_id_al_instante(client_app):
    async with cliente(client_app) as c:
        r = await c.post("/tasks", json={"query": "¿Cuántas ventas tuvimos la última semana?"})
        assert r.status_code == 202 and r.json()["status"] == "PENDING" and r.json()["job_id"]


async def test_una_consulta_de_ventas_termina_sin_pausas(client_app):
    async with cliente(client_app) as c:
        s = await estado(c, await crear(c, "¿Cuántas ventas tuvimos la última semana?"))
    assert s["status"] == "DONE" and s["result"] and s["answer_origin"] == "llm" and s["error"] is None
    assert s["current_node"] == "redactor" and s["steps"] >= 3
    assert s["approval_request"] is None and s["clarification_request"] is None and s["execution"] == []


async def test_job_inexistente_da_404(client_app):
    async with cliente(client_app) as c:
        assert (await c.get("/tasks/no-existe")).status_code == 404
        assert (await c.post("/tasks/no-existe/approve", json={"aprobar_todo": True})).status_code == 404
        assert (await c.post("/tasks/no-existe/clarify", json={"texto": "stock"})).status_code == 404


@pytest.mark.parametrize("cuerpo", [{}, {"query": "x"}, {"query": "   "}, {"query": "a" * 501}, {"query": "hola mundo", "otro": 1}])
async def test_cuerpos_invalidos_dan_422(client_app, cuerpo):
    async with cliente(client_app) as c:
        assert (await c.post("/tasks", json=cuerpo)).status_code == 422


async def test_health_informa_redis_y_rag(client_app):
    async with cliente(client_app) as c:
        r = await c.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok" and "rag_disponible" in r.json()


# ================================================================ la pausa de aprobación
async def test_aprobar_ejecuta_solo_lo_aprobado(client_app, ajustes):
    async with cliente(client_app) as c:
        job = await crear(c, "¿Qué productos deberíamos ordenar del inventario?")
        s = await estado(c, job)
        assert s["status"] == "WAITING_APPROVAL" and s["current_node"] == "inventario"
        propuestas = s["approval_request"]["acciones"]
        assert s["approval_request"]["tipo"] == "aprobacion" and len(propuestas) >= 1 and s["result"] is None
        assert contar(ajustes.sqlite_path, "ordenes_compra") == 7  # nada se ejecutó todavía

        pos = next(a["posicion"] for a in propuestas if a["sku"] == "BAG-001")
        r = await c.post(f"/tasks/{job}/approve", json={"aprobadas": [pos], "comentario": "solo esta"})
        assert r.status_code == 202 and r.json()["status"] == "RUNNING"
        s = await estado(c, job)
    assert s["status"] == "DONE" and s["approval_request"] is None
    assert [e["ok"] for e in s["execution"]] == [True] and "BAG-001" in s["execution"][0]["detalle"]
    assert contar(ajustes.sqlite_path, "ordenes_compra") == 8 and contar(ajustes.sqlite_path, "ordenes_compra", sku="BAG-001", estado="creada") == 1


async def test_aprobar_todo_y_no_aprobar_nada(client_app, ajustes):
    async with cliente(client_app) as c:
        a = await crear(c, "revisa el inventario")
        r = await c.post(f"/tasks/{a}/approve", json={"aprobar_todo": True})
        assert r.status_code == 202
        sa = await estado(c, a)
        b = await crear(c, "revisa el inventario otra vez")
        await c.post(f"/tasks/{b}/approve", json={"aprobadas": []})
        sb = await estado(c, b)
    assert sa["status"] == "DONE" and len(sa["execution"]) >= 2 and all(e["ok"] for e in sa["execution"])
    assert sb["status"] == "DONE" and sb["execution"] == []
    assert contar(ajustes.sqlite_path, "ordenes_compra") == 7 + len(sa["execution"])  # solo las de A


async def test_una_decision_se_acepta_una_sola_vez(client_app):
    async with cliente(client_app) as c:
        job = await crear(c, "revisa el inventario")
        assert (await c.post(f"/tasks/{job}/approve", json={"aprobadas": [0]})).status_code == 202
        segunda = await c.post(f"/tasks/{job}/approve", json={"aprobadas": [0]})
        assert segunda.status_code == 409  # el job ya terminó: no espera aprobación


async def test_dos_clientes_que_responden_a_la_vez_no_reanudan_dos_veces(client_app, redis):
    async with cliente(client_app) as c:
        job = await crear(c, "revisa el inventario")
        # cierra la ventana entre la lectura del estado y el candado: ambos ven WAITING_APPROVAL
        from app.api import jobs
        assert await jobs.claim_resume(redis, job) is True
        assert await jobs.claim_resume(redis, job) is False  # el segundo no pasa
        r = await c.post(f"/tasks/{job}/approve", json={"aprobadas": [0]})
        assert r.status_code == 409 and "ya recibió" in r.json()["detail"]


@pytest.mark.parametrize("cuerpo,codigo", [
    ({"aprobadas": [999]}, 422), ({"aprobadas": [-1]}, 422), ({"aprobadas": [0, 0]}, 422),
    ({"aprobar_todo": True, "aprobadas": [0]}, 422), ({"aprobar": True}, 422),
])
async def test_decisiones_de_aprobacion_invalidas(client_app, cuerpo, codigo):
    async with cliente(client_app) as c:
        job = await crear(c, "revisa el inventario")
        assert (await c.post(f"/tasks/{job}/approve", json=cuerpo)).status_code == codigo
        assert (await estado(c, job))["status"] == "WAITING_APPROVAL"  # una decisión inválida no mueve el job


async def test_aprobar_un_job_que_no_espera_aprobacion_da_409(client_app):
    async with cliente(client_app) as c:
        job = await crear(c, "¿Cuántas ventas tuvimos?")
        assert (await c.post(f"/tasks/{job}/approve", json={"aprobar_todo": True})).status_code == 409
        assert (await c.post(f"/tasks/{job}/clarify", json={"texto": "stock"})).status_code == 409


# ================================================================ la pausa de aclaración
async def test_una_consulta_ambigua_pregunta_y_sigue_con_la_respuesta(client_app, ajustes):
    async with cliente(client_app) as c:
        job = await crear(c, "¿Cómo vamos?")
        s = await estado(c, job)
        assert s["status"] == "WAITING_CLARIFICATION" and s["approval_request"] is None
        assert s["clarification_request"] == {"tipo": "aclaracion", "pregunta": "¿Qué quieres revisar: ventas, inventario o pedidos?", "intento": 1, "maximo": 2}

        r = await c.post(f"/tasks/{job}/clarify", json={"texto": "el inventario"})
        assert r.status_code == 202
        s = await estado(c, job)
        assert s["status"] == "WAITING_APPROVAL" and s["clarification_request"] is None  # la segunda pausa, de otro tipo
        assert (await c.post(f"/tasks/{job}/approve", json={"aprobar_todo": True})).status_code == 202  # el candado es por pausa
        s = await estado(c, job)
    assert s["status"] == "DONE" and s["execution"]


@pytest.mark.parametrize("cuerpo", [{}, {"texto": ""}, {"texto": "   "}, {"texto": "x" * 501}])
async def test_aclaraciones_invalidas_dan_422(client_app, cuerpo):
    async with cliente(client_app) as c:
        job = await crear(c, "¿Cómo vamos?")
        assert (await c.post(f"/tasks/{job}/clarify", json=cuerpo)).status_code == 422


async def test_fuera_de_alcance_responde_sin_pausas(client_app):
    async with cliente(client_app) as c:
        s = await estado(c, await crear(c, "cuéntame un chiste"))
    assert s["status"] == "DONE" and "fuera de lo que puedo hacer" in s["result"] and s["answer_origin"] == "plantilla"


# ================================================================ los fallos terminan en FAILED, nunca en un polling eterno
async def test_un_cierre_controlado_del_grafo_deja_el_job_en_failed_con_el_motivo(client_app):
    async with cliente(client_app) as c:
        s = await estado(c, await crear(c, "esto falla seguro"))
    assert s["status"] == "FAILED" and s["error"].startswith("orquestador: SalidaInvalida")
    assert "problema interno" in s["result"] and s["current_node"] == "cierre"


async def test_una_excepcion_dentro_del_worker_deja_el_job_en_failed(client_app, ajustes):
    class GrafoRoto:
        async def astream(self, *a, **k):
            raise ConnectionError("Redis se cayó")
            yield  # pragma: no cover

    from app.api.main import get_graph
    client_app.dependency_overrides[get_graph] = lambda: GrafoRoto()
    async with cliente(client_app) as c:
        s = await estado(c, await crear(c, "hola mundo"))
    assert s["status"] == "FAILED" and s["error"] == "ConnectionError: Redis se cayó"


async def test_una_ejecucion_demasiado_larga_se_marca_failed(client_app, env):
    class GrafoLento:
        async def astream(self, *a, **k):
            await asyncio.sleep(5)
            yield {}

    from app.api.main import get_ajustes, get_graph
    from app.config import Settings
    env.setenv("JOB_TIMEOUT_SECONDS", "0.1")
    client_app.dependency_overrides[get_graph] = lambda: GrafoLento()
    client_app.dependency_overrides[get_ajustes] = lambda: Settings(_env_file=None)
    async with cliente(client_app) as c:
        s = await estado(c, await crear(c, "hola mundo"))
    assert s["status"] == "FAILED" and s["error"].startswith("TimeoutError")


async def test_un_error_al_reanudar_tambien_deja_el_job_en_failed(client_app, redis):
    async with cliente(client_app) as c:
        job = await crear(c, "¿Cómo vamos?")
        from app.api.main import get_graph

        class GrafoQueFallaAlReanudar:
            async def astream(self, *a, **k):
                raise RuntimeError("falló al reanudar")
                yield  # pragma: no cover

        client_app.dependency_overrides[get_graph] = lambda: GrafoQueFallaAlReanudar()
        assert (await c.post(f"/tasks/{job}/clarify", json={"texto": "stock"})).status_code == 202
        s = await estado(c, job)
    assert s["status"] == "FAILED" and "falló al reanudar" in s["error"]


# ================================================================ concurrencia
async def test_cinco_consultas_a_la_vez_no_se_mezclan(client_app):
    consultas = ["¿Cuántas ventas tuvimos la última semana?", "revisa el inventario", "cuéntame un chiste",
                 "¿Qué productos son tendencia en ventas?", "¿Cómo vamos?"]
    async with cliente(client_app) as c:
        jobs = await asyncio.gather(*(crear(c, q) for q in consultas))
        estados = await asyncio.gather(*(estado(c, j) for j in jobs))
    assert len(set(jobs)) == 5
    assert [s["status"] for s in estados] == ["DONE", "WAITING_APPROVAL", "DONE", "DONE", "WAITING_CLARIFICATION"]
    assert [s["query"] for s in estados] == consultas


# ================================================================ el arranque falla pronto y con un mensaje útil
async def test_sin_la_base_simulada_la_api_no_arranca_y_explica_que_hacer(env, tmp_path):
    from app.api.main import app, lifespan
    env.chdir(tmp_path)  # sin .env a la vista: el arranque carga el .env al entorno y no debe tocar el de quien corre las pruebas
    env.setenv("SQLITE_PATH", str(tmp_path / "no_existe.db"))
    with pytest.raises(RuntimeError, match="python -m scripts.seed_db"):
        async with lifespan(app):
            pass  # pragma: no cover


async def test_sin_el_env_completo_la_api_no_arranca(monkeypatch, tmp_path):
    from pydantic import ValidationError
    from app import config
    from app.api.main import app, lifespan
    for var in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # sin .env a la vista
    config.get_settings.cache_clear()
    with pytest.raises(ValidationError):
        async with lifespan(app):
            pass  # pragma: no cover
