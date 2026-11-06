"""Ingesta de los manuales a Chroma. Necesita la clave del proveedor de embeddings en el .env.

    python -m scripts.ingest_rag
"""
import asyncio

from app.rag.ingest import ingestar


def main() -> None:
    r = asyncio.run(ingestar())
    print(f"Colección {r['coleccion']}: {r['fragmentos']} fragmentos, {r['dimension']} dimensiones (modelo {r['modelo']})")


if __name__ == "__main__":
    main()
