"""Verifica, eslabón por eslabón, que el entorno real funciona. Úsalo antes de la primera ingesta.

    python -m scripts.verificar_entorno            # prueba todo, incluidas las llamadas reales a los modelos
    python -m scripts.verificar_entorno --sin-chat # omite la llamada al modelo de texto (ahorra una llamada)

Cada verificación depende de las anteriores: si una falla, las que dependen de ella se omiten y se
explica por qué. Hace 2 llamadas reales a la API de embeddings y, salvo --sin-chat, 1 al modelo de texto.
"""
import argparse
import asyncio
import sys
import tempfile
from dataclasses import dataclass

import chromadb
from pydantic import ValidationError

from app.clients import AsyncLLMManager, ChatMessage, ModelConfig
from app.config import Settings, get_settings
from app.llm import get_embeddings
from app.observability import init_observability
from app.rag.chunking import cargar_catalogo, fragmentar_corpus
from app.rag.ingest import CATALOGO, DIR_DOCS


@dataclass
class Verificacion:
    nombre: str
    estado: str  # "ok", "falla" u "omitida"
    detalle: str


def _faltantes(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, err['loc'])) or 'configuración'}: {err['msg']}" for err in e.errors())


async def verificar(settings: Settings | None = None, *, embeddings=None, cliente_texto=None, con_chat: bool = True,
                    dir_docs=DIR_DOCS, catalogo=CATALOGO, revisar_trazas: bool = False) -> list[Verificacion]:
    out: list[Verificacion] = []

    def ok(n, d): out.append(Verificacion(n, "ok", d))
    def falla(n, d): out.append(Verificacion(n, "falla", d))
    def omite(n, por): out.append(Verificacion(n, "omitida", f"depende de «{por}», que falló"))

    # 1) manuales y catálogo (no necesitan configuración ni red)
    try:
        ok("Manuales y catálogo", f"{len(fragmentar_corpus(dir_docs, cargar_catalogo(catalogo)))} fragmentos, coinciden con el catálogo")
    except Exception as e:
        falla("Manuales y catálogo", str(e))

    # 2) configuración
    try:
        s = settings or get_settings()
        ok("Configuración (.env)", f"chat: {s.llm_provider}/{s.llm_model} · embeddings: {s.embedding_provider}/{s.embedding_model}")
    except ValidationError as e:
        falla("Configuración (.env)", _faltantes(e))
        for n in ("Modelo de texto", "Embeddings", "Chroma"):
            omite(n, "Configuración (.env)")
        return out

    # 2b) trazas: LangSmith lee su configuración del entorno; esto carga el .env y comprueba que las trazas quedaron activas
    if revisar_trazas:
        o = init_observability()
        if o["enabled"]:
            ok("Trazas (LangSmith)", f"activas, proyecto '{o['project']}'")
        else:
            out.append(Verificacion("Trazas (LangSmith)", "omitida", "desactivadas: define LANGSMITH_TRACING=true y LANGSMITH_API_KEY en el .env"))

    # 3) modelo de texto (y, si hay, el de respaldo: un respaldo mal escrito solo fallaría justo cuando se lo necesita)
    if not con_chat:
        out.append(Verificacion("Modelo de texto", "omitida", "pedido con --sin-chat"))
        if s.llm_fallback_model:
            out.append(Verificacion("Modelo de respaldo", "omitida", "pedido con --sin-chat"))
    else:
        try:
            cliente = cliente_texto or AsyncLLMManager.get_client(s)
            r = await cliente.generate([ChatMessage(role="user", content="Responde solo con la palabra: ok")],
                                       ModelConfig.from_settings(s, max_tokens=20, temperature=0))
            ok("Modelo de texto", f"respondió en {r.latency_ms:.0f} ms ({r.prompt_tokens} tokens de entrada, {r.completion_tokens} de salida)")
        except Exception as e:
            falla("Modelo de texto", f"{type(e).__name__}: {e}")
            cliente = None
        if s.llm_fallback_model and cliente is not None:
            try:
                r = await cliente.generate([ChatMessage(role="user", content="Responde solo con la palabra: ok")],
                                           ModelConfig.from_settings(s, model_name=s.llm_fallback_model, max_tokens=20, temperature=0))
                ok("Modelo de respaldo", f"{s.llm_fallback_model} respondió en {r.latency_ms:.0f} ms")
            except Exception as e:
                falla("Modelo de respaldo", f"{s.llm_fallback_model}: {type(e).__name__}: {e}")

    # 4) embeddings
    vectores = None
    try:
        emb = embeddings or get_embeddings(s)
        vectores = await emb.aembed_documents(["manual de operaciones logísticas", "orden de compra"])
        consulta = await emb.aembed_query("¿cuál es el corte de despacho?")
        if len(vectores[0]) != len(consulta):
            raise RuntimeError(f"documento y consulta tienen dimensiones distintas ({len(vectores[0])} y {len(consulta)})")
        ok("Embeddings", f"dimensión {len(consulta)}, modelo {s.embedding_model}")
    except Exception as e:
        falla("Embeddings", f"{type(e).__name__}: {e}")

    # 5) Chroma: se crea una colección temporal con coseno, se guarda un vector y se busca
    if vectores is None:
        omite("Chroma", "Embeddings")
    else:
        try:
            # ignore_cleanup_errors: en Windows, Chroma mantiene abierto su archivo mientras vive el proceso y el borrado de la
            # carpeta temporal falla con PermissionError aunque todo haya funcionado. Eso no es una falla de Chroma.
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                col = chromadb.PersistentClient(path=tmp).create_collection(
                    "verificacion", configuration={"hnsw": {"space": "cosine"}}, embedding_function=None)
                col.add(ids=["a", "b"], embeddings=vectores, documents=["a", "b"])
                r = col.query(query_embeddings=[vectores[0]], n_results=1, include=["distances"])
                if r["ids"][0][0] != "a" or r["distances"][0][0] > 1e-3:
                    raise RuntimeError("la búsqueda no devolvió el vector que se acababa de guardar")
            ok("Chroma", "escribe y busca con distancia coseno")
        except Exception as e:
            falla("Chroma", f"{type(e).__name__}: {e}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sin-chat", action="store_true")
    args = ap.parse_args()
    resultados = asyncio.run(verificar(con_chat=not args.sin_chat, revisar_trazas=True))
    marca = {"ok": "✓", "falla": "✗", "omitida": "·"}
    for v in resultados:
        print(f"{marca[v.estado]} {v.nombre}: {v.detalle}")
    fallas = [v for v in resultados if v.estado == "falla"]
    print("\nTodo listo para la ingesta." if not fallas else f"\n{len(fallas)} verificación(es) fallaron: corrige y vuelve a correr.")
    sys.exit(1 if fallas else 0)


if __name__ == "__main__":
    main()
