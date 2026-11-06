"""Pruebas del corte por estructura: debe producir exactamente lo que dice el catálogo."""
from pathlib import Path

import pytest

from app.rag.chunking import (
    DOCUMENTOS, ErrorDeFragmentacion, cargar_catalogo, fragmentar_corpus, fragmentar_documento,
)

DOCS = Path("data/docs_rag")
CATALOGO = cargar_catalogo("data/eval/rag_catalogo_fragmentos.csv")
LOG = (DOCS / "manual_operaciones_logisticas.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fragmentos():
    return fragmentar_corpus(DOCS, CATALOGO)


def log(markdown):
    return fragmentar_documento(markdown, "LOG", "manual_logistico", CATALOGO)


def test_produce_exactamente_los_61_fragmentos_del_catalogo(fragmentos):
    assert [f.id for f in fragmentos] and {f.id for f in fragmentos} == set(CATALOGO)
    assert len(fragmentos) == len({f.id for f in fragmentos}) == 61


def test_cantidad_por_documento_y_por_tipo(fragmentos):
    por_doc = {d: sum(1 for f in fragmentos if f.metadata["documento"] == d) for _, d in DOCUMENTOS.values()}
    assert por_doc == {"manual_logistico": 29, "manual_ordenes_compra": 17, "guia_marca_precios": 15}
    por_tipo = {t: sum(1 for f in fragmentos if f.metadata["tipo_contenido"] == t) for t in
                ("regla", "tabla", "ejemplo", "pregunta_frecuente")}
    assert por_tipo == {"regla": 21, "tabla": 9, "ejemplo": 8, "pregunta_frecuente": 23}


def test_cada_pregunta_frecuente_es_un_fragmento_propio(fragmentos):
    faq = [f for f in fragmentos if f.metadata["tipo_contenido"] == "pregunta_frecuente"]
    assert len(faq) == 23 and all(f.texto.count("¿") >= 1 for f in faq)
    una = next(f for f in faq if f.id == "LOG-10.q4")
    assert "Durante los primeros 3 días" in una.texto and "plazo de entrega según la zona" not in una.texto


def test_las_tablas_quedan_enteras_junto_a_su_titulo(fragmentos):
    f = next(x for x in fragmentos if x.id == "LOG-2")
    filas = [l for l in f.texto.splitlines() if l.startswith("|")]
    assert len(filas) == 9  # encabezado, separador y los 7 parámetros
    for parametro in ("Corte de despacho", "Capacidad de preparación", "Lead time del proveedor", "Mensajes para pedir datos"):
        assert parametro in f.texto
    assert f.texto.startswith("Manual de operaciones logísticas > 2. Parámetros operativos")


def test_cada_ejemplo_de_aviso_es_un_fragmento_y_sin_marcas_de_cita(fragmentos):
    ejemplos = [f for f in fragmentos if f.metadata["tipo_contenido"] == "ejemplo"]
    assert len(ejemplos) == 8
    ultimo = next(f for f in ejemplos if f.id == "LOG-8.4")
    assert "tercera vez" in ultimo.texto and "\n>" not in ultimo.texto and not ultimo.texto.split("\n\n")[1].startswith(">")
    assert ultimo.texto.startswith("Manual de operaciones logísticas > 8. Cómo redactar los avisos al cliente > Ejemplo:")


def test_el_titulo_viaja_dentro_del_texto_y_no_quedan_negritas(fragmentos):
    for f in fragmentos:
        assert " > " in f.texto.split("\n")[0] or f.id.endswith("-0")
        assert "**" not in f.texto


def test_los_metadatos_son_de_tipo_simple_y_estan_completos(fragmentos):
    for f in fragmentos:
        assert set(f.metadata) == {"documento", "seccion", "tipo_contenido", "agente", "causa"}
        assert all(isinstance(v, str) and v for v in f.metadata.values())  # Chroma no admite None


def test_las_subsecciones_de_causa_llevan_su_numero_de_causa(fragmentos):
    causas = {f.id: f.metadata["causa"] for f in fragmentos if f.id.startswith("LOG-5.") and f.id != "LOG-5.0"}
    assert causas == {"LOG-5.1": "1", "LOG-5.2": "2", "LOG-5.3": "3", "LOG-5.4": "4", "LOG-5.5": "5"}


# ---------------------------------------------------------------- si el manual y el catálogo no coinciden, falla
def test_falla_si_a_un_manual_le_falta_una_seccion():
    roto = LOG.replace("## 9. Casos que requieren revisión humana", "## 9. Otra cosa")
    with pytest.raises(ErrorDeFragmentacion, match="título del manual"):
        log(roto)
    sin_causa5 = LOG.split("### Causa 5")[0] + "## 6. Compensaciones" + LOG.split("## 6. Compensaciones", 1)[1]
    with pytest.raises(ErrorDeFragmentacion, match="faltan en el manual"):
        log(sin_causa5)


def test_falla_si_el_manual_tiene_una_seccion_que_el_catalogo_no_conoce():
    con_extra = LOG + "\n## 11. Sección nueva\n\nTexto.\n"
    with pytest.raises(ErrorDeFragmentacion, match="no están en el catálogo"):
        log(con_extra)


def test_falla_si_cambia_el_tipo_de_contenido():
    sin_tabla = LOG.replace("| Parámetro | Valor | Qué significa |\n| --- | --- | --- |", "Parámetro, valor y significado:")
    with pytest.raises(ErrorDeFragmentacion, match="tipo"):
        log(sin_tabla)


def test_falla_si_una_seccion_no_esta_numerada_o_las_preguntas_no_tienen_formato():
    with pytest.raises(ErrorDeFragmentacion, match="numerado"):
        log(LOG.replace("## 4. Cuándo un pedido está atrasado", "## Cuándo un pedido está atrasado"))
    with pytest.raises(ErrorDeFragmentacion, match="preguntas"):
        log(LOG.split("## 10. Preguntas frecuentes")[0] + "## 10. Preguntas frecuentes\n\nTexto sin formato de pregunta.\n")


def test_falla_si_falta_un_manual(tmp_path):
    (tmp_path / "manual_ordenes_compra.md").write_text("# x", encoding="utf-8")
    with pytest.raises(ErrorDeFragmentacion, match="Faltan manuales"):
        fragmentar_corpus(tmp_path, CATALOGO)
