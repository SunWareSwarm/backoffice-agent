"""El verificador de entorno, con modelos falsos: debe señalar qué eslabón falla y omitir los que dependen de él."""
from types import SimpleNamespace

import pytest

from app.clients import ModelResponse, Provider
from app.config import Settings
from scripts.verificar_entorno import verificar
from tests.fakes import EmbeddingsHash


class TextoFalso:
    def __init__(self, error=None):
        self.error = error

    async def generate(self, messages, config):
        if self.error:
            raise self.error
        return ModelResponse(content="ok", provider=Provider.GOOGLE, model_name=config.model_name,
                             prompt_tokens=7, completion_tokens=1, latency_ms=12.0)


class EmbeddingsRotos(EmbeddingsHash):
    async def aembed_documents(self, texts):
        raise ConnectionError("sin red")


def estados(resultados):
    return {v.nombre: v.estado for v in resultados}


async def test_todo_funciona(env):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), cliente_texto=TextoFalso())
    assert set(estados(r).values()) == {"ok"} and len(r) == 5
    assert "dimensión 128" in next(v.detalle for v in r if v.nombre == "Embeddings")


async def test_sin_chat_omite_solo_esa_llamada(env):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), con_chat=False)
    e = estados(r)
    assert e["Modelo de texto"] == "omitida" and e["Embeddings"] == "ok" and e["Chroma"] == "ok"


async def test_si_fallan_los_embeddings_se_omite_chroma_y_se_dice_por_que(env):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsRotos(), cliente_texto=TextoFalso())
    e = estados(r)
    assert (e["Embeddings"], e["Chroma"], e["Modelo de texto"]) == ("falla", "omitida", "ok")
    assert "sin red" in next(v.detalle for v in r if v.nombre == "Embeddings")
    assert "Embeddings" in next(v.detalle for v in r if v.nombre == "Chroma")


async def test_si_falla_el_modelo_de_texto_lo_demas_sigue(env):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), cliente_texto=TextoFalso(RuntimeError("clave inválida")))
    e = estados(r)
    assert e["Modelo de texto"] == "falla" and e["Embeddings"] == "ok" and e["Chroma"] == "ok"
    assert "clave inválida" in next(v.detalle for v in r if v.nombre == "Modelo de texto")


async def test_una_configuracion_incompleta_se_explica_y_omite_todo_lo_demas(env, monkeypatch):
    from app import config
    monkeypatch.delenv("LLM_MODEL")
    config.get_settings.cache_clear()
    r = await verificar()  # sin pasar settings: usa get_settings(), igual que el script real
    e = estados(r)
    assert e["Manuales y catálogo"] == "ok"  # no depende de la configuración
    assert e["Configuración (.env)"] == "falla" and {e["Modelo de texto"], e["Embeddings"], e["Chroma"]} == {"omitida"}
    assert "llm_model" in next(v.detalle for v in r if v.nombre == "Configuración (.env)")


async def test_si_los_manuales_no_estan_lo_dice(env, tmp_path):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), con_chat=False, dir_docs=tmp_path)
    assert estados(r)["Manuales y catálogo"] == "falla" and "Faltan manuales" in r[0].detalle


async def test_el_verificador_avisa_si_las_trazas_quedaron_apagadas(env, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # sin .env a la vista
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY"):
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), con_chat=False, revisar_trazas=True)
    trazas = next(v for v in r if v.nombre == "Trazas (LangSmith)")
    assert trazas.estado == "omitida" and "LANGSMITH_API_KEY" in trazas.detalle  # avisa, pero no es una falla


async def test_el_verificador_confirma_las_trazas_activas_aunque_vengan_del_dotenv(env, monkeypatch, tmp_path):
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, "x")
        monkeypatch.delenv(var)
    (tmp_path / ".env").write_text("LANGSMITH_TRACING=true\nLANGSMITH_API_KEY=ls-clave\nLANGSMITH_PROJECT=backoffice-agent\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), con_chat=False, revisar_trazas=True)
    trazas = next(v for v in r if v.nombre == "Trazas (LangSmith)")
    assert trazas.estado == "ok" and "backoffice-agent" in trazas.detalle


async def test_en_windows_el_borrado_bloqueado_de_la_carpeta_temporal_no_cuenta_como_falla_de_chroma(env, monkeypatch):
    """Regresión: Chroma mantiene abierto su archivo y en Windows el borrado falla con PermissionError aunque todo funcione."""
    import tempfile

    original = tempfile.TemporaryDirectory

    class DirTempBloqueado:
        """Imita a Windows: al salir, el borrado falla salvo que se pida ignorar los errores de limpieza."""

        def __init__(self, *args, ignore_cleanup_errors=False, **kwargs):
            self.ignorar, self._d = ignore_cleanup_errors, original(ignore_cleanup_errors=True)

        def __enter__(self):
            return self._d.__enter__()

        def __exit__(self, *exc):
            self._d.__exit__(*exc)
            if not self.ignorar:
                raise PermissionError("[WinError 32] El proceso no tiene acceso al archivo porque está siendo utilizado por otro proceso")

    monkeypatch.setattr(tempfile, "TemporaryDirectory", DirTempBloqueado)
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), con_chat=False)
    assert estados(r)["Chroma"] == "ok"


def test_las_pruebas_no_leen_el_dotenv_real_de_quien_las_ejecuta(monkeypatch, tmp_path):
    """Regresión: con un .env real en la carpeta, una prueba que borraba una variable seguía viéndola."""
    from pydantic import ValidationError

    for var in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    (tmp_path / ".env").write_text("LLM_PROVIDER=google\nLLM_MODEL=del-dotenv\nEMBEDDING_PROVIDER=google\nEMBEDDING_MODEL=e\nGOOGLE_API_KEY=k\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValidationError):
        Settings()  # sin pasar _env_file: el aislamiento de conftest impide que lea el .env
