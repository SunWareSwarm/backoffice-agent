import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.llm import get_chat_model, get_embeddings


def nuevo(**kw):
    return Settings(_env_file=None, **kw)


def test_sin_modelo_la_aplicacion_no_arranca(env):
    env.delenv("LLM_MODEL")
    with pytest.raises(ValidationError):
        nuevo()


def test_falta_la_clave_del_proveedor_elegido(env):
    env.delenv("GOOGLE_API_KEY")
    with pytest.raises(ValidationError, match="clave"):
        nuevo()


def test_el_nombre_de_la_coleccion_incluye_proveedor_modelo_y_dimensiones(env):
    assert nuevo().rag_collection_name == "rag_google_embedding-de-prueba"
    env.setenv("EMBEDDING_DIMENSIONS", "768")
    assert nuevo().rag_collection_name == "rag_google_embedding-de-prueba_768d"


def test_cambiar_el_modelo_de_embeddings_cambia_la_coleccion(env):
    a = nuevo().rag_collection_name
    env.setenv("EMBEDDING_MODEL", "otro/modelo:v2")
    b = nuevo().rag_collection_name
    assert a != b and re.fullmatch(r"[a-zA-Z0-9._-]+", b)  # nombre válido para Chroma


def test_el_cliente_se_elige_por_configuracion_y_es_google(env):
    s = nuevo()
    assert type(get_chat_model(s)).__name__ == "ChatGoogleGenerativeAI"
    assert type(get_embeddings(s)).__name__ == "GoogleGenerativeAIEmbeddings"


def test_los_clientes_exponen_metodos_async(env):
    s = nuevo()
    assert hasattr(get_chat_model(s), "ainvoke")
    assert {"aembed_documents", "aembed_query"} <= set(dir(get_embeddings(s)))


def test_anthropic_no_sirve_para_embeddings(env):
    env.setenv("EMBEDDING_PROVIDER", "anthropic")
    with pytest.raises(ValidationError):
        nuevo()


def test_ningun_nombre_de_modelo_esta_escrito_en_el_codigo():
    """Regla de la rúbrica: los nombres de modelo van en variables de entorno, no en el código."""
    patron = re.compile(r"(gemini-|gpt-|claude-|text-embedding|voyage-)", re.I)
    sospechosos = []
    for archivo in Path("app").rglob("*.py"):
        for n, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if patron.search(linea) and not linea.strip().startswith(("#", '"""')):
                sospechosos.append(f"{archivo}:{n}: {linea.strip()}")
    assert not sospechosos, "\n".join(sospechosos)
