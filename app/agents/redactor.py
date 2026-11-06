"""Redactor: la respuesta final para el usuario, a partir de hechos ya validados.

Garantías:
  - El LLM recibe HECHOS con las cifras y fechas ya formateadas; no calcula nada.
  - Cada número, fecha e identificador del texto debe existir en los hechos. Si no, se rechaza y se reintenta UNA vez
    diciéndole cuáles sobran.
  - Si aun así no cumple, la respuesta la arma el código (`origen = "plantilla"`): el usuario nunca recibe un texto
    con datos que el sistema no calculó.
  - Las consultas fuera de alcance o no entendidas se responden con texto fijo, sin gastar una llamada al LLM.
"""
import re

from app.agents.comun import Dependencias, a_json, fecha_larga, seguro
from app.agents.orquestador import MOTIVO_SIN_ENTENDER
from app.agents.prompts import CAPACIDADES, REDACTOR
from app.clients import ChatMessage
from app.schemas import RespuestaFinal

_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")
_ID = re.compile(r"\b[A-Z]{2,}-[A-Za-z0-9]+\b")
TEXTO_CIERRE = ("No pude completar tu consulta por un problema interno. El detalle quedó registrado para revisarlo. "
                "Puedes intentarlo de nuevo en unos minutos.")


# ---------------------------------------------------------------- formato de los hechos
def _n(x) -> str:
    return str(int(x)) if float(x).is_integer() else f"{x:.1f}".replace(".", ",")


def _miles(x) -> str:
    return f"{int(round(x)):,}".replace(",", ".")


def _describir(a: dict) -> str:
    return {"crear_orden_compra": lambda: f"orden de compra de {a.get('cantidad')} unidades de {a.get('sku')}",
            "enviar_aviso": lambda: f"aviso ({a.get('tipo')}) al pedido {a.get('pedido_id')}",
            "emitir_compensacion": lambda: f"compensación ({a.get('tipo')}) al pedido {a.get('pedido_id')}",
            "cancelar_con_reembolso": lambda: f"cancelación con reembolso del pedido {a.get('pedido_id')}"}[a["accion"]]()


def _hechos_agente(r: dict) -> dict:
    if r["agente"] == "bi":
        v = r["ventas_semana"]
        return {"resumen": r["resumen"], "limitaciones": r["limitaciones"],
                "ventas_semana": None if not v else {"desde": fecha_larga(_fecha(v["desde"])), "hasta": fecha_larga(_fecha(v["hasta"])),
                                                     "unidades": _n(v["unidades"]), "ingresos": _miles(v["ingresos"])},
                "recomendaciones": [{"sku": x["sku"], "nombre": x["nombre"], "accion": x["accion"], "motivo": x["motivo"],
                                     "ventas": _n(x["unidades"]), "ventas_por_unidad_de_stock": f"{x['ventas_por_unidad_de_stock']:.2f}".replace(".", ","),
                                     "margen": f"{round(x['margen_pct'] * 100)} %", "clasificacion": x["clasificacion"],
                                     "evidencia_suficiente": x["evidencia_suficiente"]} for x in r["recomendaciones"]]}
    if r["agente"] == "inventario":
        return {"cantidad_de_alertas": _n(len(r["alertas"])), "alertas": [
            {"sku": a["estado"]["sku"], "nombre": a["estado"]["nombre"], "stock": _n(a["estado"]["stock_actual"]),
             "cobertura_dias": None if a["estado"]["cobertura_dias"] is None else _n(a["estado"]["cobertura_dias"]),
             "alertas": a["estado"]["alertas"], "tendencia": a["tendencia"]["tendencia"] if a["tendencia"] else None,
             "orden_sugerida": _n(a["sugerencia"]["cantidad"]) if a["sugerencia"] else None} for a in r["alertas"]]}
    c, e, d = r["cola"], r["envios"], r["demanda_vs_capacidad"]
    return {"envios_en_curso": {"en_transito": _n(e["total_en_transito"]), "por_estado": {k: _n(v) for k, v in e["por_estado"].items()},
                                "carteras_en_transito": [{"sku": x["sku"], "nombre": x["nombre"], "unidades": _n(x["unidades"]), "pedidos": _n(x["pedidos"])}
                                                         for x in e["carteras"][:8]]},
            "demanda_vs_capacidad": {
                "capacidad_diaria": _n(d["capacidad_diaria"]), "dias_analizados": _n(d["dias_analizados"]),
                "dias_sobre_capacidad": _n(d["dias_sobre_capacidad"]), "pedidos_dia_maximo": _n(d["pedidos_dia_maximo"]),
                "promedio_pedidos_dia": _n(d["promedio_pedidos_dia"]),
                "espera_dias_normales": None if d["espera_promedio_dias_normales"] is None else _n(d["espera_promedio_dias_normales"]),
                "espera_dias_pico": None if d["espera_promedio_dias_pico"] is None else _n(d["espera_promedio_dias_pico"]),
                "entregas_totales": _n(d["entregas_totales"]), "entregas_con_atraso": _n(d["entregas_con_atraso"]),
                "dias_pico": [{"fecha": fecha_larga(_fecha(x["fecha"])), "pedidos": _n(x["pedidos"]), "espera_dias": _n(x["espera_promedio_dias"])}
                              for x in d["dias_pico"]]},
            "cola": {"pedidos_en_cola": _n(c["en_cola"]), "capacidad_diaria": _n(c["capacidad_diaria"]),
                     "dias_para_vaciar": _n(c["dias_para_vaciar"])},
            "pedidos_gestionados": _n(len(r["gestiones"])), "pedidos_omitidos": _n(r["pedidos_omitidos"]),
            "gestiones": [{"pedido": g["pedido_id"], "causa": g["causa"], "explicacion": g["explicacion"],
                           "nueva_fecha": fecha_larga(_fecha(g["nueva_fecha"]["fecha"])) if g["nueva_fecha"] and g["nueva_fecha"]["fecha"] else None,
                           "acciones": [_describir(a) for a in g["acciones"]], "requiere_revision": g["requiere_revision"]}
                          for g in r["gestiones"]]}


