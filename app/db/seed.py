"""Generador de la base SQLite simulada (fase 4.2).

Dos ideas de diseño:

1. RELOJ SIMULADO. La fecha "hoy" se guarda como dato (parametros.fecha_referencia) y todo se
   calcula respecto a ella. Así los resultados son reproducibles: las herramientas leen "hoy" de la
   base y no de date.today().

2. CASOS SEMBRADOS. Además de un flujo normal de pedidos (simulado con cola y capacidad), se
   inyectan casos conocidos de antemano: un quiebre próximo, un stock estancado, un falso ganador,
   atrasos con cada causa, un pico de demanda, etc. Son lo que el agente debe descubrir y lo que las
   pruebas verifican. Los productos BAG-001 a BAG-008 son los de los casos; BAG-009 a BAG-030 son
   productos normales que no deben generar ninguna alerta.

Los datos son ficticios.
"""
import math
import random
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

ESQUEMA = Path(__file__).with_name("schema.sql")

HISTORIAL_DIAS = 84  # 12 semanas
CAPACIDAD_DIARIA = 10
HORA_CORTE = 15
PLAZO_ENTREGA = {"RM": 2, "regiones": 5}  # máximo, en días, desde el despacho
DIAS_COURIER = {"RM": (1, 1, 2), "regiones": (3, 4, 4, 5)}  # lo habitual: nunca supera el plazo
LEAD_TIME_PROVEEDOR = 10
FACTOR_SEMANA = (1.0, 1.0, 1.0, 1.1, 1.3, 1.4, 1.1)  # lunes a domingo
# Días antes de "hoy" con demanda extra y cuántos pedidos extra. Un pico grande y pasado (deja atrasos
# en el historial) y un repunte pequeño y reciente (deja un atraso actual moderado).
PICOS = {16: 16, 15: 16, 14: 16, 6: 8, 5: 8}

PARAMETROS = {
    "lead_time_proveedor_dias": str(LEAD_TIME_PROVEEDOR),
    "hora_corte": f"{HORA_CORTE}:00",
    "capacidad_diaria": str(CAPACIDAD_DIARIA),
    "plazo_entrega_rm_dias": "2",
    "plazo_entrega_regiones_dias": "5",
    "compensacion_desde_dias": "2",
    "cupon_porcentaje": "30",
    "espera_stock_dias": "3",
    "max_mensajes_datos": "3",
    "stock_estancado_dias": "14",
    "minimo_ventas_ganador": "15",
    "historial_minimo_semanas": "4",
    "cobertura_objetivo_dias": "14",  # propuesta: días de venta que debe cubrir una orden, además del lead time
}

PROVEEDORES = [(1, "Textiles Andes"), (2, "Cuero Sur"), (3, "Moda Express"), (4, "Bolsos del Pacífico")]

# sku, nombre, categoria, tamano, color, material, estilo, precio, costo, proveedor, stock, stock_seguridad
PRODUCTOS_CASO = [
    ("BAG-001", "Baguette Cherry", "baguette", "mini", "rojo", "charol", "Y2K", 24990, 11000, 1, 18, 6),
    ("BAG-002", "Tote Denim Wash", "tote", "grande", "azul", "denim", "retro", 32990, 16500, 2, 40, 8),
    ("BAG-003", "Bandolera Pixel", "bandolera", "mediano", "negro", "sintético", "Y2K", 19990, 15500, 3, 140, 10),
    ("BAG-004", "Mini Shoulder Lila", "shoulder", "mini", "lila", "satén", "Y2K", 27990, 11000, 1, 0, 4),
    ("BAG-005", "Mochila Retro Stripes", "mochila", "mediano", "multicolor", "sintético", "retro", 34990, 17500, 4, 0, 6),
    ("BAG-006", "Clutch Glam Plata", "clutch", "mini", "plata", "charol", "glam", 22990, 9000, 2, 0, 3),
    ("BAG-007", "Baguette Mora", "baguette", "mini", "morado", "charol", "Y2K", 25990, 11500, 3, 3, 3),
    ("BAG-008", "Tote Minimal Beige", "tote", "grande", "beige", "sintético", "minimal", 36990, 17000, 4, 55, 8),
]
TIPOS = {"baguette": ("mini", "mediano"), "bandolera": ("mediano",), "tote": ("grande", "mediano"),
         "mochila": ("mediano", "grande"), "clutch": ("mini",), "shoulder": ("mini", "mediano")}
