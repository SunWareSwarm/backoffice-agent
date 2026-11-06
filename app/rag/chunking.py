"""Corte por estructura (títulos) + regla propia para preguntas frecuentes + metadatos (fase 4.3).

Reglas de corte (decididas):
  - Un fragmento por sección o subsección. El título viaja dentro del texto del fragmento.
  - Las tablas quedan enteras, junto a su título.
  - Cada ejemplo de aviso es un fragmento propio.
  - Cada pregunta frecuente, con su respuesta, es un fragmento propio.

Identificadores: PREFIJO-N (sección), PREFIJO-N.i (subsección i), PREFIJO-N.0 (introducción de una
sección que tiene subsecciones), PREFIJO-N.qK (pregunta frecuente K) y PREFIJO-0 (título e introducción).

El catálogo (data/eval/rag_catalogo_fragmentos.csv) es la fuente de verdad de los ids y de los
metadatos que no se pueden deducir del texto (agente y causa). La fragmentación FALLA si un manual
y el catálogo no coinciden: así no se ingesta nada a medias cuando alguien edita un título.
"""
import csv
import re
from dataclasses import dataclass
from pathlib import Path

# archivo -> (prefijo de los ids, valor del metadato "documento")
DOCUMENTOS = {
    "manual_operaciones_logisticas.md": ("LOG", "manual_logistico"),
    "manual_ordenes_compra.md": ("OC", "manual_ordenes_compra"),
    "guia_marca_politica_precios.md": ("MAR", "guia_marca_precios"),
}

_ENCABEZADO = re.compile(r"^(#{1,3})\s+(.*\S)\s*$")
_NUMERADO = re.compile(r"^(\d+)\.\s+(.*)$")
_PREGUNTA = re.compile(r"\*\*(¿.+?\?)\*\*\s*(.+?)(?=\s*\*\*¿|\Z)", re.S)
_SEPARADOR_TABLA = re.compile(r"^\|[\s:|-]+\|\s*$")


class ErrorDeFragmentacion(ValueError):
    """Un manual y el catálogo no coinciden, o un manual no tiene la estructura esperada."""


@dataclass(frozen=True)
class Fragmento:
    id: str
    texto: str  # lo que se convierte en vector
    metadata: dict  # documento, seccion, tipo_contenido, agente, causa (todos de tipo simple)


@dataclass
class _Trozo:
    id: str
    encabezado: str
    tipo: str
    ruta: str  # "Documento > Sección > Subsección"
    cuerpo: str


def cargar_catalogo(ruta: str | Path) -> dict[str, dict]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    ids = [fila["chunk_id"] for fila in filas]
    if len(ids) != len(set(ids)):
        raise ErrorDeFragmentacion("El catálogo tiene ids repetidos")
    return {fila["chunk_id"]: fila for fila in filas}


def _tiene_tabla(cuerpo: str) -> bool:
    return any(_SEPARADOR_TABLA.match(linea.strip()) for linea in cuerpo.splitlines())


def _tipo(cuerpo: str, es_ejemplo: bool) -> str:
    if es_ejemplo:
        return "ejemplo"
    return "tabla" if _tiene_tabla(cuerpo) else "regla"


def _limpiar(cuerpo: str) -> str:
    """Quita el formato que solo estorba al vectorizar: citas (>) y negritas."""
    lineas = [re.sub(r"^>\s?", "", linea) for linea in cuerpo.strip().splitlines()]
    return re.sub(r"\*\*(.+?)\*\*", r"\1", "\n".join(lineas))