def _fecha(texto):
    from datetime import date
    return date.fromisoformat(str(texto)[:10])


def construir_hechos(estado) -> dict:
    ruta = estado.get("ruta") or {}
    if ruta.get("decision") == "fuera_de_alcance":
        return {"tipo": "no_entiendo" if ruta.get("motivo") == MOTIVO_SIN_ENTENDER else "fuera_de_alcance", "puedo_ayudar_con": CAPACIDADES}
    hechos: dict = {"tipo": "resultado", "consulta": estado["solicitud"]["texto"]}
    for r in estado.get("resultados", []):
        hechos[r["agente"]] = _hechos_agente(r)
    acciones = estado.get("acciones", [])
    if acciones:
        decision, ejecucion = estado.get("decision_humana") or {"aprobadas": []}, estado.get("ejecucion") or []
        hechos["acciones"] = {
            "propuestas": _n(len(acciones)),
            "ejecutadas": [r["detalle"] for r in ejecucion if r["ok"]],
            "rechazadas_por_las_reglas": [r["detalle"] for r in ejecucion if not r["ok"]],
            "no_aprobadas": [_describir(a) for i, a in enumerate(acciones) if i not in set(decision["aprobadas"])],
        }
    return hechos


# ---------------------------------------------------------------- validación y respuesta de reserva
def validar_respuesta(texto: str, hechos: dict) -> list[str]:
    """Cada cifra, fecha e identificador del texto debe estar en los hechos. Devuelve los que sobran."""
    base = a_json(hechos)
    numeros = {re.sub(r"\D", "", t) for t in _NUMERO.findall(base)}
    ids = set(_ID.findall(base))
    sobran = [f"cifra o fecha que no está en los hechos: {t}" for t in _NUMERO.findall(texto) if re.sub(r"\D", "", t) not in numeros]
    sobran += [f"identificador que no está en los hechos: {t}" for t in _ID.findall(texto) if t not in ids]
    return sobran