COLORES = ["rojo", "azul", "negro", "rosado", "verde", "amarillo", "blanco", "celeste", "plata", "burdeo", "lila", "beige"]
MATERIALES = ["charol", "denim", "satén", "sintético", "vegan leather"]
ESTILOS = ["Y2K", "retro", "minimal", "glam"]
ADJETIVOS = ["Cloud", "Neon", "Candy", "Disco", "Moon", "Velvet", "Aqua", "Sunset", "Berry", "Mint", "Coral",
             "Glitter", "Pop", "Sky", "Rose", "Lemon", "Ocean", "Twist", "Spark", "Dream", "Fizz", "Charm"]
SKUS_CASO = {f[0] for f in PRODUCTOS_CASO}


@dataclass
class Pedido:
    pedido_id: str
    ingreso: datetime
    region: str
    envio_pagado: int
    lineas: list[tuple[str, int]]  # (sku, cantidad)
    cancelado: bool = False
    manual: bool = False  # los casos a mano traen su propia historia
    eventos: list[tuple[str, datetime]] = field(default_factory=list)
    fecha_prometida: date | None = None
    causa: str | None = None
    avisos: list[tuple[str, datetime]] = field(default_factory=list)  # (tipo, cuándo)
    respuestas: list[tuple[str, datetime]] = field(default_factory=list)
    reembolso: tuple[str, str, datetime] | None = None  # (motivo, estado, cuándo)
    # solo para la simulación
    elegible: date | None = None
    inicio_prep: datetime | None = None
    entrega_real: datetime | None = None

    @property
    def estado(self) -> str:
        return self.eventos[-1][0]


# ---------------------------------------------------------------- utilidades
def _poisson(rng: random.Random, lam: float) -> int:
    limite, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limite:
            return k
        k += 1


def _elegible(ingreso: datetime) -> date:
    """Un pedido ingresado antes del corte entra a preparación ese día; después, al siguiente."""
    return ingreso.date() + timedelta(days=0 if ingreso.hour < HORA_CORTE else 1)


def _repartir(desde: int, hasta: int, n: int) -> list[int]:
    """n días-antes-de-hoy repartidos parejo entre `desde` y `hasta` (ambos incluidos)."""
    if n == 1:
        return [desde]
    return [desde - round(i * (desde - hasta) / (n - 1)) for i in range(n)]


# ---------------------------------------------------------------- catálogo e inventario
def _catalogo(rng: random.Random) -> tuple[list[dict], dict[str, float]]:
    productos, tasas = [], {}
    for sku, nombre, cat, tam, color, mat, est, precio, costo, prov, stock, seg in PRODUCTOS_CASO:
        productos.append(dict(sku=sku, nombre=nombre, categoria=cat, tamano=tam, color=color, material=mat, estilo=est,
                              precio=precio, costo=costo, proveedor_id=prov, stock=stock, seguridad=seg))
    tasas["BAG-008"] = 0.12  # producto sano que aparece en varios pedidos
    tipos = list(TIPOS)
    for i in range(22):
        tipo = tipos[i % len(tipos)]
        precio = rng.choice(range(15990, 39991, 1000))
        sku = f"BAG-{9 + i:03d}"
        productos.append(dict(
            sku=sku, nombre=f"{tipo.capitalize()} {ADJETIVOS[i]}", categoria=tipo, tamano=rng.choice(TIPOS[tipo]),
            color=rng.choice(COLORES), material=rng.choice(MATERIALES), estilo=rng.choice(ESTILOS), precio=precio,
            costo=round(precio * rng.uniform(0.38, 0.58), -2), proveedor_id=rng.randint(1, 4),
            stock=None, seguridad=rng.randint(3, 8)))
        tasas[sku] = rng.uniform(0.04, 0.15)  # ventas por día: la tienda trabaja con holgura, no al límite
    return productos, tasas


