"""Fábrica de clientes de texto: elige el proveedor según la configuración.

Regla de uso (opción C, ver docs/decisiones.md):
  - app/clients  -> texto libre y streaming (por ejemplo, el Redactor), con métricas de latencia.
  - app/llm.py   -> lo que necesita salida estructurada, herramientas o embeddings (LangChain).

Los módulos de cada proveedor se importan solo al usarlos: no hace falta tener instalados los SDK
de los proveedores que no se usan.
"""
from app.clients.base import BaseLLMClient
from app.clients.schemas import Provider
from app.config import Settings, get_settings


class AsyncLLMManager:
    @classmethod
    def get_client(cls, settings: Settings | None = None, provider: Provider | str | None = None) -> BaseLLMClient:
        s = settings or get_settings()
        try:
            prov = Provider(provider or s.llm_provider)
        except ValueError:
            validos = [p.value for p in Provider]
            raise ValueError(f"Proveedor {provider!r} no soportado. Opciones válidas: {validos}") from None

        claves = {Provider.GOOGLE: s.google_api_key, Provider.OPENAI: s.openai_api_key, Provider.ANTHROPIC: s.anthropic_api_key}
        if not claves[prov]:
            raise ValueError(f"Falta la clave de API de {prov.value!r}")
        politicas = dict(
            timeout_seconds=s.llm_timeout_seconds, max_retries=s.llm_max_retries, retry_base_seconds=s.llm_retry_base_seconds
        )

        if prov is Provider.GOOGLE:
            from app.clients.gemini_client import GeminiClient
            return GeminiClient(claves[prov], **politicas)
        if prov is Provider.OPENAI:
            from app.clients.openai_client import OpenAIClient
            return OpenAIClient(claves[prov], **politicas)
        from app.clients.anthropic_client import AnthropicClient
        return AnthropicClient(claves[prov], **politicas)
