import os

# Los tests NUNCA deben enviar trazas a LangSmith (ensuciarían tu proyecto), aunque tu .env las active.
# Debe ir antes de importar la app, porque LangSmith lee esta variable al primer uso.
os.environ["LANGSMITH_TRACING"] = "false"

import pytest

from app.config import get_settings

BASE_ENV = {
    "LLM_PROVIDER": "google",
    "LLM_MODEL": "modelo-de-prueba",
    "EMBEDDING_PROVIDER": "google",
    "EMBEDDING_MODEL": "embedding-de-prueba",
    "GOOGLE_API_KEY": "clave-falsa",
}


@pytest.fixture
def env(monkeypatch):
    """Variables de entorno de prueba, sin leer ningún .env real."""
    for k in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS",
              "GOOGLE_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "RAG_TOP_K"):
        monkeypatch.delenv(k, raising=False)
    for k, v in BASE_ENV.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


# ---------------------------------------------------------------- base simulada compartida por las pruebas de herramientas
import shutil
from datetime import date

from app.db.connection import conectar
from app.db.seed import generar

HOY = date(2026, 11, 3)


@pytest.fixture(scope="session")
def bd_base(tmp_path_factory):
    """La tienda simulada con reloj fijo. Solo lectura: las pruebas que escriben usan `bd`."""
    ruta = tmp_path_factory.mktemp("tienda") / "base.db"
    generar(ruta, HOY, 42)
    return ruta


@pytest.fixture
def bd(bd_base, tmp_path):
    """Una copia de la base para cada prueba que escribe."""
    copia = tmp_path / "copia.db"
    shutil.copy(bd_base, copia)
    return copia


@pytest.fixture(scope="session")
def con_ro(bd_base):
    c = conectar(bd_base)
    yield c
    c.close()


@pytest.fixture(scope="session")
def params(con_ro):
    from app.tools.comun import cargar_parametros
    return cargar_parametros(con_ro)



# ---------------------------------------------------------------- API: Redis simulado y grafo real con modelos falsos
import fakeredis
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.comun import Dependencias
from app.clients import ModelConfig
from app.config import Settings
from app.graph import construir_grafo
from tests.fakes import ChatFalso, TextoFalso, chat_negocio, texto_negocio


@pytest.fixture
async def redis():
    client = fakeredis.FakeAsyncRedis(decode_responses=True)  # Redis simulado en memoria
    yield client
    await client.aclose()


@pytest.fixture
def ajustes(env, bd):
    """Configuración de prueba: la base es una copia desechable y los tiempos son cortos."""
    env.setenv("SQLITE_PATH", str(bd))
    env.setenv("JOB_TIMEOUT_SECONDS", "20")
    return Settings(_env_file=None)


@pytest.fixture
def grafo(ajustes):
    deps = Dependencias(ruta_db=ajustes.sqlite_path, chat=ChatFalso(chat_negocio), texto=TextoFalso(texto_negocio),
                        config_texto=ModelConfig(model_name="modelo-de-prueba"))
    return construir_grafo(deps, InMemorySaver())  # checkpointer en memoria (en producción: Redis)


@pytest.fixture
def client_app(redis, grafo, ajustes):
    from app.api.main import app, get_ajustes, get_graph, get_redis

    app.dependency_overrides[get_redis] = lambda: redis  # la API usa el Redis simulado
    app.dependency_overrides[get_graph] = lambda: grafo
    app.dependency_overrides[get_ajustes] = lambda: ajustes
    yield app
    app.dependency_overrides.clear()



@pytest.fixture(autouse=True)
def sin_dotenv_real(monkeypatch):
    """Ninguna prueba lee el `.env` de quien la ejecuta: dependerían de su máquina y pasarían o fallarían según sus claves."""
    from app.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
