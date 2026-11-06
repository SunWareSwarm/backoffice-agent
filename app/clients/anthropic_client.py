"""Cliente de Anthropic (SDK asíncrono oficial). Hace un intento y traduce los errores."""
from collections.abc import AsyncGenerator

import anthropic

from app.clients import errors as E
from app.clients.base import BaseLLMClient
from app.clients.schemas import ChatMessage, ModelConfig, ModelResponse, Provider


class AnthropicClient(BaseLLMClient):
    provider = Provider.ANTHROPIC

    def __init__(self, api_key: str, *, sdk_client=None, **politicas):
        super().__init__(**politicas)
        self._sdk = sdk_client or anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    @staticmethod
    def _preparar(messages: list[ChatMessage], config: ModelConfig) -> dict:
        """Anthropic recibe 'system' como parámetro aparte, no dentro de la lista de mensajes."""
        sistema = "\n".join(m.content for m in messages if m.role == "system")
        resto = [{"role": m.role, "content": m.content} for m in messages if m.role != "system"]
        args = {
            "model": config.model_name,
            "messages": resto,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
        }
        if sistema:
            args["system"] = sistema
        return args

    def _traducir(self, exc: Exception) -> E.LLMError:
        p, msg = self.provider.value, str(exc)
        if isinstance(exc, anthropic.RateLimitError):
            return E.LLMRateLimitError(msg, provider=p, status_code=429)
        if isinstance(exc, anthropic.APITimeoutError):
            return E.LLMTimeoutError(msg, provider=p)
        if isinstance(exc, anthropic.APIConnectionError):
            return E.LLMConnectionError(msg, provider=p)
        if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
            return E.LLMAuthError(msg, provider=p, status_code=getattr(exc, "status_code", None))
        if isinstance(exc, anthropic.APIStatusError):
            return E.from_status(exc.status_code, msg, p)
        return E.LLMUnknownError(msg, provider=p)

    async def _generate_once(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        try:
            r = await self._sdk.messages.create(**self._preparar(messages, config))
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
        texto = "".join(b.text for b in (r.content or []) if getattr(b, "type", None) == "text")
        return ModelResponse(
            content=texto,
            provider=self.provider,
            model_name=config.model_name,
            prompt_tokens=r.usage.input_tokens if r.usage else None,
            completion_tokens=r.usage.output_tokens if r.usage else None,
        )

    async def _stream_once(self, messages: list[ChatMessage], config: ModelConfig) -> AsyncGenerator[str, None]:
        try:
            async with self._sdk.messages.stream(**self._preparar(messages, config)) as flujo:
                async for texto in flujo.text_stream:
                    yield texto
        except E.LLMError:
            raise
        except Exception as exc:
            raise self._traducir(exc) from exc
