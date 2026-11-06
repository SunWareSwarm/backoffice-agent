"""Contratos del cliente de modelos (no confundir con app/schemas.py, que son los del dominio).

Hallazgos n.º 1 y 3 de la revisión del Módulo 1:
  - `model_name` ya no tiene valor por defecto ni respaldo escrito en el código.
  - `role` es un vocabulario cerrado, no un texto libre.
"""
from enum import Enum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.config import Settings


class Provider(str, Enum):
    """Mismos valores que LLM_PROVIDER en la configuración."""
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, description="Contenido textual del mensaje")


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_name: str = Field(min_length=1, description="Identificador del modelo; siempre viene de la configuración")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1000, gt=0)

    @classmethod
    def from_settings(cls, settings: "Settings", **cambios) -> "ModelConfig":
        """Arma la configuración desde las variables de entorno. Los parámetros se pueden ajustar."""
        datos = {"model_name": settings.llm_model, **cambios}
        return cls(**datos)


class ModelResponse(BaseModel):
    content: str
    provider: Provider
    model_name: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: float | None = Field(default=None, description="Tiempo total, incluidos los reintentos")
    attempts: int = Field(default=1, ge=1, description="Intentos usados (1 = sin reintentos)")
