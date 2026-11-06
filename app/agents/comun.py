"""Piezas comunes de los agentes: dependencias, acceso a la base sin bloquear, y salida JSON validada."""
import asyncio
import json
import logging
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Any

import httpx
from langgraph.errors import GraphBubbleUp
from pydantic import TypeAdapter, ValidationError

from app.clients import ModelConfig
from app.clients.errors import LLMError
from app.clients.retry import espera
from app.db.connection import conectar
from app.tools.comun import Parametros, cargar_parametros


@dataclass
class Dependencias:
    """Todo lo que los nodos necesitan del exterior. En las pruebas se reemplaza por dobles."""
    ruta_db: str
    chat: Any  # modelo de chat de LangChain: `await chat.ainvoke(mensajes)`; para salidas estructuradas
    texto: Any  # cliente de texto de app/clients: `await texto.generate(mensajes, config)`; para texto libre
    config_texto: ModelConfig
    retriever: Any | None = None  # búsqueda en los manuales (opcional)
    max_aclaraciones: int = 2
    max_pasos: int = 24  # nodos por consulta; al superarlo el grafo cierra de forma controlada
    max_pasos_bi: int = 4  # llamadas a herramientas que BI puede hacer antes de dictaminar
    max_pedidos_logistica: int = 10  # pedidos que Logística gestiona por consulta (los más atrasados primero)


class ErrorAgente(RuntimeError):
    """Un agente no pudo producir un resultado válido."""


class SalidaInvalida(ErrorAgente):
    """El LLM no devolvió algo que cumpla el contrato, ni tras el reintento."""


async def con_base(deps: Dependencias, funcion: Callable[[Any, Parametros], Any]):
    """Ejecuta `funcion(con, parametros)` en un hilo con una conexión de solo lectura propia.

    SQLite es síncrono: así no se bloquea el event loop, y cada hilo usa su propia conexión.
    """
    def correr():
        con = conectar(deps.ruta_db)
        try:
            return funcion(con, cargar_parametros(con))
        finally:
            con.close()

    return await asyncio.to_thread(correr)


def texto_de(mensaje) -> str:
    """El texto de la respuesta de un modelo de chat (algunos proveedores devuelven una lista de bloques)."""
    contenido = getattr(mensaje, "content", mensaje)
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "".join(b if isinstance(b, str) else b.get("text", "") for b in contenido)
    return str(contenido)


def _extraer_json(texto: str) -> str:
    texto = re.sub(r"```(?:json)?", "", texto).strip()
    inicio = min((i for i in (texto.find("{"), texto.find("[")) if i >= 0), default=-1)
    fin = max(texto.rfind("}"), texto.rfind("]"))
    if inicio < 0 or fin < inicio:
        raise ValueError("la respuesta no contiene un JSON")
    return texto[inicio:fin + 1]


def resumen_error(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(map(str, x['loc'])) or 'respuesta'}: {x['msg']}" for x in e.errors())[:400]
    return str(e)[:400]


async def pedir_json(chat, tipo, sistema: str, usuario: str, *, reintentos: int = 1):
    """Pide un JSON al modelo y lo valida con el contrato. Si no cumple, se lo devuelve con el error UNA vez.

    No se usa la salida estructurada nativa de cada proveedor: algunos no aceptan los contratos estrictos
    (`extra="forbid"`) y así el comportamiento es el mismo con cualquier proveedor.
    """
    adaptador = tipo if isinstance(tipo, TypeAdapter) else TypeAdapter(tipo)
    mensajes = [("system", sistema), ("human", usuario)]
    error = None
    for _ in range(reintentos + 1):
        texto = texto_de(await chat.ainvoke(mensajes))
        try:
            return adaptador.validate_json(_extraer_json(texto))
        except (ValidationError, ValueError) as e:
            error = e
            mensajes = mensajes + [("ai", texto), ("human", f"Tu respuesta no cumplió el formato: {resumen_error(e)}. "
                                                          "Responde solo con el JSON corregido.")]
    raise SalidaInvalida(f"El modelo no devolvió un JSON válido tras {reintentos + 1} intentos: {resumen_error(error)}")


