"""Pruebas de ingesta y recuperación, con embeddings falsos (sin red, sin claves, sin costo)."""
import chromadb
import pytest

from app.config import Settings
from app.rag.chunking import ErrorDeFragmentacion
from app.rag.ingest import ingestar
from app.rag.retriever import ErrorRecuperacion, Retriever, donde, fusion_rrf, tokenizar
from tests.fakes import EmbeddingsHash


@pytest.fixture
def ajustes(env, tmp_path):
    env.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    return Settings(_env_file=None)


@pytest.fixture
def cliente(ajustes):
    return chromadb.PersistentClient(path=ajustes.chroma_path)


@pytest.fixture
async def listo(ajustes, cliente):
    emb = EmbeddingsHash()
    resumen = await ingestar(settings=ajustes, embeddings=emb, cliente=cliente)
    return Retriever(cliente.get_collection(ajustes.rag_collection_name, embedding_function=None), emb, k=4), resumen, emb


# ---------------------------------------------------------------- funciones puras
def test_tokenizar_quita_tildes_plurales_y_palabras_vacias():
    assert tokenizar("¿Cuántos pedidos tenemos en el corte de las 15:00?") == ["pedido", "tenemo", "corte", "15", "00"]
    assert tokenizar("de la el y") == []


def test_fusion_rrf_premia_lo_que_aparece_en_los_dos_ordenes():
    r = fusion_rrf([["a", "b"], ["b", "c"]])
    assert [i for i, _ in r] == ["b", "a", "c"]  # b aparece en los dos órdenes: gana aunque nunca fue primero en ambos
    assert dict(r)["b"] == pytest.approx(1 / 62 + 1 / 61) and dict(r)["a"] == pytest.approx(1 / 61)
    assert fusion_rrf([]) == []


def test_donde_traduce_filtros_a_la_sintaxis_de_chroma():
    assert donde(None) is None and donde({}) is None
    assert donde({"agente": "bi"}) == {"agente": "bi"}
    assert donde({"agente": "bi", "causa": "2"}) == {"$and": [{"agente": "bi"}, {"causa": "2"}]}


# ---------------------------------------------------------------- ingesta
async def test_la_ingesta_crea_la_coleccion_con_coseno_y_todos_los_fragmentos(listo, cliente, ajustes):
    _, resumen, _ = listo
    col = cliente.get_collection(ajustes.rag_collection_name, embedding_function=None)
    assert resumen == {"coleccion": "rag_google_embedding-de-prueba", "fragmentos": 61, "dimension": 128,
                       "modelo": "embedding-de-prueba"}
    assert col.count() == 61
    assert col.configuration["hnsw"]["space"] == "cosine"  # no el L2 por defecto de Chroma
    assert col.metadata["embedding_model"] == "embedding-de-prueba" and col.metadata["embedding_dimensions"] == 128


async def test_la_ingesta_guarda_texto_y_metadatos(listo, cliente, ajustes):
    col = cliente.get_collection(ajustes.rag_collection_name, embedding_function=None)
    r = col.get(ids=["LOG-6"], include=["documents", "metadatas"])
    assert "cupón de 30 %" in r["documents"][0]
    assert r["metadatas"][0] == {"documento": "manual_logistico", "seccion": "6. Compensaciones por atraso",
                                 "tipo_contenido": "regla", "agente": "logistica", "causa": "n/a"}


async def test_los_embeddings_se_piden_por_lotes(listo):
    _, _, emb = listo
    assert emb.llamadas_documentos == [32, 29]  # 61 fragmentos en lotes de 32


async def test_volver_a_ingestar_no_duplica(ajustes, cliente):
    emb = EmbeddingsHash()
    await ingestar(settings=ajustes, embeddings=emb, cliente=cliente)
    await ingestar(settings=ajustes, embeddings=emb, cliente=cliente)
    await ingestar(settings=ajustes, embeddings=emb, cliente=cliente, reiniciar=False)
    assert cliente.get_collection(ajustes.rag_collection_name, embedding_function=None).count() == 61