def _trocear(markdown: str, prefijo: str) -> list[_Trozo]:
    """Parte un manual en trozos según sus títulos. No conoce el catálogo."""
    titulo_doc, intro, secciones = None, [], []
    h2 = h3 = None
    destino = intro
    for linea in markdown.splitlines():
        m = _ENCABEZADO.match(linea)
        if m:
            nivel, texto = len(m.group(1)), m.group(2)
            if nivel == 1:
                titulo_doc, destino = texto, intro
            elif nivel == 2:
                num = _NUMERADO.match(texto)
                if not num:
                    raise ErrorDeFragmentacion(f"El título de sección no está numerado: {texto!r}")
                h2 = {"num": int(num.group(1)), "titulo": texto, "intro": [], "h3": []}
                secciones.append(h2)
                destino, h3 = h2["intro"], None
            else:
                if h2 is None:
                    raise ErrorDeFragmentacion(f"Subsección sin sección: {texto!r}")
                h3 = {"titulo": texto, "lineas": []}
                h2["h3"].append(h3)
                destino = h3["lineas"]
            continue
        destino.append(linea)
    if not titulo_doc:
        raise ErrorDeFragmentacion("El documento no tiene título (#)")

    trozos = [_Trozo(f"{prefijo}-0", "Título e introducción", "regla", titulo_doc, "\n".join(intro))]
    for sec in secciones:
        n, titulo, cuerpo = sec["num"], sec["titulo"], "\n".join(sec["intro"]).strip()
        ruta = f"{titulo_doc} > {titulo}"
        if re.sub(r"^\d+\.\s+", "", titulo) == "Preguntas frecuentes":
            preguntas = _PREGUNTA.findall(cuerpo)
            if not preguntas:
                raise ErrorDeFragmentacion(f"La sección {titulo!r} no tiene preguntas con el formato **¿...?** respuesta")
            for k, (pregunta, respuesta) in enumerate(preguntas, 1):
                trozos.append(_Trozo(f"{prefijo}-{n}.q{k}", pregunta, "pregunta_frecuente", ruta, f"{pregunta} {respuesta.strip()}"))
        elif sec["h3"]:
            if cuerpo:
                trozos.append(_Trozo(f"{prefijo}-{n}.0", titulo, _tipo(cuerpo, False), ruta, cuerpo))
            for i, sub in enumerate(sec["h3"], 1):
                texto = "\n".join(sub["lineas"]).strip()
                trozos.append(_Trozo(f"{prefijo}-{n}.{i}", sub["titulo"], _tipo(texto, sub["titulo"].startswith("Ejemplo")),
                                     f"{ruta} > {sub['titulo']}", texto))
        else:
            trozos.append(_Trozo(f"{prefijo}-{n}", titulo, _tipo(cuerpo, False), ruta, cuerpo))
    return trozos


def fragmentar_documento(markdown: str, prefijo: str, documento: str, catalogo: dict[str, dict]) -> list[Fragmento]:
    """Corta un manual y lo valida contra el catálogo. Lanza ErrorDeFragmentacion si no coinciden."""
    trozos = _trocear(markdown, prefijo)
    producidos = {t.id for t in trozos}
    esperados = {i for i in catalogo if i.startswith(prefijo + "-")}
    if producidos - esperados:
        raise ErrorDeFragmentacion(f"Fragmentos del manual que no están en el catálogo: {sorted(producidos - esperados)}")
    if esperados - producidos:
        raise ErrorDeFragmentacion(f"Fragmentos del catálogo que faltan en el manual: {sorted(esperados - producidos)}")

    fragmentos = []
    for t in trozos:
        fila = catalogo[t.id]
        if fila["documento"] != documento:
            raise ErrorDeFragmentacion(f"{t.id}: el catálogo dice documento {fila['documento']!r}, se esperaba {documento!r}")
        if fila["tipo_contenido"] != t.tipo and not (t.tipo == "regla" and t.id.endswith("-0")):
            raise ErrorDeFragmentacion(f"{t.id}: el texto es de tipo {t.tipo!r} pero el catálogo dice {fila['tipo_contenido']!r}")
        es_introduccion = t.id.endswith((".0", "-0"))
        if not es_introduccion and fila["seccion"] != t.encabezado:
            raise ErrorDeFragmentacion(f"{t.id}: el título del manual es {t.encabezado!r} pero el catálogo dice {fila['seccion']!r}")
        cuerpo = _limpiar(t.cuerpo)
        if not cuerpo:
            raise ErrorDeFragmentacion(f"{t.id}: el fragmento está vacío")
        fragmentos.append(Fragmento(
            id=t.id,
            texto=f"{t.ruta}\n\n{cuerpo}",
            metadata={"documento": fila["documento"], "seccion": fila["seccion"], "tipo_contenido": fila["tipo_contenido"],
                      "agente": fila["agente"], "causa": fila["causa"]},
        ))
    return fragmentos


def fragmentar_corpus(dir_docs: str | Path, catalogo: dict[str, dict]) -> list[Fragmento]:
    """Corta los tres manuales. Falla si falta alguno o si algo no coincide con el catálogo."""
    dir_docs = Path(dir_docs)
    faltan = [nombre for nombre in DOCUMENTOS if not (dir_docs / nombre).exists()]
    if faltan:
        raise ErrorDeFragmentacion(f"Faltan manuales en {dir_docs}: {faltan}")
    fragmentos: list[Fragmento] = []
    for nombre, (prefijo, documento) in DOCUMENTOS.items():
        texto = (dir_docs / nombre).read_text(encoding="utf-8")
        fragmentos.extend(fragmentar_documento(texto, prefijo, documento, catalogo))
    return fragmentos
