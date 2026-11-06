"""Cliente de OpenAI (SDK asíncrono oficial). Hace un intento y traduce los errores."""
from collections.abc import AsyncGenerator

import openai

from app.clients import errors as E
from app.clients.base import BaseLLMClient
from app.clients.schemas import ChatMessage, ModelConfig, ModelResponse, Provider


class OpenAIClient(BaseLLMClient):
    provider = Provider.OPENAI

    def __init__(self, api_key: str, *, sdk_client=None, **politicas):
        super().__init__(**politicas)
        # max_retries=0: la política de reintentos es la de BaseLLMClient, no la del SDK
        self._sdk = sdk_client or openai.AsyncOpenAI(api_key=api_key, max_retries=0)

    @staticmethod
    def _formato(messages: list[ChatMessage]) -> list[dict]:
        return [m.model_dump() for m in messages]

    def _traducir(self, exc: Exception) -> E.LLMError:
        p, msg = self.provider.value, str(exc)
        if isinstance(exc, openai.RateLimitError):
            return E.LLMRateLimitError(msg, provider=p, status_code=429)
        if isinstance(exc, openai.APITimeoutError):  # va antes: es subclase de APIConnectionError
            return E.LLMTimeoutError(msg, provider=p)
        if isinstance(exc, openai.APIConnectionError):
            return E.LLMConnectionError(msg, provider=p)
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return E.LLMAuthError(msg, provider=p, status_code=getattr(exc, "status_code", None))
        if isinstance(exc, openai.APIStatusError):
            return E.from_status(exc.status_code, msg, p)
        return E.LLMUnknownError(msg, provider=p)

    async def _generate_once(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        try:
            r = await self._sdk.chat.completions.create(
                model=config.model_name,
                messages=self._formato(messages),
                temperature=config.temperature,
                max_completion_tokens=config.max_tokens,
            )
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
        uso = r.usage
        return ModelResponse(
            content=r.choices[0].message.content or "",
            provider=self.provider,
            model_name=config.model_name,
            prompt_tokens=uso.prompt_tokens if uso else None,
            completion_tokens=uso.completion_tokens if uso else None,
        )

    async def _stream_once(self, messages: list[ChatMessage], config: ModelConfig) -> AsyncGenerator[str, None]:
        try:
            flujo = await self._sdk.chat.completions.create(
                model=config.model_name,
                messages=self._formato(messages),
                temperature=config.temperature,
                max_completion_tokens=config.max_tokens,
                stream=True,
            )
            async for trozo in flujo:
                if trozo.choices and trozo.choices[0].delta.content:
                    yield trozo.choices[0].delta.content
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
