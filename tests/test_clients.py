"""Pruebas del cliente de texto: contratos, errores tipados, reintentos, tiempo máximo y proveedores.

Todo con SDK falsos: sin red, sin claves, sin costo.
"""
import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import openai
import pytest
from google.genai import errors as gerrors
from pydantic import ValidationError

from app.clients import (
    AsyncLLMManager, ChatMessage, LLMAuthError, LLMBadRequestError, LLMError, LLMRateLimitError,
    LLMServerError, LLMTimeoutError, ModelConfig, Provider,
)
from app.clients.anthropic_client import AnthropicClient
from app.clients.errors import from_status
from app.clients.gemini_client import GeminiClient
from app.clients.openai_client import OpenAIClient
from app.clients.retry import espera, reintentar
from app.config import Settings

MSGS = [ChatMessage(role="system", content="Sé breve."), ChatMessage(role="user", content="Hola")]
CFG = ModelConfig(model_name="modelo-de-prueba")
REQ = httpx.Request("POST", "http://prueba")


def resp(codigo):
    return httpx.Response(codigo, request=REQ)


async def sin_espera(_):  # reemplaza asyncio.sleep: los reintentos no hacen esperar a las pruebas
    return None


POLITICA = dict(timeout_seconds=1.0, max_retries=2, retry_base_seconds=0.0, sleep=sin_espera)


# ---------------------------------------------------------------- contratos
def test_el_rol_es_un_vocabulario_cerrado():
    with pytest.raises(ValidationError):
        ChatMessage(role="usuario", content="hola")
    with pytest.raises(ValidationError):
        ChatMessage(role="user", content="")


def test_el_modelo_es_obligatorio_y_no_tiene_valor_por_defecto():
    with pytest.raises(ValidationError):
        ModelConfig()
    with pytest.raises(ValidationError):
        ModelConfig(model_name="x", temperature=3)


def test_la_configuracion_del_modelo_sale_de_la_configuracion_del_entorno(env):
    s = Settings(_env_file=None)
    assert ModelConfig.from_settings(s).model_name == "modelo-de-prueba"
    assert ModelConfig.from_settings(s, temperature=0.1).temperature == 0.1


# ---------------------------------------------------------------- errores
@pytest.mark.parametrize("codigo,clase,reintentable", [
    (429, LLMRateLimitError, True), (401, LLMAuthError, False), (403, LLMAuthError, False),
    (400, LLMBadRequestError, False), (500, LLMServerError, True), (503, LLMServerError, True),
    (408, LLMTimeoutError, True),
])
def test_un_codigo_http_se_traduce_al_error_que_corresponde(codigo, clase, reintentable):
    e = from_status(codigo, "x", "google")
    assert isinstance(e, clase) and e.retryable is reintentable and e.status_code == codigo


def test_el_mensaje_del_error_incluye_el_proveedor():
    assert str(LLMAuthError("clave inválida", provider="openai")) == "[openai] clave inválida"


# ---------------------------------------------------------------- reintentos
async def test_reintenta_un_fallo_transitorio_y_luego_funciona():
    llamadas = []

    async def llamada():
        llamadas.append(1)
        if len(llamadas) < 3:
            raise LLMRateLimitError("cuota", provider="x")
        return "ok"

    resultado, intentos = await reintentar(llamada, max_reintentos=3, base_segundos=0, dormir=sin_espera)
    assert resultado == "ok" and intentos == 3


async def test_un_error_no_reintentable_falla_de_inmediato():
    llamadas = []

    async def llamada():
        llamadas.append(1)
        raise LLMAuthError("clave", provider="x")

    with pytest.raises(LLMAuthError):
        await reintentar(llamada, max_reintentos=5, base_segundos=0, dormir=sin_espera)
    assert len(llamadas) == 1  # reintentar con una clave inválida solo gasta tiempo


async def test_al_agotar_los_reintentos_se_relanza_el_ultimo_error():
    async def llamada():
        raise LLMServerError("caído", provider="x")

    with pytest.raises(LLMServerError):
        await reintentar(llamada, max_reintentos=2, base_segundos=0, dormir=sin_espera)


