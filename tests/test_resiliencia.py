"""Resiliencia ante modelos saturados (503) o con cuota agotada (429): reintentos, modelo de respaldo y listado de modelos."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from google.genai import errors as gerrors

from app.agents.comun import ChatResiliente, SalidaInvalida, es_transitorio, pedir_json
from app.clients import ClienteConRespaldo, LLMAuthError, LLMServerError, ModelConfig, ModelResponse, Provider
from app.config import Settings
from app.llm import get_chat_model
from app.schemas import DecisionRuta
from scripts.listar_modelos import nombres_google
from scripts.verificar_entorno import verificar
from tests.fakes import EmbeddingsHash, ruta

ERROR_503 = gerrors.ServerError(503, {"error": {"message": "This model is currently experiencing high demand. Spikes in demand are usually temporary. "
                                                         "Please try again later.", "status": "UNAVAILABLE"}})  # el error real de la primera prueba
ERROR_429 = gerrors.ClientError(429, {"error": {"message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"}})


# ---------------------------------------------------------------- ¿vale la pena reintentar?
@pytest.mark.parametrize("error", [
    ERROR_503, ERROR_429, gerrors.ServerError(500, {"error": {"message": "interno", "status": "INTERNAL"}}),
    httpx.ConnectTimeout("sin respuesta"), ConnectionError("se cayó la red"), LLMServerError("caído", provider="google"),
    Exception("503 UNAVAILABLE. {'error': {'message': 'This model is currently experiencing high demand'}}"),
])
def test_los_errores_pasajeros_se_reintentan(error):
    assert es_transitorio(error) is True


@pytest.mark.parametrize("error", [
    gerrors.ClientError(400, {"error": {"message": "petición inválida", "status": "INVALID_ARGUMENT"}}),
    gerrors.ClientError(401, {"error": {"message": "API key not valid", "status": "UNAUTHENTICATED"}}),
    gerrors.ClientError(404, {"error": {"message": "model not found", "status": "NOT_FOUND"}}),
    LLMAuthError("clave inválida", provider="google"), ValueError("dato inválido"), SalidaInvalida("JSON inválido"), KeyError("x"),
])
def test_los_errores_definitivos_no_se_reintentan(error):
    assert es_transitorio(error) is False


def test_se_reconoce_el_error_aunque_venga_envuelto_por_otra_libreria():
    try:
        try:
            raise ERROR_503
        except Exception as original:
            raise RuntimeError("falló la llamada al modelo") from original
    except RuntimeError as envuelto:
        assert es_transitorio(envuelto) is True


# ---------------------------------------------------------------- el chat resiliente
class ChatGuion:
    """Modelo de chat de mentira que responde según un guion: cada paso es un texto o una excepción."""

    def __init__(self, *pasos):
        self.pasos, self.llamadas = list(pasos), 0

    async def ainvoke(self, mensajes, *a, **k):
        self.llamadas += 1
        paso = self.pasos.pop(0) if self.pasos else self.pasos_fin
        if isinstance(paso, Exception):
            raise paso
        return SimpleNamespace(content=paso)

    pasos_fin = "ok"


def resiliente(principal, respaldo=None, reintentos=3):
    pausas: list[float] = []

    async def dormir(s):
        pausas.append(s)

    return ChatResiliente(principal, respaldo, reintentos=reintentos, espera_base=2.0, dormir=dormir, azar=lambda: 0.0), pausas


async def test_si_responde_a_la_primera_no_hay_esperas():
    chat, pausas = resiliente(ChatGuion("hola"))
    assert (await chat.ainvoke([])).content == "hola" and pausas == []


async def test_un_503_pasajero_se_reintenta_con_espera_creciente_y_funciona():
    principal = ChatGuion(ERROR_503, ERROR_503, "ya respondió")
    chat, pausas = resiliente(principal)
    assert (await chat.ainvoke([])).content == "ya respondió" and principal.llamadas == 3
    assert pausas == [2.0, 4.0]  # 2 s y luego 4 s


async def test_un_error_definitivo_falla_de_inmediato_sin_esperar():
    principal = ChatGuion(gerrors.ClientError(401, {"error": {"message": "API key not valid", "status": "UNAUTHENTICATED"}}))
    chat, pausas = resiliente(principal, ChatGuion("respaldo"))
    with pytest.raises(gerrors.ClientError):
        await chat.ainvoke([])
    assert principal.llamadas == 1 and pausas == []


async def test_si_el_principal_sigue_saturado_se_usa_el_modelo_de_respaldo():
    principal, respaldo = ChatGuion(*[ERROR_503] * 10), ChatGuion("respuesta del respaldo")
    chat, pausas = resiliente(principal, respaldo, reintentos=2)
    assert (await chat.ainvoke([])).content == "respuesta del respaldo"
    assert principal.llamadas == 3 and respaldo.llamadas == 1  # el intento original y 2 reintentos, y luego el respaldo
    assert pausas == [2.0, 4.0]


async def test_si_ambos_fallan_se_propaga_el_ultimo_error():
    principal, respaldo = ChatGuion(*[ERROR_503] * 10), ChatGuion(*[ERROR_429] * 10)
    chat, _ = resiliente(principal, respaldo, reintentos=1)
    with pytest.raises(gerrors.ClientError):  # el 429 del respaldo
        await chat.ainvoke([])
    assert principal.llamadas == 2 and respaldo.llamadas == 2


async def test_sin_respaldo_se_propaga_el_error_tras_los_reintentos():
    principal = ChatGuion(*[ERROR_503] * 10)
    chat, _ = resiliente(principal, None, reintentos=2)
    with pytest.raises(gerrors.ServerError):
        await chat.ainvoke([])
    assert principal.llamadas == 3


async def test_una_pausa_del_grafo_no_se_trata_como_error_pasajero():
    class Cancelado(ChatGuion):
        async def ainvoke(self, *a, **k):
            raise asyncio.CancelledError  # lo que ocurre cuando vence el tiempo máximo del trabajo

    chat, pausas = resiliente(Cancelado())
    with pytest.raises(asyncio.CancelledError):
        await chat.ainvoke([])
    assert pausas == []


async def test_se_combina_con_el_json_validado_un_503_y_luego_un_json_valido():
    chat, _ = resiliente(ChatGuion(ERROR_503, ruta("fuera_de_alcance", motivo="no es del negocio")))
    decision = await pedir_json(chat, DecisionRuta, "sistema", "usuario")
    assert decision.decision == "fuera_de_alcance"


# ---------------------------------------------------------------- el cliente de texto con respaldo
class ClienteGuion:
    provider = Provider.GOOGLE

    def __init__(self, falla_en=(), error=None):
        self.modelos, self.falla_en, self.error = [], set(falla_en), error or LLMServerError("saturado", provider="google")

    async def generate(self, messages, config):
        self.modelos.append(config.model_name)
        if config.model_name in self.falla_en:
            raise self.error
        return ModelResponse(content="ok", provider=Provider.GOOGLE, model_name=config.model_name)


CFG = ModelConfig(model_name="principal")


async def test_el_cliente_de_texto_pasa_al_respaldo_si_el_principal_esta_saturado():
    base = ClienteGuion(falla_en={"principal"})
    r = await ClienteConRespaldo(base, "respaldo").generate([], CFG)
    assert r.model_name == "respaldo" and base.modelos == ["principal", "respaldo"]


async def test_un_error_definitivo_no_activa_el_respaldo():
    base = ClienteGuion(falla_en={"principal"}, error=LLMAuthError("clave inválida", provider="google"))
    with pytest.raises(LLMAuthError):
        await ClienteConRespaldo(base, "respaldo").generate([], CFG)
    assert base.modelos == ["principal"]


async def test_sin_respaldo_o_si_el_respaldo_es_el_mismo_modelo_no_se_repite_la_llamada():
    base = ClienteGuion(falla_en={"principal"})
    with pytest.raises(LLMServerError):
        await ClienteConRespaldo(base, None).generate([], CFG)
    base = ClienteGuion(falla_en={"principal"})
    with pytest.raises(LLMServerError):
        await ClienteConRespaldo(base, "principal").generate([], CFG)
    assert base.modelos == ["principal"]


async def test_si_el_principal_funciona_no_se_toca_el_respaldo_y_lo_demas_se_delega():
    base = ClienteGuion()
    c = ClienteConRespaldo(base, "respaldo")
    assert (await c.generate([], CFG)).model_name == "principal" and base.modelos == ["principal"]
    assert c.provider is Provider.GOOGLE  # el resto de los atributos los atiende el cliente real


# ---------------------------------------------------------------- configuración y listado de modelos
def test_un_respaldo_vacio_en_el_env_significa_sin_respaldo(env):
    env.setenv("LLM_FALLBACK_MODEL", "")
    assert Settings(_env_file=None).llm_fallback_model is None
    env.setenv("LLM_FALLBACK_MODEL", "  otro-modelo  ")
    assert Settings(_env_file=None).llm_fallback_model == "otro-modelo"
    env.delenv("LLM_FALLBACK_MODEL")
    assert Settings(_env_file=None).llm_fallback_model is None


def test_se_puede_pedir_un_modelo_de_chat_distinto_al_principal(env):
    s = Settings(_env_file=None)
    assert "modelo-de-prueba" in get_chat_model(s).model and "otro-modelo" in get_chat_model(s, modelo="otro-modelo").model


def test_el_listado_deja_solo_modelos_de_texto_y_sin_el_prefijo():
    modelos = [SimpleNamespace(name="models/modelo-b", supported_actions=["generateContent", "countTokens"]),
               SimpleNamespace(name="models/modelo-a", supported_actions=["generateContent"]),
               SimpleNamespace(name="models/embedding-x", supported_actions=["embedContent"]),
               SimpleNamespace(name="models/sin-datos", supported_actions=None),
               SimpleNamespace(name="models/modelo-a", supported_actions=["generateContent"])]
    assert nombres_google(modelos) == ["modelo-a", "modelo-b", "sin-datos"]  # ordenado, sin repetidos ni embeddings


# ---------------------------------------------------------------- el verificador también comprueba el respaldo
class TextoRegistra:
    def __init__(self, falla_en=()):
        self.modelos, self.falla_en = [], set(falla_en)

    async def generate(self, messages, config):
        self.modelos.append(config.model_name)
        if config.model_name in self.falla_en:
            raise RuntimeError("404 modelo no encontrado")
        return ModelResponse(content="ok", provider=Provider.GOOGLE, model_name=config.model_name, prompt_tokens=5, completion_tokens=1, latency_ms=9.0)


def estados(r):
    return {v.nombre: v.estado for v in r}


async def test_el_verificador_prueba_el_modelo_de_respaldo(env):
    env.setenv("LLM_FALLBACK_MODEL", "modelo-de-respaldo")
    texto = TextoRegistra()
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), cliente_texto=texto)
    assert estados(r)["Modelo de respaldo"] == "ok" and texto.modelos == ["modelo-de-prueba", "modelo-de-respaldo"]


async def test_un_respaldo_mal_escrito_se_detecta_antes_de_necesitarlo(env):
    env.setenv("LLM_FALLBACK_MODEL", "modelo-que-no-existe")
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), cliente_texto=TextoRegistra(falla_en={"modelo-que-no-existe"}))
    e = estados(r)
    assert e["Modelo de texto"] == "ok" and e["Modelo de respaldo"] == "falla"
    assert "modelo-que-no-existe" in next(v.detalle for v in r if v.nombre == "Modelo de respaldo")


async def test_sin_respaldo_configurado_no_aparece_esa_verificacion(env):
    r = await verificar(Settings(_env_file=None), embeddings=EmbeddingsHash(), cliente_texto=TextoRegistra())
    assert "Modelo de respaldo" not in estados(r)
