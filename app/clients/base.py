"""Clase base del cliente de texto. Aquí viven las políticas comunes; cada proveedor solo traduce.

Patrón "método plantilla": generate() y generate_stream() son públicos y aplican tiempo máximo,
reintentos y medición de latencia. Los proveedores implementan _generate_once() y _stream_once(),
que hacen UN intento y traducen los errores del SDK a errores propios (errors.py).

Los SDK de OpenAI y Anthropic traen reintentos propios: se desactivan (max_retries=0) para que la
única política sea la de este archivo y no se multipliquen los intentos.
"""
import asyncio
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Awaitable, Callable

from langsmith import traceable

from app.clients.errors import LLMError, LLMTimeoutError
from app.clients.retry import espera, reintentar
from app.clients.schemas import ChatMessage, ModelConfig, ModelResponse, Provider


# Nombre del proveedor tal como lo espera LangSmith para calcular el costo
_PROVEEDOR_LANGSMITH = {Provider.GOOGLE: "google_genai", Provider.OPENAI: "openai", Provider.ANTHROPIC: "anthropic"}


def _entradas_traza(entradas: dict) -> dict:
    return {"messages": [m.model_dump() for m in entradas["messages"]], "config": entradas["config"].model_dump()}


def _salidas_traza(r: ModelResponse | None) -> dict:
    """LangSmith calcula el costo a partir de `usage_metadata` y del modelo."""
    if r is None:  # la llamada falló: LangSmith registra el error aparte y no hay salida que procesar
        return {}
    entrada, salida = r.prompt_tokens or 0, r.completion_tokens or 0
    return {"content": r.content, "attempts": r.attempts,
            "usage_metadata": {"input_tokens": entrada, "output_tokens": salida, "total_tokens": entrada + salida}}


class BaseLLMClient(ABC):
    provider: Provider

    def __init__(
        self,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 3,
        retry_base_seconds: float = 1.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self._sleep = sleep

    # ---- lo que implementa cada proveedor ----
    @abstractmethod
    async def _generate_once(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse: ...

    @abstractmethod
    def _stream_once(self, messages: list[ChatMessage], config: ModelConfig) -> AsyncGenerator[str, None]: ...

    # ---- lo que usa el resto del proyecto ----
    async def generate(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        """Respuesta completa, con tiempo máximo, reintentos y medición de latencia.

        Cada llamada queda registrada en LangSmith como una ejecución de tipo LLM, con proveedor, modelo y tokens:
        así el costo por ejecución incluye también lo que no pasa por LangChain. Con las trazas apagadas, no hace nada.
        """
        return await self._generar_trazado(
            messages, config,
            langsmith_extra={"metadata": {"ls_provider": _PROVEEDOR_LANGSMITH[self.provider], "ls_model_name": config.model_name}},
        )

    @traceable(run_type="llm", name="cliente_de_texto", process_inputs=_entradas_traza, process_outputs=_salidas_traza)
    async def _generar_trazado(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        inicio = time.perf_counter()

        async def un_intento() -> ModelResponse:
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    return await self._generate_once(messages, config)
            except TimeoutError as e:
                raise LLMTimeoutError(
                    f"La llamada superó {self.timeout_seconds} s", provider=self.provider.value
                ) from e

        respuesta, intentos = await reintentar(
            un_intento, max_reintentos=self.max_retries, base_segundos=self.retry_base_seconds, dormir=self._sleep
        )
        return respuesta.model_copy(
            update={"latency_ms": round((time.perf_counter() - inicio) * 1000, 1), "attempts": intentos}
        )

    async def generate_stream(self, messages: list[ChatMessage], config: ModelConfig) -> AsyncGenerator[str, None]:
        """Emite el texto a medida que llega.

        Se reintenta solo si el fallo ocurre ANTES del primer fragmento: después, el usuario ya vio
        texto parcial y repetir la llamada duplicaría lo emitido.
        """
        intento = 0
        while True:
            flujo = self._stream_once(messages, config)
            emitio = False
            try:
                while True:
                    try:
                        fragmento = await asyncio.wait_for(anext(flujo), self.timeout_seconds)
                    except StopAsyncIteration:
                        return
                    except TimeoutError as e:
                        raise LLMTimeoutError(
                            f"Sin datos durante {self.timeout_seconds} s", provider=self.provider.value
                        ) from e
                    emitio = True
                    yield fragmento
            except LLMError as e:
                if emitio or not e.retryable or intento >= self.max_retries:
                    raise
                intento += 1
                await self._sleep(espera(intento, self.retry_base_seconds))
            finally:
                await flujo.aclose()