async def test_la_espera_crece_al_doble_en_cada_reintento():
    esperas = []

    async def dormir(s):
        esperas.append(s)

    async def llamada():
        raise LLMRateLimitError("cuota", provider="x")

    with pytest.raises(LLMRateLimitError):
        await reintentar(llamada, max_reintentos=3, base_segundos=1.0, dormir=dormir, azar=lambda: 0.0)
    assert esperas == [1.0, 2.0, 4.0]
    assert espera(2, 1.0, azar=lambda: 1.0) == 2.5  # con el máximo de azar: 2 × 1,25


# ---------------------------------------------------------------- proveedores falsos
class FakeOpenAI:
    def __init__(self, pasos):
        self.pasos, self.llamadas = list(pasos), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._crear))

    async def _crear(self, **kw):
        self.llamadas.append(kw)
        paso = self.pasos.pop(0)
        if isinstance(paso, Exception):
            raise paso
        if kw.get("stream"):
            async def gen():
                for t in paso:
                    yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=t))])
            return gen()
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=paso))],
                               usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7))


async def test_openai_devuelve_texto_tokens_latencia_e_intentos():
    sdk = FakeOpenAI(["Hola, ¿en qué ayudo?"])
    r = await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert (r.content, r.prompt_tokens, r.completion_tokens, r.attempts) == ("Hola, ¿en qué ayudo?", 11, 7, 1)
    assert r.provider is Provider.OPENAI and r.latency_ms is not None
    assert sdk.llamadas[0]["model"] == "modelo-de-prueba"  # el modelo viene de la configuración, no del código


async def test_openai_reintenta_la_cuota_y_traduce_la_clave_invalida():
    sdk = FakeOpenAI([openai.RateLimitError("cuota", response=resp(429), body=None), "listo"])
    r = await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert r.content == "listo" and r.attempts == 2
    sdk2 = FakeOpenAI([openai.AuthenticationError("mala clave", response=resp(401), body=None)])
    with pytest.raises(LLMAuthError):
        await OpenAIClient("k", sdk_client=sdk2, **POLITICA).generate(MSGS, CFG)
    assert len(sdk2.llamadas) == 1


async def test_openai_traduce_el_tiempo_agotado_de_la_red():
    sdk = FakeOpenAI([openai.APITimeoutError(request=REQ)] * 3)
    with pytest.raises(LLMTimeoutError):
        await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert len(sdk.llamadas) == 3  # 1 intento + 2 reintentos


async def test_openai_streaming_emite_los_fragmentos_en_orden():
    sdk = FakeOpenAI([["Ho", "la", "!"]])
    out = [t async for t in OpenAIClient("k", sdk_client=sdk, **POLITICA).generate_stream(MSGS, CFG)]
    assert out == ["Ho", "la", "!"]


class FakeAnthropic:
    def __init__(self, pasos):
        self.pasos, self.llamadas = list(pasos), []
        self.messages = SimpleNamespace(create=self._crear, stream=self._stream)

    async def _crear(self, **kw):
        self.llamadas.append(kw)
        paso = self.pasos.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=t) for t in paso],
                               usage=SimpleNamespace(input_tokens=5, output_tokens=3))

    def _stream(self, **kw):
        trozos = self.pasos.pop(0)

        class Ctx:
            async def __aenter__(self_):
                async def gen():
                    for t in trozos:
                        yield t
                return SimpleNamespace(text_stream=gen())

            async def __aexit__(self_, *a):
                return False
        return Ctx()


async def test_anthropic_separa_el_system_y_une_los_bloques_de_texto():
    sdk = FakeAnthropic([["Hola ", "mundo"]])
    r = await AnthropicClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert r.content == "Hola mundo" and (r.prompt_tokens, r.completion_tokens) == (5, 3)
    enviado = sdk.llamadas[0]
    assert enviado["system"] == "Sé breve." and all(m["role"] != "system" for m in enviado["messages"])


async def test_anthropic_traduce_cuota_y_errores_de_servidor():
    sdk = FakeAnthropic([anthropic.RateLimitError("cuota", response=resp(429), body=None),
                         anthropic.InternalServerError("caído", response=resp(500), body=None), ["ok"]])
    r = await AnthropicClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert r.content == "ok" and r.attempts == 3


