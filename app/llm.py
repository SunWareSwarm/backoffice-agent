"""Cliente abstracto: el resto del proyecto pide un modelo de chat y un modelo de embeddings aquí,
sin saber de qué proveedor son. Cambiar de proveedor es cambiar variables de entorno.

Dos clientes separados, porque son dos problemas distintos:
  - get_chat_model():  genera texto y usa herramientas.
  - get_embeddings():  convierte texto en vectores (y NO todos los proveedores lo ofrecen).

Dentro de funciones async se usan siempre los métodos async: ainvoke(), aembed_documents(),
aembed_query(). Un invoke() síncrono bloquearía el event loop.
"""
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config import Settings, get_settings


def get_chat_model(settings: Settings | None = None, modelo: str | None = None) -> BaseChatModel:
    """El modelo de chat configurado, o `modelo` si se pide otro del mismo proveedor (por ejemplo, el de respaldo)."""
    s = settings or get_settings()
    nombre = modelo or s.llm_model
    if s.llm_provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=nombre, google_api_key=s.google_api_key)
    if s.llm_provider == "openai":
        from langchain_openai import ChatOpenAI  # pip install langchain-openai

        return ChatOpenAI(model=nombre, api_key=s.openai_api_key)
    if s.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic  # pip install langchain-anthropic

        return ChatAnthropic(model=nombre, api_key=s.anthropic_api_key)
    raise ValueError(f"Proveedor de chat no soportado: {s.llm_provider}")


def get_embeddings(settings: Settings | None = None) -> Embeddings:
    s = settings or get_settings()
    if s.embedding_provider == "google":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        kwargs = {"output_dimensionality": s.embedding_dimensions} if s.embedding_dimensions else {}
        return GoogleGenerativeAIEmbeddings(model=s.embedding_model, google_api_key=s.google_api_key, **kwargs)
    if s.embedding_provider == "openai":
        from langchain_openai import OpenAIEmbeddings  # pip install langchain-openai

        kwargs = {"dimensions": s.embedding_dimensions} if s.embedding_dimensions else {}
        return OpenAIEmbeddings(model=s.embedding_model, api_key=s.openai_api_key, **kwargs)
    raise ValueError(f"Proveedor de embeddings no soportado: {s.embedding_provider}")