# ---------------------------------------------------------------- ventas
def _ventas(rng: random.Random, hoy: date, tasas: dict[str, float]) -> list[tuple[int, str, int, bool]]:
    """Devuelve (días antes de hoy, sku, cantidad, es_caso_sembrado)."""
    ev: list[tuple[int, str, int, bool]] = []
    # flujo normal: un proceso de Poisson por producto y día, con más ventas el fin de semana
    for sku in sorted(tasas):
        for dias_antes in range(HISTORIAL_DIAS, 0, -1):
            factor = FACTOR_SEMANA[(hoy - timedelta(days=dias_antes)).weekday()]
            for _ in range(_poisson(rng, tasas[sku] * factor)):
                ev.append((dias_antes, sku, 2 if rng.random() < 0.1 else 1, False))
    # todo producto sano vende al menos una vez en la última semana (si no, parecería estancado)
    for sku in sorted(tasas):
        if not any(s == sku and 1 <= d <= 7 for d, s, _, _ in ev):
            ev.append((rng.randint(1, 6), sku, 1, False))

    # casos sembrados, con calendarios exactos
    def caso(sku, offsets):
        ev.extend((d, sku, 1, True) for d in offsets)

    caso("BAG-001", _repartir(84, 15, 70))  # ritmo bajo...
    caso("BAG-001", [d for d in range(14, 0, -1) for _ in range(3)])  # ...y 3 por día las últimas 2 semanas
    caso("BAG-002", _repartir(84, 21, 64))  # la última venta fue hace 21 días
    caso("BAG-003", _repartir(84, 1, 60))  # mucho volumen, con mucho stock
    caso("BAG-004", _repartir(19, 1, 12))  # 12 ventas, todas recientes
    caso("BAG-005", _repartir(60, 12, 25))
    caso("BAG-006", _repartir(60, 12, 20))
    caso("BAG-007", _repartir(70, 15, 30))
    return ev


def _pedidos_desde_ventas(rng: random.Random, hoy: date, ventas, tasas) -> list[Pedido]:
    """Convierte las ventas en pedidos. Algunos pedidos normales llevan dos productos."""
    pedidos: list[Pedido] = []
    por_dia: dict[int, list] = {}
    for d, sku, qty, caso in sorted(ventas):
        por_dia.setdefault(d, []).append((sku, qty, caso))

    def nuevo(d, lineas, cancelable):
        ingreso = datetime.combine(hoy - timedelta(days=d), time(rng.randint(8, 21), rng.randint(0, 59)))
        region = "RM" if rng.random() < 0.6 else "regiones"
        p = Pedido("", ingreso, region, 1 if rng.random() < 0.7 else 0, lineas)
        p.cancelado = cancelable and rng.random() < 0.02
        pedidos.append(p)
        return p

    for d in sorted(por_dia, reverse=True):
        previo = None
        for sku, qty, caso in por_dia[d]:
            if previo is not None and not caso and rng.random() < 0.15 and all(s != sku for s, _ in previo.lineas):
                previo.lineas.append((sku, qty))
                continue
            previo = nuevo(d, [(sku, qty)], cancelable=not caso and sku not in SKUS_CASO)
            if caso:
                previo = None
    # pico de demanda: días con muchos más pedidos que la capacidad de preparación
    normales = sorted(s for s in tasas if s not in SKUS_CASO)
    for d, extra in PICOS.items():
        for _ in range(extra):
            nuevo(d, [(rng.choice(normales), 1)], cancelable=True)
    return pedidos


# ---------------------------------------------------------------- simulación del flujo
def _simular_flujo(pedidos: list[Pedido], hoy: date, ahora: datetime, rng: random.Random) -> None:
    """Cola de preparación con capacidad diaria, despacho al día siguiente y entrega por courier."""
    pedidos.sort(key=lambda p: p.ingreso)
    for n, p in enumerate(pedidos, 1):
        p.pedido_id = f"PED-{n:04d}"
        p.elegible = _elegible(p.ingreso)
        p.fecha_prometida = p.elegible + timedelta(days=2 + PLAZO_ENTREGA[p.region])
    fluyen = sorted((p for p in pedidos if not p.cancelado), key=lambda p: (p.elegible, p.ingreso))
    cola, i, dia = [], 0, fluyen[0].elegible
    while dia <= hoy:
        while i < len(fluyen) and fluyen[i].elegible <= dia:
            cola.append(fluyen[i])
            i += 1
        for n in range(min(CAPACIDAD_DIARIA, len(cola))):
            cola.pop(0).inicio_prep = datetime.combine(dia, time(9, 0)) + timedelta(minutes=5 * n)
        dia += timedelta(days=1)

    for p in pedidos:
        ev = [("solicitado", p.ingreso), ("ingresado", p.ingreso + timedelta(minutes=10))]
        if p.cancelado:
            ev.append(("cancelado", p.ingreso + timedelta(days=1)))
        elif p.inicio_prep:
            despacho = datetime.combine(p.inicio_prep.date() + timedelta(days=1), time(10, 0))
            entrega = datetime.combine(despacho.date() + timedelta(days=rng.choice(DIAS_COURIER[p.region])), time(15, 0))
            ev += [("en_preparacion", p.inicio_prep), ("espera_despacho", p.inicio_prep + timedelta(hours=8)),
                   ("en_ruta", despacho), ("proximo_a_llegar", datetime.combine(entrega.date(), time(8, 0))),
                   ("entregado", entrega)]
            p.entrega_real = entrega
        p.eventos = [e for e in ev if e[1] <= ahora]
        if p.cancelado:
            continue
        # causa: un pedido que esperó 2 días o más en la cola y llegó (o va a llegar) tarde
        entregado = p.estado == "entregado"
        tarde = p.entrega_real.date() > p.fecha_prometida if entregado else hoy > p.fecha_prometida
        espera = ((p.inicio_prep.date() if p.inicio_prep else hoy) - p.elegible).days
        if tarde and espera >= 2:
            p.causa = "capacidad"


