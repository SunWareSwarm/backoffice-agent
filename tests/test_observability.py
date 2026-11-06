"""Observabilidad: nombre, etiquetas y metadatos de las trazas, configuración de LangSmith y reporte de latencia."""
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from langchain_core.tracers.run_collector import RunCollectorCallbackHandler

from app.observability import RESUME_RUN_NAME, RUN_NAME, init_observability, trace_config
from app.state import estado_inicial
from scripts.latency_report import percentile, summarize


def test_trace_config_identifica_la_ejecucion():
    cfg = trace_config("abc123")
    assert cfg["run_name"] == RUN_NAME and cfg["metadata"] == {"job_id": "abc123"} and "api" in cfg["tags"] and "resume" not in cfg["tags"]


def test_una_reanudacion_es_una_traza_raiz_aparte():
    cfg = trace_config("abc123", resumed=True)
    assert cfg["run_name"] == RESUME_RUN_NAME != RUN_NAME and "resume" in cfg["tags"]  # no se mezcla con las normales en p95 ni costo


async def test_la_traza_raiz_lleva_nombre_etiquetas_y_metadatos(grafo):
    """Con un recolector local (sin LangSmith) comprueba que el config produce la traza esperada."""
    collector = RunCollectorCallbackHandler()
    config = {"configurable": {"thread_id": "job-9"}, "recursion_limit": 40, "callbacks": [collector], **trace_config("job-9")}
    await grafo.ainvoke(estado_inicial("¿Cuántas ventas tuvimos la última semana?"), config)

    root = collector.traced_runs[0]  # solo se recolectan trazas raíz
    assert root.name == RUN_NAME and root.metadata["job_id"] == "job-9"
    assert root.metadata["thread_id"] == "job-9"  # LangChain copia el thread_id a los metadatos
    assert "proyecto-final" in root.tags


def test_init_observability_activo(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "ls-clave")
    monkeypatch.setenv("LANGSMITH_PROJECT", "mi-proyecto")
    assert init_observability(cargar_env=False) == {"enabled": True, "project": "mi-proyecto"}


def test_init_observability_sin_api_key_no_queda_activo(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert init_observability(cargar_env=False)["enabled"] is False


def test_init_observability_desactivado_usa_el_proyecto_propio(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert init_observability(cargar_env=False) == {"enabled": False, "project": "backoffice-agent"}


def test_percentil_rango_mas_cercano():
    assert percentile([1, 2, 3, 4, 5], 0.95) == 5  # con 5 datos, p95 = el máximo
    assert percentile([5, 1, 4, 2, 3], 0.50) == 3
    assert percentile(list(range(10, 101, 10)), 0.95) == 100
    with pytest.raises(ValueError):
        percentile([], 0.95)


def _run(seconds, cost, tokens=1000):
    t0 = datetime(2026, 11, 4, tzinfo=timezone.utc)
    return SimpleNamespace(id="abcdef123456", status="success", start_time=t0, end_time=t0 + timedelta(seconds=seconds),
                           total_cost=cost, total_tokens=tokens)


def test_summarize_calcula_latencia_y_costo():
    out = summarize([_run(10, 0.002), _run(12, 0.004), _run(11, 0.003), _run(30, 0.005), _run(13, 0.001)])
    assert out["n"] == 5 and out["p50_s"] == 12 and out["p95_s"] == 30
    assert out["avg_cost"] == pytest.approx(0.003) and out["runs_without_cost"] == 0


def test_summarize_avisa_cuando_falta_el_costo_y_omite_trazas_en_curso():
    en_curso = _run(5, None)
    en_curso.end_time = None
    out = summarize([_run(10, None), _run(12, 0.004), en_curso])
    assert out["n"] == 2 and out["runs_without_cost"] == 1


# ---------------------------------------------------------------- el .env debe llegar al entorno: LangSmith lo lee de ahí
def _sin_langsmith(monkeypatch):
    """Deja las variables fuera del entorno y garantiza que, al terminar la prueba, vuelvan a estar como antes."""
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, "x")  # registra el estado original...
        monkeypatch.delenv(var)  # ...y la quita; al terminar, monkeypatch deja el entorno limpio


def test_las_variables_de_langsmith_del_dotenv_llegan_al_entorno(monkeypatch, tmp_path):
    """Regresión: pydantic-settings lee el .env pero no lo vuelca al entorno; sin esto las trazas quedaban apagadas en silencio."""
    _sin_langsmith(monkeypatch)
    (tmp_path / ".env").write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=ls-del-dotenv\nLANGSMITH_PROJECT=proyecto-del-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert init_observability() == {"enabled": True, "project": "proyecto-del-dotenv"}
    assert os.environ["LANGSMITH_API_KEY"] == "ls-del-dotenv"


def test_el_dotenv_no_pisa_lo_que_ya_esta_definido_en_el_entorno(monkeypatch, tmp_path):
    _sin_langsmith(monkeypatch)
    (tmp_path / ".env").write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=ls-del-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")  # lo que hace conftest para que las pruebas nunca envíen trazas
    assert init_observability()["enabled"] is False


def test_sin_dotenv_no_falla(monkeypatch, tmp_path):
    _sin_langsmith(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert init_observability()["enabled"] is False
