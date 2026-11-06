"""Pruebas de la evaluación: las métricas, el conjunto de preguntas y la línea base de BM25."""
import asyncio

import pytest

from app.rag.evaluacion import acierto_en_k, cargar_preguntas, evaluar, formatear, rango_reciproco
from app.rag.retriever import Resultado
from scripts.eval_rag import BuscadorBM25


def test_el_conjunto_tiene_47_preguntas_y_5_sin_respuesta():
    ps = cargar_preguntas()
    assert len(ps) == 47 and sum(1 for p in ps if not p.tiene_respuesta) == 5
    q13 = next(p for p in ps if p.id == "Q13")
    assert q13.esperados == {"LOG-6", "LOG-10.q2", "MAR-5", "MAR-7.q7"} and q13.agente == "logistica"


def test_acierto_en_k_y_rango_reciproco():
    esperados = {"X", "Y"}
    assert acierto_en_k(["a", "b", "X"], esperados, 3) and not acierto_en_k(["a", "b", "X"], esperados, 2)
    assert rango_reciproco(["a", "Y", "X"], esperados) == 0.5 and rango_reciproco(["a"], esperados) == 0.0


class Buscador:
    """Buscador de mentira: devuelve lo que se le indique y recuerda con qué filtro lo llamaron."""

    def __init__(self, respuesta):
        self.respuesta, self.filtros = respuesta, []

    async def buscar(self, consulta, *, k, modo, filtro=None):
        self.filtros.append(filtro)
        ids = self.respuesta(consulta)[:k]
        return [Resultado(i, "", {}, puntaje=1.0 / n, posicion=n) for n, i in enumerate(ids, 1)]


async def test_un_buscador_perfecto_acierta_todo_y_uno_malo_nada():
    ps = cargar_preguntas()
    por_texto = {p.texto: sorted(p.esperados) for p in ps}
    perfecto = await evaluar(Buscador(lambda q: por_texto[q] or ["LOG-0"]), ps, modo="bm25")
    assert perfecto["global"]["acierto@1"] == perfecto["global"]["mrr"] == 1.0 and perfecto["fallos"] == []
    malo = await evaluar(Buscador(lambda q: ["NADA"]), ps, modo="bm25")
    assert malo["global"]["acierto@5"] == 0.0 and len(malo["fallos"]) == 42


async def test_el_filtro_de_agente_solo_se_usa_si_se_pide():
    ps = cargar_preguntas()[:3]
    b = Buscador(lambda q: ["a"])
    await evaluar(b, ps, modo="bm25")
    await evaluar(b, ps, modo="bm25", filtrar_por_agente=True)
    assert b.filtros[:3] == [None] * 3 and all(f == {"agente": p.agente} for f, p in zip(b.filtros[3:], ps))


async def test_las_preguntas_sin_respuesta_se_reportan_aparte():
    r = await evaluar(Buscador(lambda q: ["LOG-0"]), cargar_preguntas(), modo="bm25")
    assert r["sin_respuesta"]["n"] == 5 and r["global"]["n"] == 42
    assert r["sin_respuesta"]["puntaje_top_promedio"] == 1.0


# ---------------------------------------------------------------- línea base real (sin claves ni red)
@pytest.fixture(scope="module")
def base_bm25():
    async def correr():
        b = BuscadorBM25()
        return {f: await evaluar(b, cargar_preguntas(), modo="bm25", filtrar_por_agente=f) for f in (False, True)}
    return asyncio.run(correr())


def test_linea_base_de_bm25_sobre_los_manuales_reales(base_bm25):
    g = base_bm25[False]["global"]
    assert g["n"] == 42
    # Piso para detectar regresiones. Ojo: las preguntas las escribió quien escribió los manuales,
    # así que el vocabulario coincide más de lo normal y esta base es optimista.
    assert g["acierto@3"] >= 0.95 and g["acierto@5"] >= 0.95 and g["mrr"] >= 0.85


def test_el_filtro_duro_por_agente_excluye_conocimiento_que_si_servia(base_bm25):
    """Hallazgo de la evaluación: Q12 la hace el Redactor, pero su respuesta está etiquetada como de logística."""
    sin = {f["id"] for f in base_bm25[False]["fallos"]}
    con = {f["id"] for f in base_bm25[True]["fallos"]}
    assert "Q12" not in sin and "Q12" in con


def test_el_reporte_tiene_una_fila_por_configuracion():
    texto = formatear([{"modo": "bm25", "filtrar_por_agente": False,
                        "global": {"n": 42, "acierto@1": 0.8, "acierto@3": 1.0, "acierto@5": 1.0, "mrr": 0.9}}])
    assert "bm25" in texto and "80%" in texto and "100%" in texto and "0.90" in texto