# ---------------------------------------------------------------- casos a mano
def _casos(hoy: date) -> list[Pedido]:
    def d(n, hora=10):
        return datetime.combine(hoy - timedelta(days=n), time(hora, 0))

    def mk(pid, ing, region, pagado, lineas, eventos, prometida, causa=None, avisos=(), respuestas=(), reembolso=None):
        p = Pedido(pid, ing, region, pagado, lineas, manual=True)
        p.eventos, p.fecha_prometida, p.causa = list(eventos), prometida, causa
        p.avisos, p.respuestas, p.reembolso = list(avisos), list(respuestas), reembolso
        p.cancelado = p.eventos[-1][0] == "cancelado"
        return p

    def base(dias, hora=9):
        return [("solicitado", d(dias, hora)), ("ingresado", d(dias, hora + 1))]

    def en_ruta(dias, prep, despacho):
        return base(dias) + [("en_preparacion", d(prep, 9)), ("espera_despacho", d(prep, 17)), ("en_ruta", d(despacho, 10))]

    h = hoy
    return [
        # Courier que no cumple: uno con envío pagado (se reembolsa el envío), otro sin pagar (cupón de 30 %)
        mk("PED-C001", d(7, 9), "regiones", 1, [("BAG-008", 1)], en_ruta(7, 6, 5), h - timedelta(days=2), "courier"),
        mk("PED-C002", d(8, 9), "RM", 0, [("BAG-008", 1)], en_ruta(8, 7, 6) + [("proximo_a_llegar", d(5, 8))],
           h - timedelta(days=3), "courier"),
        # Falta de stock: con una orden de compra en camino (C003, C004) y sin ella (C005, C006)
        mk("PED-C003", d(4), "RM", 1, [("BAG-005", 1)], base(4, 10), h - timedelta(days=1), "falta_stock",
           avisos=[("atraso", d(2, 11))], respuestas=[("acepta_espera", d(1, 16))]),
        mk("PED-C004", d(2, 15), "regiones", 0, [("BAG-005", 2)], base(2, 15), h + timedelta(days=6), "falta_stock"),
        mk("PED-C005", d(4), "RM", 0, [("BAG-006", 1)], base(4, 10), h - timedelta(days=1), "falta_stock"),
        mk("PED-C006", d(3, 11), "regiones", 1, [("BAG-006", 1)], base(3, 11), h + timedelta(days=4), "falta_stock"),
        # Dirección errónea: con 1 aviso, con 3 avisos sin respuesta, y con respuesta del cliente
        mk("PED-C007", d(6), "regiones", 1, [("BAG-008", 1)], en_ruta(6, 5, 4) + [("entrega_fallida", d(2, 14))],
           h - timedelta(days=1), "direccion_erronea", avisos=[("solicitud_direccion", d(2, 16))]),
        mk("PED-C008", d(9), "RM", 0, [("BAG-008", 1)], en_ruta(9, 8, 7) + [("entrega_fallida", d(6, 14))],
           h - timedelta(days=4), "direccion_erronea",
           avisos=[("solicitud_direccion", d(6, 16)), ("solicitud_direccion", d(5, 16)), ("solicitud_direccion", d(3, 16))]),
        mk("PED-C009", d(7), "RM", 1, [("BAG-008", 1)], en_ruta(7, 6, 5) + [("entrega_fallida", d(3, 14))],
           h - timedelta(days=1), "direccion_erronea", avisos=[("solicitud_direccion", d(3, 16))],
           respuestas=[("envia_direccion", datetime.combine(hoy, time(8, 0)))]),
        # Cliente sin datos de entrega: con 2 avisos, y con 3 sin respuesta
        mk("PED-C010", d(5, 10), "RM", 1, [("BAG-008", 1)], base(5, 10), h - timedelta(days=1), "cliente_sin_datos",
           avisos=[("solicitud_datos", d(4, 11)), ("solicitud_datos", d(3, 11))]),
        mk("PED-C011", d(8, 10), "regiones", 0, [("BAG-008", 1)], base(8, 10), h - timedelta(days=1), "cliente_sin_datos",
           avisos=[("solicitud_datos", d(7, 11)), ("solicitud_datos", d(5, 11)), ("solicitud_datos", d(3, 11))]),
        # Pedido cancelado tras 3 mensajes sin respuesta, con su reembolso
        mk("PED-C012", d(15, 10), "RM", 1, [("BAG-008", 1)], base(15, 10) + [("cancelado", d(9, 12))],
           h - timedelta(days=10), "cliente_sin_datos",
           avisos=[("solicitud_datos", d(14, 11)), ("solicitud_datos", d(12, 11)), ("solicitud_datos", d(10, 11))],
           reembolso=("datos", "procesado", d(9, 12))),
        # Stock insuficiente: 4 pedidos por preparar suman 8 unidades de un producto con 3 en stock
        mk("PED-C013", d(1, 10), "RM", 1, [("BAG-007", 2)], base(1, 10), h + timedelta(days=4)),
        mk("PED-C014", d(1, 14), "regiones", 0, [("BAG-007", 2)], base(1, 14), h + timedelta(days=7)),
        mk("PED-C015", d(1, 9), "RM", 1, [("BAG-007", 2)], base(1, 9) + [("en_preparacion", d(0, 9))], h + timedelta(days=3)),
        mk("PED-C016", d(1, 11), "regiones", 1, [("BAG-007", 2)], base(1, 11) + [("en_preparacion", d(0, 9))], h + timedelta(days=6)),
    ]