def seguro(nombre: str):
    """Decorador de nodos: cualquier fallo se convierte en `error` en el state y el grafo cierra de forma controlada.

    Las pausas del grafo (interrupciones) NO son fallos y se dejan pasar.
    """
    def decorador(nodo):
        @wraps(nodo)
        async def envuelto(estado):
            try:
                return await nodo(estado)
            except GraphBubbleUp:
                raise
            except Exception as e:
                return {"error": f"{nombre}: {type(e).__name__}: {e}", "pasos": 1}
        return envuelto
    return decorador


def fecha_larga(fecha) -> str:
    """Una fecha como se escribe en un aviso: «8 de noviembre de 2026»."""
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    return f"{fecha.day} de {meses[fecha.month - 1]} de {fecha.year}"


def a_json(objeto: Any) -> str:
    return json.dumps(objeto, ensure_ascii=False, indent=1, default=str)


# ---------------------------------------------------------------- resiliencia del modelo de chat (LangChain)
log = logging.getLogger("uvicorn.error")
_CODIGOS_TRANSITORIOS = {408, 429, 500, 502, 503, 504}
_PISTAS_TRANSITORIAS = ("unavailable", "resource_exhausted", "high demand", "overloaded", "deadline exceeded",
                        "too many requests", "rate limit", "timed out")


def es_transitorio(exc: BaseException) -> bool:
    """¿Vale la pena reintentar? Sí si es saturación (503), cuota (429), error del servidor o un problema de red.

    Se revisa la cadena completa de causas porque LangChain y los SDK envuelven el error original. NO es transitorio:
    una clave inválida, un modelo que no existe, una petición mal formada o un JSON inválido del modelo.
    """
    visto: set[int] = set()
    e: BaseException | None = exc
    while e is not None and id(e) not in visto:
        visto.add(id(e))
        for atributo in ("code", "status_code"):
            codigo = getattr(e, atributo, None)
            if isinstance(codigo, int) and codigo in _CODIGOS_TRANSITORIOS:
                return True
        estado = getattr(getattr(e, "response", None), "status_code", None)
        if isinstance(estado, int) and estado in _CODIGOS_TRANSITORIOS:
            return True
        if isinstance(e, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.TransportError)):
            return True
        if isinstance(e, LLMError) and e.retryable:
            return True
        if any(pista in str(e).lower() for pista in _PISTAS_TRANSITORIAS):
            return True
        e = e.__cause__ or e.__context__
    return False


class ChatResiliente:
    """Envuelve un modelo de chat de LangChain: reintenta con espera creciente y, si sigue fallando, usa el de respaldo.

    Solo expone `ainvoke`, que es lo único que usan los agentes. Los errores que NO son transitorios (clave inválida,
    modelo inexistente...) se propagan de inmediato: reintentarlos solo gastaría tiempo.
    """

    def __init__(self, principal, respaldo=None, *, reintentos: int = 3, espera_base: float = 2.0, dormir=asyncio.sleep, azar=random.random):
        self.principal, self.respaldo = principal, respaldo
        self.reintentos, self.espera_base, self._dormir, self._azar = reintentos, espera_base, dormir, azar

    async def ainvoke(self, mensajes, *args, **kwargs):
        ultimo: BaseException | None = None
        for nombre, modelo in (("principal", self.principal), ("de respaldo", self.respaldo)):
            if modelo is None:
                continue
            for intento in range(self.reintentos + 1):
                try:
                    return await modelo.ainvoke(mensajes, *args, **kwargs)
                except Exception as e:
                    if not es_transitorio(e):
                        raise
                    ultimo = e
                    if intento < self.reintentos:
                        pausa = espera(intento + 1, self.espera_base, self._azar)
                        log.warning("Modelo %s no disponible (%s). Reintento %d de %d en %.1f s", nombre, type(e).__name__, intento + 1, self.reintentos, pausa)
                        await self._dormir(pausa)
            if nombre == "principal" and self.respaldo is not None:
                log.warning("El modelo principal sigue sin responder tras %d reintentos: se usa el de respaldo", self.reintentos)
        raise ultimo
