"""La herramienta de conocimiento devuelve fragmentos con su origen, con modelos falsos."""
import chromadb
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.rag.ingest import ingestar
from app.rag.retriever import Retriever
from app.schemas import FragmentoRecuperado, ResultadoConocimiento
from app.tools.knowledge import buscar_en_manuales
from tests.fakes import EmbeddingsHash


@pytest.fixture
async def retriever(env, tmp_path):
    env.setenv("CHROMA_PATH", str(tmp_path / "chroma"))
    s = Settings(_env_file=None)
    cliente, emb = chromadb.PersistentClient(path=s.chroma_path), EmbeddingsHash()
    await ingestar(settings=s, embeddings=emb, cliente=cliente)
    return await Retriever.abrir(s, embeddings=emb, cliente=cliente, modo="hibrido")


async def test_devuelve_fragmentos_con_id_seccion_y_puntaje(retriever):
    r = await buscar_en_manuales(retriever, "cupón de 30 % de descuento por atraso", k=3)
    assert isinstance(r, ResultadoConocimiento) and r.consulta.startswith("cupón") and 1 <= len(r.fragmentos) <= 3
    assert all(f.id and f.seccion and f.texto and f.puntaje > 0 for f in r.fragmentos)
    assert any("30 %" in f.texto for f in r.fragmentos)


async def test_por_defecto_busca_en_todos_los_manuales_y_el_filtro_es_opcional(retriever):
    sin = await buscar_en_manuales(retriever, "avisos al cliente", k=5, modo="bm25")
    con = await buscar_en_manuales(retriever, "avisos al cliente", k=5, modo="bm25", agente="redactor")
    assert {f.id.split("-")[0] for f in sin.fragmentos} >= {"LOG"}
    assert con.fragmentos and all(f.id in {x.id for x in sin.fragmentos} | {f.id for f in con.fragmentos} for f in con.fragmentos)


async def test_el_contrato_de_salida_rechaza_campos_inventados():
    with pytest.raises(ValidationError):
        FragmentoRecuperado(id="LOG-1", seccion="1", texto="x", puntaje=1.0, relevancia="alta")