async def test_anthropic_streaming():
    sdk = FakeAnthropic([["a", "b", "c"]])
    out = [t async for t in AnthropicClient("k", sdk_client=sdk, **POLITICA).generate_stream(MSGS, CFG)]
    assert "".join(out) == "abc"


class FakeGemini:
    def __init__(self, pasos):
        self.pasos, self.llamadas = list(pasos), []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=self._gen, generate_content_stream=self._stream))

    async def _gen(self, **kw):
        self.llamadas.append(kw)
        paso = self.pasos.pop(0)
        if isinstance(paso, Exception):
            raise paso
        return SimpleNamespace(text=paso, usage_metadata=SimpleNamespace(prompt_token_count=9, candidates_token_count=4))

    async def _stream(self, **kw):
        paso = self.pasos.pop(0)
        if isinstance(paso, Exception):
            raise paso

        async def gen():
            for t in paso:
                yield SimpleNamespace(text=t)
        return gen()


async def test_gemini_usa_system_instruction_y_mapea_los_tokens():
    sdk = FakeGemini(["Hola"])
    r = await GeminiClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert (r.content, r.prompt_tokens, r.completion_tokens) == ("Hola", 9, 4)
    assert sdk.llamadas[0]["config"].system_instruction == "Sé breve."


@pytest.mark.parametrize("codigo,clase", [(429, LLMRateLimitError), (401, LLMAuthError), (400, LLMBadRequestError)])
async def test_gemini_traduce_los_errores_por_codigo(codigo, clase):
    e = gerrors.ClientError(codigo, {"error": {"message": "x", "status": "X"}})
    sdk = FakeGemini([e] * 3)
    with pytest.raises(clase):
        await GeminiClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    esperados = 3 if clase is LLMRateLimitError else 1  # solo la cuota se reintenta
    assert len(sdk.llamadas) == esperados


async def test_gemini_reintenta_un_error_de_servidor():
    e = gerrors.ServerError(503, {"error": {"message": "x", "status": "UNAVAILABLE"}})
    r = await GeminiClient("k", sdk_client=FakeGemini([e, "ya"]), **POLITICA).generate(MSGS, CFG)
    assert r.content == "ya" and r.attempts == 2


# ---------------------------------------------------------------- tiempo máximo y streaming con fallos
class Lento(GeminiClient):
    async def _generate_once(self, messages, config):
        await asyncio.sleep(5)


async def test_una_llamada_lenta_se_corta_con_error_de_tiempo():
    c = Lento("k", sdk_client=object(), timeout_seconds=0.05, max_retries=0, retry_base_seconds=0, sleep=sin_espera)
    with pytest.raises(LLMTimeoutError):
        await c.generate(MSGS, CFG)


class StreamFalla(GeminiClient):
    def __init__(self, guion, **kw):
        super().__init__("k", sdk_client=object(), **kw)
        self.guion, self.intentos = list(guion), 0

    async def _stream_once(self, messages, config):
        self.intentos += 1
        for paso in self.guion.pop(0):
            if isinstance(paso, Exception):
                raise paso
            yield paso


async def test_el_streaming_reintenta_si_falla_antes_del_primer_fragmento():
    c = StreamFalla([[LLMRateLimitError("cuota")], ["a", "b"]], **POLITICA)
    assert [t async for t in c.generate_stream(MSGS, CFG)] == ["a", "b"] and c.intentos == 2


async def test_el_streaming_no_reintenta_despues_de_emitir_texto():
    c = StreamFalla([["a", LLMServerError("caído")], ["a", "b"]], **POLITICA)
    visto = []
    with pytest.raises(LLMServerError):
        async for t in c.generate_stream(MSGS, CFG):
            visto.append(t)
    assert visto == ["a"] and c.intentos == 1  # repetir duplicaría lo que el usuario ya vio


# ---------------------------------------------------------------- fábrica
def test_la_fabrica_elige_el_proveedor_de_la_configuracion(env):
    s = Settings(_env_file=None)
    c = AsyncLLMManager.get_client(s)
    assert isinstance(c, GeminiClient) and c.max_retries == s.llm_max_retries and c.timeout_seconds == s.llm_timeout_seconds


