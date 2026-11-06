"""Prueba de carga: N peticiones CONCURRENTES contra la API, esperando a que todas terminen.

Uso (con Redis, la base simulada y la API levantados):
    python -m scripts.load_test                       # 5 peticiones contra http://localhost:8000
    python -m scripts.load_test --url http://localhost:8000 --n 5

Las consultas son de VENTAS: las atiende solo BI y no proponen acciones, así que no disparan la pausa de aprobación.
Una consulta que pausa dejaría el trabajo esperando y distorsionaría la medición de costo y latencia. Si algún
trabajo termina pausado, el script lo muestra como PAUSADO y avisa.

Al terminar imprime cuánto tardó cada trabajo y la hora de inicio y fin: úsala para filtrar la ventana de tiempo en el
dashboard de LangSmith y para `python -m scripts.latency_report`.
"""
import argparse
import asyncio
import time
from datetime import datetime

import httpx

QUERIES = [
    "¿Cuántas ventas tuvimos la última semana y cuánto fue el ingreso?",
    "¿Qué productos tuvieron mejores ventas por unidad de stock en las últimas 12 semanas?",
    "¿Qué carteras tienen mejor margen y cómo se han vendido?",
    "¿Qué categorías de carteras venden más y cuál es su tendencia de ventas?",
    "¿Qué productos deberíamos incluir en el próximo drop según las ventas?",
]
TERMINAL = {"DONE", "FAILED"}
PAUSADO = {"WAITING_APPROVAL", "WAITING_CLARIFICATION"}  # esperan a un humano: aquí no es lo esperado


async def run_one(client: httpx.AsyncClient, query: str, poll: float, timeout: float) -> dict:
    """Envía UNA consulta y consulta su estado hasta que termine (patrón ticket y consulta)."""
    t0 = time.perf_counter()
    r = await client.post("/tasks", json={"query": query})
    r.raise_for_status()
    accepted_in = time.perf_counter() - t0  # lo que tardó la API en devolver el job_id
    job_id = r.json()["job_id"]

    while True:
        job = (await client.get(f"/tasks/{job_id}")).json()
        estado = job["status"]
        if estado in TERMINAL or estado in PAUSADO:
            return {"job_id": job_id, "status": "PAUSADO" if estado in PAUSADO else estado, "detalle": estado, "query": query,
                    "accepted_s": accepted_in, "total_s": time.perf_counter() - t0, "error": job.get("error"),
                    "origen": job.get("answer_origin")}
        if time.perf_counter() - t0 > timeout:
            return {"job_id": job_id, "status": "TIMEOUT_CLIENTE", "detalle": estado, "query": query, "accepted_s": accepted_in,
                    "total_s": time.perf_counter() - t0, "error": None, "origen": None}
        await asyncio.sleep(poll)


async def run_load_test(client: httpx.AsyncClient, queries: list[str], poll: float = 2.0, timeout: float = 600.0):
    # gather lanza todas a la vez: eso es lo que las hace "concurrentes".
    return await asyncio.gather(*(run_one(client, q, poll, timeout) for q in queries))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--n", type=int, default=5, help="cantidad de peticiones concurrentes")
    args = parser.parse_args()
    queries = [QUERIES[i % len(QUERIES)] for i in range(args.n)]

    async with httpx.AsyncClient(base_url=args.url, timeout=30) as client:
        start = datetime.now().astimezone()
        t0 = time.perf_counter()
        print(f"Lanzando {args.n} peticiones concurrentes a {args.url} ({start:%H:%M:%S})...")
        results = await run_load_test(client, queries)
        wall = time.perf_counter() - t0
        end = datetime.now().astimezone()

    print(f"\n{'job_id':<10}{'estado':<16}{'ticket (s)':>11}{'total (s)':>11}  {'respuesta':<10}consulta")
    for r in results:
        print(f"{r['job_id'][:8]:<10}{r['status']:<16}{r['accepted_s']:>11.3f}{r['total_s']:>11.1f}  {str(r['origen'] or '-'):<10}{r['query'][:46]}")
        if r["error"]:
            print(f"{'':<10}ERROR: {r['error']}")

    totals = sorted(r["total_s"] for r in results)
    ok = sum(r["status"] == "DONE" for r in results)
    print(f"\nTerminaron en DONE: {ok}/{len(results)} | tiempo total de la corrida: {wall:.1f} s")
    print(f"Más lento: {totals[-1]:.1f} s | más rápido: {totals[0]:.1f} s")
    print(f"Ventana de la corrida: {start:%H:%M:%S} -> {end:%H:%M:%S} (hora local; filtra el dashboard con ella)")
    if any(r["status"] == "PAUSADO" for r in results):
        print("AVISO: hubo trabajos PAUSADOS (esperan aclaración o aprobación). Esas consultas no sirven para medir: ajusta QUERIES.")
    if ok < len(results):
        print("AVISO: hubo trabajos que no terminaron en DONE; revisa el error antes de capturar el dashboard.")


if __name__ == "__main__":
    asyncio.run(main())
