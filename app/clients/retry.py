"""Reintentos con espera creciente. Solo se reintenta lo que el error declara reintentable."""
import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.clients.errors import LLMError

T = TypeVar("T")


def espera(intento: int, base: float, azar: Callable[[], float] = random.random) -> float:
    """Espera antes del reintento n.º `intento` (1, 2, 3...): base × 2^(intento-1), más hasta 25 % de azar."""
    return base * (2 ** (intento - 1)) * (1 + 0.25 * azar())


async def reintentar(
    llamada: Callable[[], Awaitable[T]],
    *,
    max_reintentos: int,
    base_segundos: float,
    dormir: Callable[[float], Awaitable[None]] = asyncio.sleep,
    azar: Callable[[], float] = random.random,
) -> tuple[T, int]:
    """Ejecuta `llamada`. Devuelve (resultado, intentos usados). Relanza el último error si se agotan."""
    intento = 0
    while True:
        try:
            return await llamada(), intento + 1
        except LLMError as e:
            if not e.retryable or intento >= max_reintentos:
                raise
            intento += 1
            await dormir(espera(intento, base_segundos, azar))
