"""Resumen de costo y latencia de las ejecuciones registradas en LangSmith.

Uso (desde la raíz del repo, con el .env cargado):
    python -m scripts.latency_report --minutes 30

Lee las trazas RAÍZ del proyecto (una por consulta) y calcula latencia p50/p95 y costo por ejecución. El costo
incluye las llamadas del cliente de texto (el del Módulo 1), que se registran como hijas de la traza raíz.
Sirve para contrastar lo que ves en el dashboard o como respaldo si este no muestra p95.
"""
import argparse
import math
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from app.observability import DEFAULT_PROJECT, RUN_NAME


def percentile(values: list[float], q: float) -> float:
    """Percentil por 'rango más cercano': ordena y toma el valor en la posición ceil(q * n).

    Con 5 datos, p95 cae en la posición 5, es decir, el máximo. Otros métodos interpolan y pueden
    dar un número algo distinto: por eso el dashboard y este script pueden diferir levemente.
    """
    if not values:
        raise ValueError("No hay valores para calcular el percentil")
    ordered = sorted(values)
    rank = math.ceil(q * len(ordered))
    return ordered[max(rank, 1) - 1]


def summarize(runs) -> dict:
    """Recibe trazas (con start_time, end_time, total_cost, total_tokens, status) y las resume."""
    rows = []
    for r in runs:
        if r.end_time is None:  # aún en ejecución: no tiene latencia final
            continue
        rows.append(
            {
                "id": str(r.id)[:8],
                "status": r.status,
                "latency_s": (r.end_time - r.start_time).total_seconds(),
                "tokens": r.total_tokens,
                "cost": float(r.total_cost) if r.total_cost is not None else None,
            }
        )
    latencies = [row["latency_s"] for row in rows]
    costs = [row["cost"] for row in rows if row["cost"] is not None]
    return {
        "rows": rows,
        "n": len(rows),
        "p50_s": percentile(latencies, 0.50) if latencies else None,
        "p95_s": percentile(latencies, 0.95) if latencies else None,
        "avg_cost": sum(costs) / len(costs) if costs else None,
        "total_cost": sum(costs) if costs else None,
        "runs_without_cost": len(rows) - len(costs),
    }


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--minutes", type=int, default=30, help="ventana de tiempo hacia atrás")
    parser.add_argument("--project", default=os.getenv("LANGSMITH_PROJECT", DEFAULT_PROJECT))
    args = parser.parse_args()

    from langsmith import Client  # import local: el resto del módulo se puede probar sin red

    since = datetime.now(timezone.utc) - timedelta(minutes=args.minutes)
    runs = [
        r
        for r in Client().list_runs(project_name=args.project, is_root=True, start_time=since)
        if r.name == RUN_NAME
    ]
    result = summarize(runs)

    print(f"Proyecto: {args.project} | ejecuciones '{RUN_NAME}' en los últimos {args.minutes} min: {result['n']}\n")
    print(f"{'traza':<10}{'estado':<10}{'latencia (s)':>14}{'tokens':>10}{'costo (USD)':>14}")
    for row in result["rows"]:
        cost = f"{row['cost']:.6f}" if row["cost"] is not None else "n/d"
        print(f"{row['id']:<10}{row['status']:<10}{row['latency_s']:>14.2f}{str(row['tokens']):>10}{cost:>14}")
    if result["n"]:
        print(f"\nLatencia p50: {result['p50_s']:.2f} s | p95: {result['p95_s']:.2f} s")
        if result["avg_cost"] is not None:
            print(f"Costo promedio por ejecución: {result['avg_cost']:.6f} USD | total: {result['total_cost']:.6f} USD")
        if result["runs_without_cost"]:
            print(
                f"AVISO: {result['runs_without_cost']} ejecución(es) sin costo. Probablemente LangSmith no "
                "tiene el precio de tu modelo: agrégalo en su tabla de precios y repite la prueba."
            )


if __name__ == "__main__":
    main()