# ---------------------------------------------------------------- órdenes de compra
def _ordenes(hoy: date, costos: dict[str, tuple[float, int]]) -> list[tuple]:
    """(sku, proveedor, cantidad, estado, creada_en, entrega_esperada)."""
    def oc(sku, cantidad, estado, dias_antes):
        creada = hoy - timedelta(days=dias_antes)
        return (sku, costos[sku][1], cantidad, estado, creada, creada + timedelta(days=LEAD_TIME_PROVEEDOR))
    return [
        oc("BAG-005", 25, "en_camino", 6),  # salva a los pedidos sin stock de BAG-005
        oc("BAG-001", 40, "recibida", 40), oc("BAG-003", 200, "recibida", 84), oc("BAG-008", 60, "recibida", 45),
        oc("BAG-002", 30, "recibida", 70), oc("BAG-010", 20, "creada", 1), oc("BAG-012", 15, "cancelada", 20),
    ]


# ---------------------------------------------------------------- generación
def generar(ruta_db: str | Path, hoy: date | None = None, semilla: int = 42) -> dict:
    """Crea la base desde cero (si existe, se reemplaza). Devuelve un resumen."""
    hoy = hoy or date.today()
    ahora = datetime.combine(hoy, time(12, 0))
    rng = random.Random(semilla)

    productos, tasas = _catalogo(rng)
    ventas = _ventas(rng, hoy, tasas)
    # stock de los productos normales: de sobra (más de 30 días de cobertura), así no generan alertas
    recientes: dict[str, int] = {}
    for dias_antes, sku, qty, _ in ventas:
        if 1 <= dias_antes <= 14:
            recientes[sku] = recientes.get(sku, 0) + qty
    for pr in productos:
        if pr["stock"] is None:
            tasa = recientes.get(pr["sku"], 0) / 14
            pr["stock"] = math.ceil(tasa * 30 * rng.uniform(1.2, 1.8)) + pr["seguridad"] if tasa else rng.randint(8, 25)

    pedidos = _pedidos_desde_ventas(rng, hoy, ventas, tasas)
    _simular_flujo(pedidos, hoy, ahora, rng)
    todos = pedidos + _casos(hoy)

    ruta_db = Path(ruta_db)
    ruta_db.parent.mkdir(parents=True, exist_ok=True)
    if ruta_db.exists():
        ruta_db.unlink()
    con = sqlite3.connect(ruta_db)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(ESQUEMA.read_text(encoding="utf-8"))
    precios = {p["sku"]: p["precio"] for p in productos}
    costos = {p["sku"]: (p["costo"], p["proveedor_id"]) for p in productos}

    def iso(x):
        return x.isoformat(sep=" ") if isinstance(x, datetime) else x.isoformat()

    con.executemany("INSERT INTO parametros VALUES (?, ?)", [("fecha_referencia", hoy.isoformat()), *PARAMETROS.items()])
    con.executemany("INSERT INTO proveedores VALUES (?, ?)", PROVEEDORES)
    con.executemany("INSERT INTO productos VALUES (?,?,?,?,?,?,?,?,?,?)", [
        (p["sku"], p["nombre"], p["categoria"], p["tamano"], p["color"], p["material"], p["estilo"], p["precio"],
         p["costo"], p["proveedor_id"]) for p in productos])
    con.executemany("INSERT INTO inventario VALUES (?,?,?)", [(p["sku"], p["stock"], p["seguridad"]) for p in productos])

    for p in todos:
        con.execute("INSERT INTO pedidos VALUES (?,?,?,?,?,?,?)", (p.pedido_id, iso(p.ingreso), p.region, p.estado,
                    p.envio_pagado, iso(p.fecha_prometida), p.causa))
        con.executemany("INSERT INTO lineas_pedido (pedido_id, sku, cantidad, precio_unitario) VALUES (?,?,?,?)",
                        [(p.pedido_id, sku, qty, precios[sku]) for sku, qty in p.lineas])
        con.executemany("INSERT INTO historial_estados (pedido_id, estado, fecha_hora) VALUES (?,?,?)",
                        [(p.pedido_id, e, iso(t)) for e, t in p.eventos])
        con.executemany("INSERT INTO bandeja_avisos (pedido_id, tipo, texto, estado) VALUES (?,?,?,?)",
                        [(p.pedido_id, tipo, f"Aviso simulado ({tipo}), mensaje {n} de {len(p.avisos)}", "enviado")
                         for n, (tipo, _) in enumerate(p.avisos, 1)])
        con.executemany("INSERT INTO respuestas_cliente (pedido_id, tipo, recibida_en) VALUES (?,?,?)",
                        [(p.pedido_id, tipo, iso(t)) for tipo, t in p.respuestas])
        if p.reembolso:
            con.execute("INSERT INTO reembolsos (pedido_id, motivo, estado, creado_en) VALUES (?,?,?,?)",
                        (p.pedido_id, p.reembolso[0], p.reembolso[1], iso(p.reembolso[2])))

    for sku, prov, cantidad, estado, creada, esperada in _ordenes(hoy, costos):
        con.execute("INSERT INTO ordenes_compra (sku, proveedor_id, cantidad, costo_total, estado, creada_en,"
                    " fecha_entrega_esperada) VALUES (?,?,?,?,?,?,?)",
                    (sku, prov, cantidad, cantidad * costos[sku][0], estado, iso(creada), iso(esperada)))

    # compensaciones ya emitidas en el pasado: dos pedidos entregados con 2 días o más de atraso
    tarde = [p for p in pedidos if p.estado == "entregado" and (p.entrega_real.date() - p.fecha_prometida).days >= 2][:2]
    for p in tarde:
        con.execute("INSERT INTO compensaciones (pedido_id, tipo, estado, creada_en) VALUES (?,?,?,?)",
                    (p.pedido_id, "reembolso_envio" if p.envio_pagado else "cupon_30", "emitida", iso(p.entrega_real)))
    con.commit()
    resumen = {tabla: con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] for tabla in
               ("productos", "pedidos", "lineas_pedido", "historial_estados", "ordenes_compra", "bandeja_avisos",
                "compensaciones", "reembolsos", "respuestas_cliente")}
    con.close()
    resumen["fecha_referencia"] = hoy.isoformat()
    return resumen
