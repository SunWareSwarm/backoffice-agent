"""Configuración central: todo lo que cambia entre entornos viene de variables de entorno.

Regla de la rúbrica: ningún nombre de modelo ni identificador de índice va escrito en el código.
Por eso los modelos NO tienen valor por defecto: si falta uno, la aplicación se niega a arrancar.
"""
import re
from functools import lru_cache
from datetime import date
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False, populate_by_name=True)

    # Modelo de chat
    llm_provider: Literal["google", "openai", "anthropic"]
    llm_model: str = Field(min_length=1)
    # Modelo al que se pasa si el principal sigue saturado (503) o limitado (429) tras los reintentos. Opcional: vacío = sin respaldo.
    llm_fallback_model: str | None = None

    # Modelo de embeddings (Anthropic no ofrece uno propio, por eso aquí no aparece)
    embedding_provider: Literal["google", "openai"]
    embedding_model: str = Field(min_length=1)
    embedding_dimensions: int | None = Field(default=None, ge=1)

    # Claves: solo hace falta la del proveedor elegido
    # Acepta GOOGLE_API_KEY o GEMINI_API_KEY (el cliente del Módulo 1 usaba esta última)
    google_api_key: str | None = Field(default=None, validation_alias=AliasChoices("google_api_key", "gemini_api_key"))
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # Política del cliente de texto (app/clients): tiempo máximo y reintentos
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=8)
    llm_retry_base_seconds: float = Field(default=2.0, ge=0)  # esperas de 2, 4 y 8 s: un 503 por alta demanda suele ser pasajero

    # API y trabajos en segundo plano
    redis_url: str = "redis://localhost:6379/0"
    job_ttl_seconds: int = Field(default=60 * 60 * 24, gt=0)  # los trabajos expiran solos de Redis a las 24 h
    job_timeout_seconds: float = Field(default=300.0, gt=0)  # una ejecución que tarde más se marca FAILED
    recursion_limit: int = Field(default=40, ge=10)  # seguro anti-bucle de LangGraph, además del límite de pasos propio

    # RAG y datos
    chroma_path: str = "data/chroma"
    rag_top_k: int = Field(default=4, ge=1, le=20)
    rag_modo: Literal["vectorial", "bm25", "hibrido"] = "hibrido"  # el de mejor desempeño en la evaluación con embeddings reales
    sqlite_path: str = "data/tienda.db"
    # La fecha «de hoy» de la tienda simulada. Fija a propósito: así los datos y las cifras de la documentación son reproducibles.
    fecha_simulada: date = date(2026, 11, 3)

    @field_validator("llm_fallback_model", mode="before")
    @classmethod
    def respaldo_vacio_es_ninguno(cls, v):
        return (v.strip() or None) if isinstance(v, str) else v  # `LLM_FALLBACK_MODEL=` vacío en el .env significa «sin respaldo»

    @model_validator(mode="after")
    def exigir_claves_del_proveedor(self):
        claves = {"google": self.google_api_key, "openai": self.openai_api_key, "anthropic": self.anthropic_api_key}
        for rol, proveedor in (("chat", self.llm_provider), ("embeddings", self.embedding_provider)):
            if not claves[proveedor]:
                raise ValueError(f"Falta la clave de API de {proveedor!r}, necesaria para {rol}")
        return self

    @property
    def rag_collection_name(self) -> str:
        """Nombre de la colección de Chroma. Incluye proveedor, modelo y dimensiones.

        Los vectores de modelos distintos NO son comparables entre sí. Si cambia el modelo de
        embeddings, el nombre cambia y se crea una colección nueva en lugar de mezclar vectores.
        """
        base = f"rag_{self.embedding_provider}_{self.embedding_model}"
        if self.embedding_dimensions:
            base += f"_{self.embedding_dimensions}d"
        return re.sub(r"[^a-zA-Z0-9._-]", "-", base)


@lru_cache
def get_settings() -> Settings:
    return Settings()
