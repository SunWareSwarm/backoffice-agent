"""Errores propios del cliente de modelos.

Hallazgo n.º 2 de la revisión del Módulo 1: todos los fallos terminaban en un RuntimeError, así que
no había forma de distinguir un límite de cuota (conviene reintentar) de una clave inválida (no
conviene). Aquí cada tipo de fallo tiene su clase y declara si es reintentable.
"""


class LLMError(Exception):
    """Base de todos los errores del cliente. `retryable` indica si tiene sentido reintentar."""

    retryable = False

    def __init__(self, message: str, *, provider: str | None = None, status_code: int | None = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code

    def __str__(self) -> str:
        prefijo = f"[{self.provider}] " if self.provider else ""
        return prefijo + super().__str__()


class LLMRateLimitError(LLMError):
    """Se superó el límite de uso (HTTP 429). Reintentable con espera."""
    retryable = True


class LLMTimeoutError(LLMError):
    """La llamada tardó más que el tiempo máximo. Reintentable."""
    retryable = True


class LLMConnectionError(LLMError):
    """No se pudo conectar con el proveedor. Reintentable."""
    retryable = True


class LLMServerError(LLMError):
    """Falla del lado del proveedor (HTTP 5xx). Reintentable."""
    retryable = True


class LLMAuthError(LLMError):
    """Clave inválida o sin permisos (HTTP 401 o 403). Reintentar no sirve."""


class LLMBadRequestError(LLMError):
    """La petición es inválida (HTTP 400, 404 o 422). Reintentar no sirve."""


class LLMUnknownError(LLMError):
    """Cualquier otro fallo que no sabemos clasificar."""


def from_status(status_code: int | None, message: str, provider: str) -> LLMError:
    """Traduce un código HTTP al error propio que corresponde."""
    if status_code == 429:
        return LLMRateLimitError(message, provider=provider, status_code=status_code)
    if status_code in (401, 403):
        return LLMAuthError(message, provider=provider, status_code=status_code)
    if status_code == 408:
        return LLMTimeoutError(message, provider=provider, status_code=status_code)
    if status_code in (400, 404, 422):
        return LLMBadRequestError(message, provider=provider, status_code=status_code)
    if status_code is not None and status_code >= 500:
        return LLMServerError(message, provider=provider, status_code=status_code)
    return LLMUnknownError(message, provider=provider, status_code=status_code)
