"""Cliente de Google (SDK asíncrono oficial, google-genai). Hace un intento y traduce los errores."""
from collections.abc import AsyncGenerator

import httpx
from google import genai
from google.genai import errors as gerrors
from google.genai import types

from app.clients import errors as E
from app.clients.base import BaseLLMClient
from app.clients.schemas import ChatMessage, ModelConfig, ModelResponse, Provider


class GeminiClient(BaseLLMClient):
    provider = Provider.GOOGLE

    def __init__(self, api_key: str, *, sdk_client=None, **politicas):
        super().__init__(**politicas)
        self._sdk = sdk_client or genai.Client(api_key=api_key)

    @staticmethod
    def _preparar(messages: list[ChatMessage], config: ModelConfig):
        """Gemini llama 'model' al asistente y recibe el 'system' como instrucción aparte."""
        sistema = "\n".join(m.content for m in messages if m.role == "system") or None
        contenidos = [
            types.Content(role="model" if m.role == "assistant" else "user", parts=[types.Part.from_text(text=m.content)])
            for m in messages
            if m.role != "system"
        ]
        cfg = types.GenerateContentConfig(
            temperature=config.temperature, max_output_tokens=config.max_tokens, system_instruction=sistema
        )
        return contenidos, cfg

    def _traducir(self, exc: Exception) -> E.LLMError:
        p, msg = self.provider.value, str(exc)
        if isinstance(exc, gerrors.APIError):
            return E.from_status(exc.code, msg, p)
        if isinstance(exc, httpx.TimeoutException):
            return E.LLMTimeoutError(msg, provider=p)
        if isinstance(exc, httpx.TransportError):
            return E.LLMConnectionError(msg, provider=p)
        return E.LLMUnknownError(msg, provider=p)

    async def _generate_once(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        contenidos, cfg = self._preparar(messages, config)
        try:
            r = await self._sdk.aio.models.generate_content(model=config.model_name, contents=contenidos, config=cfg)
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
        uso = r.usage_metadata
        return ModelResponse(
            content=r.text or "",
            provider=self.provider,
            model_name=config.model_name,
            prompt_tokens=uso.prompt_token_count if uso else None,
            completion_tokens=uso.candidates_token_count if uso else None,
        )

    async def _stream_once(self, messages: list[ChatMessage], config: ModelConfig) -> AsyncGenerator[str, None]:
        contenidos, cfg = self._preparar(messages, config)
        try:
            flujo = await self._sdk.aio.models.generate_content_stream(
                model=config.model_name, contents=contenidos, config=cfg
            )
            async for trozo in flujo:
                if trozo.text:
                    yield trozo.text
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
