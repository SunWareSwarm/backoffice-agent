"""El arranque con un solo comando: prepara lo que falte, es idempotente, reintenta lo pasajero y la configuración de Docker es coherente."""
import sqlite3
import sys
from pathlib import Path

import chromadb
import pytest
from google.genai import errors as gerrors

import scripts.arrancar as arrancar
from app.config import Settings
from app.rag.chunking import ErrorDeFragmentacion
from scripts.arrancar import base_lista, preparar, rag_listo
from tests.fakes import EmbeddingsHash

RAIZ = Path(__file__).resolve().parent.parent
ERROR_503 = gerrors.ServerError(503, {"error": {"message": "high demand", "status": "UNAVAILABLE"}})


@pytest.fixture
def s(env, tmp_path):
    """Configuración con la base y Chroma en una carpeta temporal vacía: como un clone recién hecho."""
    env.setenv("SQLITE_PATH", str(tmp_path / "almacen" / "tienda.db"))
    env.setenv("CHROMA_PATH", str(tmp_path / "almacen" / "chroma"))
    return Settings(_env_file=None)


async def correr(s, emb=None, **kw):
    return await preparar(s, embeddings=emb or EmbeddingsHash(), log=lambda *_: None, **kw)


def centinela(ruta, poner=True):
    """Una marca en la base para saber si se volvió a crear (si desaparece, es otra base)."""
    con = sqlite3.connect(ruta)
    try:
        if poner:
            con.execute("INSERT INTO parametros VALUES ('centinela', '1')")
            con.commit()
        return con.execute("SELECT COUNT(*) FROM parametros WHERE clave = 'centinela'").fetchone()[0]
    finally:
        con.close()


# ---------------------------------------------------------------- desde cero
async def test_desde_cero_crea_la_base_y_el_rag(s):
    emb = EmbeddingsHash()
    assert not base_lista(s.sqlite_path) and not rag_listo(s, 61)
    assert await correr(s, emb) == {"base": "creada", "rag": "ingestado"}
    assert base_lista(s.sqlite_path) and rag_listo(s, 61)
    assert emb.llamadas_documentos  # se calcularon los embeddings
    con = sqlite3.connect(s.sqlite_path)
    assert con.execute("SELECT valor FROM parametros WHERE clave = 'fecha_referencia'").fetchone()[0] == "2026-11-03"
    assert con.execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 30


async def test_la_fecha_simulada_sale_de_la_configuracion(s, env):
    env.setenv("FECHA_SIMULADA", "2026-10-15")
    await correr(Settings(_env_file=None))
    con = sqlite3.connect(s.sqlite_path)
    assert con.execute("SELECT valor FROM parametros WHERE clave = 'fecha_referencia'").fetchone()[0] == "2026-10-15"


# ---------------------------------------------------------------- idempotente
async def test_un_segundo_arranque_no_rehace_nada(s):
    emb = EmbeddingsHash()
    await correr(s, emb)
    centinela(s.sqlite_path)
    llamadas = len(emb.llamadas_documentos)
    assert await correr(s, emb) == {"base": "ya existía", "rag": "ya existía"}
    assert centinela(s.sqlite_path, poner=False) == 1  # la base es la misma
    assert len(emb.llamadas_documentos) == llamadas  # y no se gastaron embeddings otra vez


async def test_reiniciar_borra_y_vuelve_a_crear_todo(s):
    emb = EmbeddingsHash()
    await correr(s, emb)
    centinela(s.sqlite_path)
    llamadas = len(emb.llamadas_documentos)
    assert await correr(s, emb, forzar=True) == {"base": "creada", "rag": "ingestado"}
    assert centinela(s.sqlite_path, poner=False) == 0 and len(emb.llamadas_documentos) > llamadas


async def test_si_solo_falta_el_rag_solo_se_hace_la_ingesta(s):
    await correr(s)
    centinela(s.sqlite_path)
    # Se borra la colección, no la carpeta: Chroma guarda en memoria una conexión por ruta (un contenedor nuevo es un proceso nuevo).
    chromadb.PersistentClient(path=s.chroma_path).delete_collection(s.rag_collection_name)
    assert not rag_listo(s, 61)
    assert await correr(s) == {"base": "ya existía", "rag": "ingestado"}
    assert centinela(s.sqlite_path, poner=False) == 1