async def test_si_un_manual_no_coincide_con_el_catalogo_no_se_crea_nada(ajustes, cliente, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    with pytest.raises(ErrorDeFragmentacion):
        await ingestar(settings=ajustes, embeddings=EmbeddingsHash(), cliente=cliente, dir_docs=docs)
    assert [c.name for c in cliente.list_collections()] == []


async def test_cambiar_el_modelo_de_embeddings_apunta_a_otra_coleccion(listo, ajustes, cliente, env):
    env.setenv("EMBEDDING_MODEL", "otro-modelo")
    nuevos = Settings(_env_file=None)
    with pytest.raises(ErrorRecuperacion, match="Ejecuta primero la ingesta"):
        await Retriever.abrir(nuevos, embeddings=EmbeddingsHash(), cliente=cliente)


# ---------------------------------------------------------------- recuperación
async def test_busqueda_vectorial_devuelve_k_resultados_ordenados(listo):
    r, _, emb = listo
    consulta = ("Parámetros operativos: corte de despacho, capacidad de preparación, plazo de entrega al cliente, "
                "lead time del proveedor, compensación por atraso, espera por falta de stock, mensajes para pedir datos")
    res = await r.buscar(consulta, k=3, modo="vectorial")
    assert [x.posicion for x in res] == [1, 2, 3]
    assert res[0].puntaje >= res[1].puntaje >= res[2].puntaje
    assert res[0].id == "LOG-2" and emb.llamadas_consulta == 1


async def test_bm25_encuentra_terminos_exactos_sin_usar_embeddings(listo):
    r, _, emb = listo
    res = await r.buscar("cupón de 30 % de descuento", k=3, modo="bm25")
    assert res and emb.llamadas_consulta == 0
    assert {x.id for x in res} & {"LOG-6", "MAR-5", "LOG-10.q3", "MAR-7.q7", "LOG-10.q2"}  # los fragmentos que hablan del cupón
    assert any("30 %" in x.texto for x in res)


async def test_hibrido_combina_los_dos_metodos(listo):
    r, _, emb = listo
    res = await r.buscar("¿Qué se le dice al cliente cuando falta stock?", k=4, modo="hibrido")
    assert 1 <= len(res) <= 4 and emb.llamadas_consulta == 1
    assert [x.posicion for x in res] == list(range(1, len(res) + 1))


@pytest.mark.parametrize("modo", ["vectorial", "bm25", "hibrido"])
async def test_el_filtro_por_metadatos_se_cumple_en_los_tres_modos(listo, modo):
    r, _, _ = listo
    res = await r.buscar("orden de compra y proveedor", k=5, modo=modo, filtro={"agente": "inventario"})
    assert res and all(x.metadata["agente"] == "inventario" for x in res)
    res2 = await r.buscar("avisos al cliente", k=5, modo=modo, filtro={"agente": "redactor", "tipo_contenido": "ejemplo"})
    assert res2 and all(x.metadata["tipo_contenido"] == "ejemplo" and x.metadata["agente"] == "redactor" for x in res2)


async def test_una_consulta_de_pura_palabra_vacia_no_devuelve_nada_en_bm25(listo):
    r, _, _ = listo
    assert await r.buscar("de la el y", k=3, modo="bm25") == []


async def test_modo_desconocido_y_dimension_distinta_dan_errores_claros(listo):
    r, _, _ = listo
    with pytest.raises(ValueError, match="Modo desconocido"):
        await r.buscar("hola", modo="magico")
    r._emb = EmbeddingsHash(dim=64)  # simula un cambio de modelo sin volver a ingestar
    with pytest.raises(ErrorRecuperacion, match="dimensiones"):
        await r.buscar("hola mundo", modo="vectorial")


async def test_abrir_sin_ingesta_previa_explica_que_hacer(ajustes, cliente):
    with pytest.raises(ErrorRecuperacion, match="python -m scripts.ingest_rag"):
        await Retriever.abrir(ajustes, embeddings=EmbeddingsHash(), cliente=cliente)