def test_la_fabrica_exige_la_clave_del_proveedor_pedido(env):
    s = Settings(_env_file=None)
    with pytest.raises(ValueError, match="clave"):
        AsyncLLMManager.get_client(s, provider="openai")
    with pytest.raises(ValueError, match="no soportado"):
        AsyncLLMManager.get_client(s, provider="otro")


def test_la_fabrica_construye_cada_proveedor_cuando_hay_clave(env):
    env.setenv("OPENAI_API_KEY", "k1"); env.setenv("ANTHROPIC_API_KEY", "k2")
    s = Settings(_env_file=None)
    assert isinstance(AsyncLLMManager.get_client(s, provider="openai"), OpenAIClient)
    assert isinstance(AsyncLLMManager.get_client(s, provider="anthropic"), AnthropicClient)


def test_se_acepta_gemini_api_key_como_alias_de_google_api_key(env):
    env.delenv("GOOGLE_API_KEY"); env.setenv("GEMINI_API_KEY", "clave-del-modulo-1")
    assert Settings(_env_file=None).google_api_key == "clave-del-modulo-1"


# ---------------------------------------------------------------- observabilidad: cada llamada queda en LangSmith con su costo
from langsmith.run_helpers import tracing_context

from tests.fakes import ClienteEspia


async def test_cada_llamada_queda_registrada_con_proveedor_modelo_y_tokens():
    espia = ClienteEspia()
    sdk = FakeOpenAI(["Hola, ¿en qué ayudo?"])
    with tracing_context(enabled=True, client=espia):
        r = await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert r.content == "Hola, ¿en qué ayudo?"  # la traza no cambia el resultado
    (creada,), (cerrada,) = espia.creadas(), espia.actualizadas()
    assert (creada["name"], creada["run_type"]) == ("cliente_de_texto", "llm")
    meta = creada["extra"]["metadata"]
    assert (meta["ls_provider"], meta["ls_model_name"]) == ("openai", "modelo-de-prueba")  # lo que LangSmith necesita para el costo
    assert creada["inputs"]["messages"][1] == {"role": "user", "content": "Hola"} and "self" not in creada["inputs"]
    assert cerrada["outputs"]["usage_metadata"] == {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


async def test_google_se_registra_con_el_nombre_de_proveedor_de_langsmith():
    espia = ClienteEspia()
    with tracing_context(enabled=True, client=espia):
        await GeminiClient("k", sdk_client=FakeGemini(["Hola"]), **POLITICA).generate(MSGS, CFG)
    assert espia.creadas()[0]["extra"]["metadata"]["ls_provider"] == "google_genai"


async def test_una_llamada_que_falla_se_registra_con_su_error_y_sigue_fallando():
    espia = ClienteEspia()
    sdk = FakeOpenAI([openai.AuthenticationError("mala clave", response=resp(401), body=None)])
    with tracing_context(enabled=True, client=espia), pytest.raises(LLMAuthError):
        await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert len(espia.creadas()) == 1 and espia.actualizadas()[0].get("error")


async def test_con_las_trazas_apagadas_no_se_registra_nada():
    espia = ClienteEspia()
    await OpenAIClient("k", sdk_client=FakeOpenAI(["ok"]), **POLITICA).generate(MSGS, CFG)  # sin tracing_context
    assert espia.registro == []


def test_la_traza_de_una_llamada_fallida_no_provoca_advertencias():
    """Regresión: LangSmith llama a process_outputs con None cuando la llamada falla; no debe romperse ni ensuciar el log."""
    from app.clients.base import _salidas_traza

    assert _salidas_traza(None) == {}


async def test_una_llamada_fallida_con_trazas_activas_no_deja_advertencias_en_el_log(caplog):
    espia = ClienteEspia()
    sdk = FakeOpenAI([openai.AuthenticationError("mala clave", response=resp(401), body=None)])
    with caplog.at_level("WARNING"), tracing_context(enabled=True, client=espia), pytest.raises(LLMAuthError):
        await OpenAIClient("k", sdk_client=sdk, **POLITICA).generate(MSGS, CFG)
    assert "process_outputs failed" not in caplog.text