async def test_un_arranque_interrumpido_se_repara_solo(s):
    """Una base a medias, o un RAG con fragmentos de menos, no cuentan como listos."""
    Path(s.sqlite_path).parent.mkdir(parents=True)
    Path(s.sqlite_path).write_bytes(b"esto no es una base de datos")  # archivo corrupto
    chromadb.PersistentClient(path=s.chroma_path).create_collection(s.rag_collection_name, embedding_function=None).add(
        ids=["x"], documents=["solo uno"], embeddings=[[0.1] * 4])  # colección incompleta
    assert not base_lista(s.sqlite_path) and not rag_listo(s, 61)
    assert await correr(s) == {"base": "creada", "rag": "ingestado"}
    assert base_lista(s.sqlite_path) and rag_listo(s, 61)


async def test_cambiar_el_modelo_de_embeddings_reindexa_sin_tocar_la_base(s, env):
    await correr(s)
    centinela(s.sqlite_path)
    env.setenv("EMBEDDING_MODEL", "otro-modelo-de-embeddings")  # otro modelo = otro nombre de colección
    assert await correr(Settings(_env_file=None)) == {"base": "ya existía", "rag": "ingestado"}
    assert centinela(s.sqlite_path, poner=False) == 1


# ---------------------------------------------------------------- errores
def falso_ingestar(fallos):
    """Una ingesta de mentira que falla con los errores indicados y luego funciona."""
    pendientes, llamadas = list(fallos), []

    async def ingestar(**kw):
        llamadas.append(1)
        if pendientes:
            raise pendientes.pop(0)
        return {"coleccion": "c", "fragmentos": 61, "dimension": 4, "modelo": "m"}

    return ingestar, llamadas


async def test_un_error_pasajero_en_la_ingesta_se_reintenta_con_espera(s, monkeypatch):
    falso, llamadas = falso_ingestar([ERROR_503, ERROR_503])
    monkeypatch.setattr(arrancar, "ingestar", falso)
    esperas = []

    async def dormir(seg):
        esperas.append(seg)

    informe = await preparar(s, dormir=dormir, log=lambda *_: None)
    assert informe["rag"] == "ingestado" and len(llamadas) == 3 and esperas == [5, 15]


async def test_si_el_error_pasajero_persiste_se_propaga_tras_los_reintentos(s, monkeypatch):
    falso, llamadas = falso_ingestar([ERROR_503] * 5)
    monkeypatch.setattr(arrancar, "ingestar", falso)

    async def dormir(seg):
        pass

    with pytest.raises(gerrors.ServerError):
        await preparar(s, dormir=dormir, log=lambda *_: None)
    assert len(llamadas) == 3


async def test_un_error_definitivo_falla_de_inmediato_sin_reintentar(s, monkeypatch):
    falso, llamadas = falso_ingestar([ValueError("clave inválida")])
    monkeypatch.setattr(arrancar, "ingestar", falso)
    with pytest.raises(ValueError):
        await preparar(s, log=lambda *_: None)
    assert len(llamadas) == 1


async def test_si_los_manuales_no_coinciden_con_el_catalogo_falla_con_claridad(s, monkeypatch, tmp_path):
    monkeypatch.setattr(arrancar, "DIR_DOCS", tmp_path / "sin_manuales")
    with pytest.raises(ErrorDeFragmentacion, match="Faltan manuales"):
        await preparar(s, embeddings=EmbeddingsHash(), log=lambda *_: None)


# ---------------------------------------------------------------- la línea de comandos
def test_sin_configuracion_el_arranque_dice_que_falta_y_que_hacer(monkeypatch, tmp_path):
    from app import config

    for var in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sys, "argv", ["arrancar"])
    config.get_settings.cache_clear()
    with pytest.raises(SystemExit) as salida:
        arrancar.main()
    mensaje = str(salida.value)
    assert "LLM_PROVIDER" in mensaje and "LLM_MODEL" in mensaje and ".env.example" in mensaje


def test_la_api_se_levanta_con_el_host_pedido_y_solo_preparar_no_la_levanta(env, monkeypatch):
    import uvicorn

    from app import config

    llamadas, preparadas = [], []

    async def preparar_falso(ajustes, forzar=False, **kw):
        preparadas.append(forzar)
        return {}

    monkeypatch.setattr(arrancar, "preparar", preparar_falso)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: llamadas.append((app, kw)))
    config.get_settings.cache_clear()

    monkeypatch.setattr(sys, "argv", ["arrancar", "--host", "0.0.0.0", "--port", "9000"])
    arrancar.main()
    assert llamadas == [("app.api.main:app", {"host": "0.0.0.0", "port": 9000})] and preparadas == [False]

    llamadas.clear()
    monkeypatch.setattr(sys, "argv", ["arrancar", "--solo-preparar", "--reiniciar"])
    arrancar.main()
    assert llamadas == [] and preparadas == [False, True]


