"""Crea (o recrea) la base de datos simulada.

    python -m scripts.seed_db                      # data/tienda.db, con la fecha de hoy
    python -m scripts.seed_db --hoy 2026-11-03     # reloj simulado fijo (resultados reproducibles)
    python -m scripts.seed_db --semilla 7 --salida /tmp/otra.db
"""
import argparse
from datetime import date
from pathlib import Path

from app.db.seed import generar


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--salida", default="data/tienda.db", type=Path)
    ap.add_argument("--hoy", type=date.fromisoformat, default=None, help="AAAA-MM-DD (por defecto, la fecha real)")
    ap.add_argument("--semilla", type=int, default=42)
    args = ap.parse_args()
    resumen = generar(args.salida, args.hoy, args.semilla)
    print(f"Base creada en {args.salida}")
    for clave, valor in resumen.items():
        print(f"  {clave}: {valor}")


if __name__ == "__main__":
    main()
