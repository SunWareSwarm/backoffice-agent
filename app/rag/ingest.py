"""Ingesta del RAG: corta los manuales, calcula embeddings y guarda en Chroma (fase 4.3).

Se corre antes de usar el sistema, no en cada consulta:  python -m scripts.ingest_rag

Decisiones:
  - Distancia coseno, fijada explícitamente al crear la colección (Chroma usa L2 por defecto).
  - Se pasan siempre los embeddings propios. Si no se pasaran, Chroma usaría en silencio su modelo
    por defecto y se mezclarían vectores de modelos distintos.
  - El nombre de la colección incluye proveedor y modelo (config.py): cambiar de modelo crea una
    colección nueva en vez de mezclar.
  - Las llamadas a Chroma son síncronas, así que se ejecutan en un hilo para no bloquear el event loop.
"""
import asyncio
from pathlib import Path

import chromadb
from langchain_core.embeddings import Embeddings

from app.config import Settings, get_settings
from app.llm import get_embeddings
from app.rag.chunking import cargar_catalogo, fragmentar_corpus

DIR_DOCS = "data/docs_rag"
CATALOGO = "data/eval/rag_catalogo_fragmentos.csv"
TAMANO_LOTE = 32  # fragmentos por llamada de embeddings


async def ingestar(
    *,
    settings: Settings | None = None,
    embeddings: Embeddings | None = None,
    cliente: chromadb.ClientAPI | None = None,
    dir_docs: str | Path = DIR_DOCS,
    catalogo: str | Path = CATALOGO,
    tamano_lote: int = TAMANO_LOTE,
    reiniciar: bool = True,
) -> dict:
    """Ingesta completa. Con `reiniciar` (por defecto) la colección se borra y se crea de nuevo."""
    s = settings or get_settings()
    emb = embeddings or get_embeddings(s)
    cliente = cliente or chromadb.PersistentClient(path=s.chroma_path)

    fragmentos = fragmentar_corpus(dir_docs, cargar_catalogo(catalogo))  # falla si algo no coincide

    vectores: list[list[float]] = []
    textos = [f.texto for f in fragmentos]
    for i in range(0, len(textos), tamano_lote):
        vectores.extend(await emb.aembed_documents(textos[i:i + tamano_lote]))
    if len(vectores) != len(fragmentos):
        raise RuntimeError(f"Se pidieron {len(fragmentos)} embeddings y llegaron {len(vectores)}")
    dimension = len(vectores[0])
    if any(len(v) != dimension for v in vectores):
        raise RuntimeError("Los embeddings no tienen todos la misma dimensión")

    nombre = s.rag_collection_name
    if reiniciar:
        try:
            await asyncio.to_thread(cliente.delete_collection, nombre)
        except Exception:  # no existía: no hay nada que borrar
            pass
    coleccion = await asyncio.to_thread(
        cliente.get_or_create_collection,
        nombre,
        configuration={"hnsw": {"space": "cosine"}},
        metadata={"embedding_provider": s.embedding_provider, "embedding_model": s.embedding_model,
                  "embedding_dimensions": dimension},
        embedding_function=None,
    )
    await asyncio.to_thread(
        coleccion.upsert,
        ids=[f.id for f in fragmentos],
        documents=textos,
        embeddings=vectores,
        metadatas=[f.metadata for f in fragmentos],
    )
    return {"coleccion": nombre, "fragmentos": len(fragmentos), "dimension": dimension, "modelo": s.embedding_model}