def respuesta_determinista(hechos: dict) -> str:
    """La respuesta armada por el código, sin LLM. Menos elegante, pero con datos garantizados."""
    ayuda = "\n".join(f"- {c}" for c in hechos.get("puedo_ayudar_con", []))
    if hechos["tipo"] == "no_entiendo":
        return f"No logré entender tu consulta. Puedo ayudarte con:\n{ayuda}\nIntenta reformularla indicando si es sobre ventas, inventario o pedidos."
    if hechos["tipo"] == "fuera_de_alcance":
        return f"Esa consulta está fuera de lo que puedo hacer. Puedo ayudarte con:\n{ayuda}"
    L = []
    if "bi" in hechos:
        b, v = hechos["bi"], hechos["bi"]["ventas_semana"]
        L.append("Ventas y estrategia: " + b["resumen"])
        if v:
            L.append(f"- Del {v['desde']} al {v['hasta']} se vendieron {v['unidades']} unidades, por {v['ingresos']} pesos.")
        L += [f"- {x['nombre']} ({x['sku']}): {x['accion'].replace('_', ' ')}. {x['motivo']}" for x in b["recomendaciones"]]
        L += [f"- Ojo: {x}" for x in b["limitaciones"]]
    if "inventario" in hechos:
        i = hechos["inventario"]
        L.append(f"Inventario: hay {i['cantidad_de_alertas']} productos con alertas.")
        for a in i["alertas"]:
            orden = f" Orden sugerida: {a['orden_sugerida']} unidades." if a["orden_sugerida"] else ""
            L.append(f"- {a['nombre']} ({a['sku']}): stock {a['stock']}, alertas: {', '.join(x.replace('_', ' ') for x in a['alertas'])}.{orden}")
    if "logistica" in hechos:
        g = hechos["logistica"]
        e, d = g["envios_en_curso"], g["demanda_vs_capacidad"]
        L.append(f"Envíos: hay {e['en_transito']} pedidos en tránsito. Por estado: "
                 + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in e["por_estado"].items()) + ".")
        L += [f"- {c['nombre']} ({c['sku']}): {c['unidades']} unidades en {c['pedidos']} pedidos" for c in e["carteras_en_transito"]]
        L.append(f"Demanda y capacidad: la capacidad es de {d['capacidad_diaria']} pedidos por día y hubo {d['dias_sobre_capacidad']} días por sobre ella "
                 f"(máximo {d['pedidos_dia_maximo']} pedidos en un día). {d['entregas_con_atraso']} de {d['entregas_totales']} entregas llegaron con atraso.")
        L.append(f"Pedidos: {g['cola']['pedidos_en_cola']} en la cola de preparación, a {g['cola']['capacidad_diaria']} por día. "
                 f"Se gestionaron {g['pedidos_gestionados']} pedidos con problema.")
        for x in g["gestiones"]:
            fecha = f" Nueva fecha: {x['nueva_fecha']}." if x["nueva_fecha"] else ""
            L.append(f"- {x['pedido']} ({x['causa']}): {x['explicacion']}.{fecha}")
        if g["pedidos_omitidos"] != "0":
            L.append(f"- Quedaron {g['pedidos_omitidos']} pedidos fuera de esta revisión por el límite de tamaño.")
    if "acciones" in hechos:
        a = hechos["acciones"]
        L.append("Acciones:")
        L += [f"- Hecho: {x}" for x in a["ejecutadas"]] + [f"- No se ejecutó (reglas): {x}" for x in a["rechazadas_por_las_reglas"]]
        L += [f"- No aprobada: {x}" for x in a["no_aprobadas"]]
    return "\n".join(L) or "No encontré nada que reportar para tu consulta."


async def redactar(deps: Dependencias, hechos: dict) -> tuple[str, str]:
    """(texto, origen). Texto fijo para lo que no necesita LLM; LLM validado para los resultados; plantilla de reserva."""
    if hechos["tipo"] != "resultado":
        return respuesta_determinista(hechos), "plantilla"
    mensajes = [ChatMessage(role="system", content=REDACTOR), ChatMessage(role="user", content=f"HECHOS:\n{a_json(hechos)}\n\nEscribe la respuesta final.")]
    for _ in range(2):  # el intento original y un reintento con lo que sobra
        texto = (await deps.texto.generate(mensajes, deps.config_texto)).content.strip()
        sobran = validar_respuesta(texto, hechos)
        if not sobran and len(texto) >= 10:
            return texto, "llm"
        mensajes = mensajes + [ChatMessage(role="assistant", content=texto or "(vacío)"),
                               ChatMessage(role="user", content="Tu respuesta no cumple: " + "; ".join(sobran[:6]) + ". Reescríbela usando solo los hechos.")]
    return respuesta_determinista(hechos), "plantilla"


def crear_nodo_redactor(deps: Dependencias):
    @seguro("redactor")
    async def nodo(estado):
        texto, origen = await redactar(deps, construir_hechos(estado))
        return {"respuesta": RespuestaFinal(texto=texto, origen=origen).model_dump(), "pasos": 1}

    return nodo


def crear_nodo_cierre():
    """Cierre controlado: nunca falla, deja el error a la vista y le da al usuario un mensaje honesto."""
    async def nodo(estado):
        return {"error": estado.get("error") or "Se superó el límite de pasos por consulta o el flujo siguió un camino no permitido",
                "respuesta": RespuestaFinal(texto=TEXTO_CIERRE, origen="plantilla").model_dump(), "pasos": 0}

    return nodo
