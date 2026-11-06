"""Contratos de la API: qué JSON acepta y qué JSON devuelve (validados con Pydantic)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.jobs import JobStatus
from app.schemas import Solicitud


class _Cuerpo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskCreate(_Cuerpo):
    """Cuerpo del POST /tasks."""
    query: str = Field(description="Consulta para el agente, de 3 a 500 caracteres")

    @field_validator("query")
    @classmethod
    def valida_como_solicitud(cls, v: str) -> str:
        return Solicitud(texto=v).texto  # mismo contrato que usa el grafo: recorta espacios y exige 3 a 500 caracteres


class ApprovalBody(_Cuerpo):
    """Cuerpo del POST /tasks/{id}/approve: qué acciones propuestas se aprueban (por posición)."""
    aprobar_todo: bool = Field(default=False, description="Aprueba todas las acciones propuestas")
    aprobadas: list[int] = Field(default_factory=list, description="Posiciones aprobadas; vacío = no se aprueba ninguna")
    comentario: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def no_mezclar(self):
        if self.aprobar_todo and self.aprobadas:
            raise ValueError("usa 'aprobar_todo' o 'aprobadas', no los dos")
        if len(set(self.aprobadas)) != len(self.aprobadas):
            raise ValueError("las posiciones aprobadas no pueden repetirse")
        return self


class ClarifyBody(_Cuerpo):
    """Cuerpo del POST /tasks/{id}/clarify: la respuesta a la pregunta aclaratoria."""
    texto: str = Field(min_length=1, max_length=500)

    @field_validator("texto")
    @classmethod
    def no_vacio(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("la aclaración no puede estar vacía")
        return v


class TaskAccepted(BaseModel):
    """Respuesta inmediata de las operaciones que encolan trabajo: el 'ticket'."""
    job_id: str
    status: JobStatus


class TaskStatus(BaseModel):
    """Respuesta del GET /tasks/{job_id}."""
    job_id: str
    status: JobStatus
    query: str
    current_node: str | None = None  # último nodo del grafo que terminó (progreso)
    result: str | None = None  # la respuesta final para el usuario
    answer_origin: Literal["llm", "plantilla"] | None = None  # 'plantilla' si el texto lo armó el código, sin LLM
    error: str | None = None
    steps: int | None = None  # nodos que recorrió el grafo
    execution: list[dict] | None = None  # qué pasó con las acciones aprobadas
    approval_request: dict | None = None  # qué se pide aprobar (solo en WAITING_APPROVAL)
    clarification_request: dict | None = None  # la pregunta aclaratoria (solo en WAITING_CLARIFICATION)
    created_at: str
    updated_at: str
