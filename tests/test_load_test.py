"""El script de carga, contra la API real (con modelos falsos y Redis simulado)."""
import httpx

from scripts.load_test import PAUSADO, QUERIES, run_load_test, run_one


async def test_el_script_de_carga_completa_las_cinco_consultas_concurrentes(client_app):
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        results = await run_load_test(c, QUERIES, poll=0.01, timeout=10)
    assert len(results) == 5
    assert all(r["status"] == "DONE" for r in results), [(r["status"], r["detalle"], r["query"]) for r in results]
    assert len({r["job_id"] for r in results}) == 5  # cada petición tuvo su propio trabajo
    assert all(r["origen"] == "llm" for r in results)


async def test_las_consultas_de_la_prueba_no_pausan_nunca(client_app):
    """Si una consulta pidiera aprobación o aclaración, ensuciaría la medición de costo y latencia."""
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        results = await run_load_test(c, QUERIES, poll=0.01, timeout=10)
    assert not any(r["detalle"] in PAUSADO for r in results)


async def test_un_trabajo_pausado_se_informa_como_pausado_y_no_cuelga_el_script(client_app):
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await run_one(c, "revisa el inventario", poll=0.01, timeout=5)
    assert r["status"] == "PAUSADO" and r["detalle"] == "WAITING_APPROVAL"


async def test_un_trabajo_que_falla_se_informa_con_su_error(client_app):
    transport = httpx.ASGITransport(app=client_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        r = await run_one(c, "esto falla seguro", poll=0.01, timeout=5)
    assert r["status"] == "FAILED" and r["error"].startswith("orquestador:")
