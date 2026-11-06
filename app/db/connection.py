"""Conexión a la base simulada.

Las herramientas de consulta usan SIEMPRE la conexión de solo lectura: aunque un LLM llegara a
generar un INSERT o un DELETE, la base lo rechaza. Solo las acciones aprobadas escriben, y lo hacen
por la conexión de escritura (tools/actions.py, fase 4.4).
"""
import sqlite3
from pathlib import Path


def conectar(ruta: str | Path, *, solo_lectura: bool = True) -> sqlite3.Connection:
    ruta = Path(ruta).resolve()
    if solo_lectura:
        con = sqlite3.connect(f"{ruta.as_uri()}?mode=ro", uri=True)
        con.execute("PRAGMA query_only = ON")
    else:
        con = sqlite3.connect(ruta)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con
