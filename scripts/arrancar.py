"""Arranque con UN solo comando: prepara lo que falte (base simulada y RAG) y levanta la API.

    docker compose up --build              # lo normal: Redis, preparación y API, todo junto
    python -m scripts.arrancar             # sin Docker (necesita Redis: docker compose up -d redis)
    python -m scripts.arrancar --reiniciar # borra y vuelve a crear la base simulada y el RAG

Es IDEMPOTENTE: cada paso comprueba si ya está hecho y lo omite. La primera vez tarda un poco más (la ingesta calcula los
embeddings de los 61 fragmentos de los manuales); las siguientes arranca en segundos.
"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

import chromadb
from pydantic import ValidationError

from app.agents.comun import es_transitorio
from app.config import Settings, get_settings
from app.db.connection import conectar
from app.db.seed import generar
from app.rag.chunking import cargar_catalogo, fragmentar_corpus
from app.rag.ingest import CATALOGO, DIR_DOCS, ingestar

ESPERAS_INGESTA = (5, 15)  # segundos entre los intentos de la ingesta ante un error pasajero (cuota o saturación)
SEMILLA = 42


def base_lista(ruta: str | Path) -> bool:
    """¿La base simulada existe y está completa? Una base a medias (un arranque interrumpido) cuenta como no lista."""
    if not Path(ruta).exists():
        return False
    try:
        con = conectar(ruta)
        try:
            fecha = con.execute("SELECT valor FROM parametros WHERE clave = 'fecha_referencia'").fetchone()
            return fecha is not None and con.execute("SELECT COUNT(*) FROM pedidos").fetchone()[0] > 0
        finally:
            con.close()
    except Exception:
        return False


def rag_listo(s: Settings, esperados: int) -> bool:
    """¿La colección del RAG existe con TODOS los fragmentos? Cambiar de modelo de embeddings da otro nombre de colección: no está lista."""
    try:
        coleccion = chromadb.PersistentClient(path=s.chroma_path).get_collection(s.rag_collection_name, embedding_function=None)
        return coleccion.count() == esperados
    except Exception:
        return False


async def preparar(s: Settings, *, forzar: bool = False, embeddings=None, dormir=asyncio.sleep, log=print) -> dict:
    """Deja lista la base simulada y el RAG. Devuelve qué se hizo en cada uno."""
    informe = {}

    if forzar or not base_lista(s.sqlite_path):
        log(f"[1/2] Creando la base simulada en {s.sqlite_path} (fecha simulada: {s.fecha_simulada})...")
        resumen = await asyncio.to_thread(generar, s.sqlite_path, s.fecha_simulada, SEMILLA)
        log(f"      listo: {resumen['productos']} productos y {resumen['pedidos']} pedidos")
        informe["base"] = "creada"
    else:
        log(f"[1/2] Base simulada: ya existe ({s.sqlite_path})")
        informe["base"] = "ya existía"

    # Falla aquí, con un mensaje claro, si un manual y el catálogo no coinciden: no se indexa nada a medias.
    esperados = len(fragmentar_corpus(DIR_DOCS, cargar_catalogo(CATALOGO)))
    if forzar or not rag_listo(s, esperados):
        log(f"[2/2] Indexando los manuales en el RAG ({esperados} fragmentos; la primera vez tarda un poco)...")
        for intento in range(len(ESPERAS_INGESTA) + 1):
            try:
                r = await ingestar(settings=s, embeddings=embeddings)
                break
            except Exception as e:
                if not es_transitorio(e) or intento == len(ESPERAS_INGESTA):
                    raise
                log(f"      el proveedor no respondió ({type(e).__name__}); reintento en {ESPERAS_INGESTA[intento]} s")
                await dormir(ESPERAS_INGESTA[intento])
        log(f"      listo: colección {r['coleccion']}, {r['fragmentos']} fragmentos")
        informe["rag"] = "ingestado"
    else:
        log(f"[2/2] RAG: ya está indexado ({esperados} fragmentos)")
        informe["rag"] = "ya existía"
    return informe


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1", help="en Docker se usa 0.0.0.0")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reiniciar", action="store_true", help="vuelve a crear la base simulada y el RAG")
    ap.add_argument("--solo-preparar", action="store_true", help="prepara los datos y termina, sin levantar la API")
    args = ap.parse_args()

    try:
        ajustes = get_settings()
    except ValidationError as e:
        faltan = ", ".join(str(err["loc"][0]).upper() for err in e.errors())
        sys.exit(f"Falta configuración en el .env o en el entorno: {faltan}\nCopia .env.example a .env y complétalo.")

    inicio = time.perf_counter()
    try:
        asyncio.run(preparar(ajustes, forzar=args.reiniciar))
    except Exception as e:
        sys.exit(f"No se pudo preparar el sistema: {type(e).__name__}: {e}")
    print(f"Todo listo en {time.perf_counter() - inicio:.1f} s.")
    if args.solo_preparar:
        return

    import uvicorn

    print(f"Levantando la API en http://{args.host}:{args.port}  (documentación en /docs)")
    uvicorn.run("app.api.main:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