# ---------------------------------------------------------------- Docker: no se puede ejecutar aquí, así que se vigila su coherencia
@pytest.fixture(scope="module")
def compose():
    yaml = pytest.importorskip("yaml")  # si falta PyYAML solo se omiten las pruebas de Docker (pip install pyyaml)
    return yaml.safe_load((RAIZ / "docker-compose.yml").read_text(encoding="utf-8"))


def test_compose_los_datos_viven_en_un_volumen_y_las_rutas_coinciden(compose):
    api = compose["services"]["api"]
    montaje = next(v for v in api["volumes"] if v.startswith("datos:"))
    destino = montaje.split(":")[1]
    assert destino == "/code/storage" and "datos" in compose["volumes"]
    assert api["environment"]["SQLITE_PATH"].startswith(destino + "/") and api["environment"]["CHROMA_PATH"].startswith(destino + "/")
    assert all("./data" not in v for v in api["volumes"])  # no se monta tu carpeta: sin permisos ni bloqueos en Windows


def test_compose_la_api_espera_a_redis_y_en_la_red_de_docker_redis_se_llama_redis(compose):
    api, redis = compose["services"]["api"], compose["services"]["redis"]
    assert api["depends_on"]["redis"]["condition"] == "service_healthy" and "healthcheck" in redis
    assert api["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert "redis-stack" in redis["image"]  # el checkpointer necesita RedisJSON y RediSearch
    assert api["env_file"] == ".env" and "8000:8000" in api["ports"]


def test_compose_la_api_tiene_healthcheck_con_un_periodo_de_gracia_para_la_primera_preparacion(compose):
    hc = compose["services"]["api"]["healthcheck"]
    assert "/health" in " ".join(hc["test"]) and int(str(hc["start_period"]).rstrip("s")) >= 120


def test_dockerfile_arranca_con_el_script_y_lleva_los_manuales_pero_no_los_secretos_ni_los_datos():
    docker = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert "scripts.arrancar" in docker and "0.0.0.0" in docker  # un solo comando, escuchando fuera del contenedor
    assert "COPY data" in docker and "COPY app" in docker and "COPY scripts" in docker
    assert ".env" not in docker.replace(".env.example", "")
    ignorados = (RAIZ / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".env", "data/chroma", "data/*.db"} <= set(ignorados)  # nunca viajan los secretos ni los datos generados
    assert (RAIZ / "data/docs_rag/manual_ordenes_compra.md").exists() and (RAIZ / "data/eval/rag_catalogo_fragmentos.csv").exists()


def test_el_ejemplo_del_env_trae_todas_las_variables_obligatorias_y_el_modo_hibrido():
    ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
    claves = {l.split("=")[0].strip() for l in ejemplo.splitlines() if "=" in l and not l.lstrip().startswith("#")}
    assert {"LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "GOOGLE_API_KEY", "REDIS_URL", "RAG_MODO"} <= claves
    assert next(l for l in ejemplo.splitlines() if l.startswith("RAG_MODO=")).split("#")[0].strip() == "RAG_MODO=hibrido"


def test_el_ejemplo_del_env_no_tiene_comentarios_al_final_de_una_linea_de_valor():
    """Regresión: el env_file de Docker Compose (sobre todo en versiones antiguas) deja el comentario como parte del valor."""
    for numero, linea in enumerate((RAIZ / ".env.example").read_text(encoding="utf-8").splitlines(), 1):
        if "=" in linea and not linea.lstrip().startswith("#"):
            valor = linea.split("=", 1)[1]
            assert "#" not in valor, f".env.example, línea {numero}: comentario pegado al valor: {linea!r}"


def test_el_ejemplo_del_env_se_lee_bien_como_esta_una_vez_completado(tmp_path, monkeypatch):
    """Se completan solo los valores obligatorios y todo lo demás se lee con los valores por defecto del ejemplo."""
    texto = (RAIZ / ".env.example").read_text(encoding="utf-8").replace("LLM_MODEL=\n", "LLM_MODEL=un-modelo\n").replace("GOOGLE_API_KEY=\n", "GOOGLE_API_KEY=k\n")
    (tmp_path / ".env").write_text(texto, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    for var in ("LLM_PROVIDER", "LLM_MODEL", "EMBEDDING_PROVIDER", "EMBEDDING_MODEL", "GOOGLE_API_KEY", "RAG_MODO", "LLM_TIMEOUT_SECONDS"):
        monkeypatch.delenv(var, raising=False)
    ajustes = Settings(_env_file=tmp_path / ".env")
    assert (ajustes.llm_provider, ajustes.llm_model, ajustes.rag_modo) == ("google", "un-modelo", "hibrido")
    assert ajustes.llm_timeout_seconds == 60 and ajustes.llm_retry_base_seconds == 2.0 and ajustes.llm_fallback_model is None
