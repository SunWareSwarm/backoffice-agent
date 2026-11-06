"""Herramienta de conocimiento: consulta los manuales (RAG) y devuelve fragmentos con su origen.

Devuelve siempre los fragmentos con su id y sección, para poder trazar de dónde salió cada regla. No
decide si la respuesta es suficiente: un umbral de puntaje no basta (ver docs/decisiones.md).
"""
from app.rag.retriever import Modo, Retriever
from app.schemas import FragmentoRecuperado, ResultadoConocimiento


async def buscar_en_manuales(retriever: Retriever, consulta: str, *, agente: str | None = None, k: int | None = None,
                             modo: Modo | None = None) -> ResultadoConocimiento:
    """`agente` aplica un filtro duro por metadato (opcional: por defecto se busca en todos los manuales)."""
    resultados = await retriever.buscar(consulta, k=k, modo=modo, filtro={"agente": agente} if agente else None)
    return ResultadoConocimiento(consulta=consulta, fragmentos=[
        FragmentoRecuperado(id=r.id, seccion=r.metadata["seccion"], texto=r.texto, puntaje=r.puntaje) for r in resultados])
