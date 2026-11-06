"""Recuperación: búsqueda vectorial, BM25 y su combinación, con filtros por metadatos (fase 4.3).

Tres modos:
  - vectorial: embedding de la consulta contra Chroma (distancia coseno). Entiende significado.
  - bm25: coincidencia de palabras. Fuerte con términos exactos (15:00, 30 %, 14 días).
  - hibrido: ambos, combinados con RRF (reciprocal rank fusion).

RRF: cada método ordena sus resultados y cada posición da puntos 1/(60 + posición). Se suman los
puntos de cada fragmento. Sirve porque no hace falta comparar puntajes de escalas distintas.

Los filtros son de igualdad sobre metadatos: {"agente": "logistica"} o varios campos a la vez.
"""
import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

import chromadb
from langchain_core.embeddings import Embeddings
from rank_bm25 import BM25Okapi

from app.config import Settings, get_settings
from app.llm import get_embeddings

Modo = Literal["vectorial", "bm25", "hibrido"]
K_RRF = 60
_PALABRAS_VACIAS = frozenset(
    "a al ante con contra de del desde el en entre es esta este esto ha hay la las lo los me mi nos o para por que "
    "qué se si sí sin sobre su sus te tu tus un una uno y ya como cómo cual cuál cuando cuándo cuanto cuánto cuantos cuántos cuantas cuántas".split()
)


class ErrorRecuperacion(RuntimeError):
    """No se pudo abrir la colección o la consulta no es compatible con ella."""


@dataclass(frozen=True)
class Resultado:
    id: str
    texto: str
    metadata: dict
    puntaje: float  # vectorial: similitud coseno · bm25: puntaje BM25 · hibrido: puntaje RRF
    posicion: int  # 1 = el mejor


def tokenizar(texto: str) -> list[str]:
    """Minúsculas, sin tildes, solo letras y números, sin palabras vacías y con plural simplificado."""
    plano = unicodedata.normalize("NFKD", texto.lower())
    plano = "".join(c for c in plano if not unicodedata.combining(c))
    fichas = []
    for t in re.findall(r"[a-z0-9]+", plano):
        if t in _PALABRAS_VACIAS:
            continue
        fichas.append(t[:-1] if len(t) > 3 and t.endswith("s") else t)
    return fichas


def cumple(metadata: dict, filtro: dict | None) -> bool:
    return not filtro or all(metadata.get(k) == v for k, v in filtro.items())


def donde(filtro: dict | None) -> dict | None:
    """Traduce un filtro simple a la sintaxis de Chroma."""
    if not filtro:
        return None
    partes = [{k: v} for k, v in filtro.items()]
    return partes[0] if len(partes) == 1 else {"$and": partes}


def fusion_rrf(rankings: list[list[str]], k_rrf: int = K_RRF) -> list[tuple[str, float]]:
    """Combina varios órdenes de ids en uno solo. Devuelve (id, puntaje) de mayor a menor."""
    puntos: dict[str, float] = {}
    for ranking in rankings:
        for pos, id_ in enumerate(ranking, 1):
            puntos[id_] = puntos.get(id_, 0.0) + 1.0 / (k_rrf + pos)
    return sorted(puntos.items(), key=lambda x: (-x[1], x[0]))


class IndiceBM25:
    """Índice de palabras en memoria. Con un corpus de decenas de fragmentos es instantáneo."""

    def __init__(self, ids: list[str], textos: list[str], metadatas: list[dict]):
        self.ids, self.metadatas = ids, metadatas
        self._bm25 = BM25Okapi([tokenizar(t) or ["_"] for t in textos])

    def buscar(self, consulta: str, n: int, filtro: dict | None = None) -> list[tuple[str, float]]:
        fichas = tokenizar(consulta)
        if not fichas:
            return []
        puntajes = self._bm25.get_scores(fichas)
        candidatos = [(i, p) for i, p in enumerate(puntajes) if cumple(self.metadatas[i], filtro) and p > 0]
        candidatos.sort(key=lambda x: (-x[1], x[0]))
        return [(self.ids[i], float(p)) for i, p in candidatos[:n]]


class Retriever:
    def __init__(self, coleccion, embeddings: Embeddings, *, k: int = 4, modo: Modo = "vectorial"):
        self._col, self._emb, self.k, self.modo = coleccion, embeddings, k, modo
        self._datos: dict[str, tuple[str, dict]] | None = None  # id -> (texto, metadata)
        self._bm25: IndiceBM25 | None = None

    @classmethod
    async def abrir(cls, settings: Settings | None = None, *, embeddings: Embeddings | None = None,
                    cliente: chromadb.ClientAPI | None = None, modo: Modo | None = None) -> "Retriever":
        s = settings or get_settings()
        cliente = cliente or chromadb.PersistentClient(path=s.chroma_path)
        try:
            col = await asyncio.to_thread(cliente.get_collection, s.rag_collection_name, embedding_function=None)
        except Exception as e:
            raise ErrorRecuperacion(
                f"No existe la colección {s.rag_collection_name!r}. Ejecuta primero la ingesta: python -m scripts.ingest_rag"
            ) from e
        return cls(col, embeddings or get_embeddings(s), k=s.rag_top_k, modo=modo or s.rag_modo)

    async def _cargar(self) -> None:
        if self._datos is None:
            crudo = await asyncio.to_thread(self._col.get, include=["documents", "metadatas"])
            self._datos = {i: (d, m) for i, d, m in zip(crudo["ids"], crudo["documents"], crudo["metadatas"])}
            self._bm25 = IndiceBM25(crudo["ids"], crudo["documents"], crudo["metadatas"])

    async def _vectorial(self, consulta: str, n: int, filtro: dict | None) -> list[tuple[str, float]]:
        vector = await self._emb.aembed_query(consulta)
        esperada = (self._col.metadata or {}).get("embedding_dimensions")
        if esperada and len(vector) != esperada:
            raise ErrorRecuperacion(
                f"La consulta tiene {len(vector)} dimensiones y la colección {esperada}: el modelo de embeddings cambió. "
                "Vuelve a ejecutar la ingesta."
            )
        r = await asyncio.to_thread(
            self._col.query, query_embeddings=[vector], n_results=min(n, len(self._datos)), where=donde(filtro),
            include=["distances"],
        )
        return [(i, 1.0 - d) for i, d in zip(r["ids"][0], r["distances"][0])]

    async def buscar(self, consulta: str, *, k: int | None = None, modo: Modo | None = None,
                     filtro: dict | None = None) -> list[Resultado]:
        k, modo = k or self.k, modo or self.modo
        await self._cargar()
        n = max(k * 3, 10)  # candidatos de cada método antes de combinar
        if modo == "vectorial":
            ranking = (await self._vectorial(consulta, k, filtro))[:k]
        elif modo == "bm25":
            ranking = self._bm25.buscar(consulta, k, filtro)
        elif modo == "hibrido":
            vec, lex = await self._vectorial(consulta, n, filtro), self._bm25.buscar(consulta, n, filtro)
            ranking = fusion_rrf([[i for i, _ in vec], [i for i, _ in lex]])[:k]
        else:
            raise ValueError(f"Modo desconocido: {modo!r}")
        return [Resultado(i, *self._datos[i], puntaje=p, posicion=pos) for pos, (i, p) in enumerate(ranking, 1)]
