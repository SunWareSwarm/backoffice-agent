"""Evalúa la recuperación del RAG con las 47 preguntas.

    python -m scripts.eval_rag --solo-bm25      # sin claves ni Chroma: solo la búsqueda por palabras
    python -m scripts.eval_rag                  # los tres modos; necesita haber corrido la ingesta
    python -m scripts.eval_rag --modos hibrido --detalle
"""
import argparse
import asyncio

from app.rag.chunking import cargar_catalogo, fragmentar_corpus
from app.rag.evaluacion import cargar_preguntas, evaluar, formatear
from app.rag.ingest import CATALOGO, DIR_DOCS
from app.rag.retriever import IndiceBM25, Resultado, Retriever


class BuscadorBM25:
    """Buscador sin Chroma ni embeddings: arma el índice de palabras directamente desde los manuales."""

    def __init__(self):
        frs = fragmentar_corpus(DIR_DOCS, cargar_catalogo(CATALOGO))
        self._indice = IndiceBM25([f.id for f in frs], [f.texto for f in frs], [f.metadata for f in frs])
        self._datos = {f.id: (f.texto, f.metadata) for f in frs}

    async def buscar(self, consulta, *, k, modo="bm25", filtro=None):
        if modo != "bm25":
            raise ValueError("Sin embeddings solo se puede evaluar el modo bm25")
        return [Resultado(i, *self._datos[i], puntaje=p, posicion=n)
                for n, (i, p) in enumerate(self._indice.buscar(consulta, k, filtro), 1)]


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--solo-bm25", action="store_true")
    ap.add_argument("--modos", default="vectorial,bm25,hibrido")
    ap.add_argument("--detalle", action="store_true", help="muestra las preguntas que fallan")
    args = ap.parse_args()

    preguntas = cargar_preguntas()
    buscador = BuscadorBM25() if args.solo_bm25 else await Retriever.abrir()
    modos = ["bm25"] if args.solo_bm25 else args.modos.split(",")
    resultados = [await evaluar(buscador, preguntas, modo=m, filtrar_por_agente=f) for m in modos for f in (False, True)]
    print(formatear(resultados))
    for r in resultados:
        sr = r["sin_respuesta"]
        print(f"\n[{r['modo']}{' + filtro' if r['filtrar_por_agente'] else ''}] preguntas sin respuesta: puntaje promedio del primer resultado = {sr['puntaje_top_promedio']:.3f}")
        if args.detalle:
            for fallo in r["fallos"]:
                print(f"  falla {fallo['id']}: {fallo['pregunta']}  esperado {fallo['esperados']}  trajo {fallo['recuperados']}")


if __name__ == "__main__":
    asyncio.run(main())
