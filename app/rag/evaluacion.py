"""Evaluación de la recuperación con el conjunto de preguntas (data/eval/rag_conjunto_evaluacion.csv).

Métricas, para las preguntas que SÍ tienen respuesta en los manuales:
  - Acierto@k: ¿alguno de los fragmentos esperados aparece entre los k primeros resultados?
  - MRR: promedio de 1/posición del primer fragmento esperado (1 = siempre primero, 0 = nunca aparece).
Para las preguntas SIN respuesta no hay fragmento correcto: se reporta el puntaje del primer
resultado, para poder decidir un umbral bajo el cual el sistema diga «no hay información».
"""
import csv
import statistics
from dataclasses import dataclass
from pathlib import Path

from app.rag.retriever import Modo

PREGUNTAS = "data/eval/rag_conjunto_evaluacion.csv"
KS = (1, 3, 5)


@dataclass(frozen=True)
class Pregunta:
    id: str
    texto: str
    agente: str
    tipo: str
    esperados: frozenset[str]
    respuesta_clave: str

    @property
    def tiene_respuesta(self) -> bool:
        return bool(self.esperados)


def cargar_preguntas(ruta: str | Path = PREGUNTAS) -> list[Pregunta]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return [Pregunta(r["id"], r["pregunta"], r["agente_que_consulta"], r["tipo_prueba"],
                         frozenset(x for x in r["fragmentos_esperados"].split(";") if x), r["respuesta_clave"])
                for r in csv.DictReader(f)]


def acierto_en_k(ids: list[str], esperados: frozenset[str] | set[str], k: int) -> bool:
    return any(i in esperados for i in ids[:k])


def rango_reciproco(ids: list[str], esperados: frozenset[str] | set[str]) -> float:
    for pos, i in enumerate(ids, 1):
        if i in esperados:
            return 1.0 / pos
    return 0.0


def _resumen(filas: list[dict]) -> dict:
    n = len(filas)
    return {"n": n, **{f"acierto@{k}": sum(f[f"a{k}"] for f in filas) / n for k in KS}, "mrr": sum(f["rr"] for f in filas) / n}


async def evaluar(buscador, preguntas: list[Pregunta], *, modo: Modo, filtrar_por_agente: bool = False) -> dict:
    """Corre todas las preguntas. `buscador` debe tener `await buscar(consulta, k=, modo=, filtro=)`."""
    con_respuesta, sin_respuesta, fallos = [], [], []
    for p in preguntas:
        filtro = {"agente": p.agente} if filtrar_por_agente else None
        res = await buscador.buscar(p.texto, k=max(KS), modo=modo, filtro=filtro)
        ids = [r.id for r in res]
        if p.tiene_respuesta:
            fila = {"tipo": p.tipo, "rr": rango_reciproco(ids, p.esperados), **{f"a{k}": acierto_en_k(ids, p.esperados, k) for k in KS}}
            con_respuesta.append(fila)
            if not fila["a5"]:
                fallos.append({"id": p.id, "pregunta": p.texto, "esperados": sorted(p.esperados), "recuperados": ids[:3]})
        else:
            sin_respuesta.append({"id": p.id, "pregunta": p.texto, "puntaje_top": res[0].puntaje if res else 0.0})
    por_tipo = {t: _resumen([f for f in con_respuesta if f["tipo"] == t]) for t in sorted({f["tipo"] for f in con_respuesta})}
    return {
        "modo": modo, "filtrar_por_agente": filtrar_por_agente, "global": _resumen(con_respuesta), "por_tipo": por_tipo,
        "sin_respuesta": {"n": len(sin_respuesta),
                          "puntaje_top_promedio": statistics.mean(s["puntaje_top"] for s in sin_respuesta) if sin_respuesta else None,
                          "detalle": sin_respuesta},
        "fallos": fallos,
    }


def formatear(resultados: list[dict]) -> str:
    """Tabla de texto con un renglón por configuración evaluada."""
    lineas = [f"{'configuración':<28}{'n':>4}{'acierto@1':>11}{'acierto@3':>11}{'acierto@5':>11}{'MRR':>8}"]
    for r in resultados:
        nombre = r["modo"] + (" + filtro de agente" if r["filtrar_por_agente"] else "")
        g = r["global"]
        lineas.append(f"{nombre:<28}{g['n']:>4}{g['acierto@1']:>11.0%}{g['acierto@3']:>11.0%}{g['acierto@5']:>11.0%}{g['mrr']:>8.2f}")
    return "\n".join(lineas)
