"""Cliente de texto con modelo de respaldo.

El cliente base ya reintenta con espera creciente los errores transitorios. Si tras los reintentos sigue fallando por saturación
o cuota, esta capa repite la llamada con otro modelo del MISMO proveedor. Los errores definitivos (clave inválida, petición
mal formada) no activan el respaldo: con otro modelo fallarían igual.
"""
import logging

from app.clients.base import BaseLLMClient
from app.clients.errors import LLMError
from app.clients.schemas import ChatMessage, ModelConfig, ModelResponse

log = logging.getLogger("uvicorn.error")


class ClienteConRespaldo:
    def __init__(self, cliente: BaseLLMClient, modelo_respaldo: str | None):
        self._cliente, self._respaldo = cliente, modelo_respaldo

    def __getattr__(self, nombre):  # lo que no se redefine (provider, generate_stream...) lo atiende el cliente real
        return getattr(self._cliente, nombre)

    async def generate(self, messages: list[ChatMessage], config: ModelConfig) -> ModelResponse:
        try:
            return await self._cliente.generate(messages, config)
        except LLMError as e:
            if not self._respaldo or not e.retryable or config.model_name == self._respaldo:
                raise
            log.warning("El modelo %s sigue sin responder (%s): se usa el de respaldo %s", config.model_name, type(e).__name__, self._respaldo)
            return await self._cliente.generate(messages, config.model_copy(update={"model_name": self._respaldo}))
