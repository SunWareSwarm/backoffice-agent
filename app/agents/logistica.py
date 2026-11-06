"""Logística: un workflow. La matriz de gestión decide (código); el LLM solo redacta los avisos.

Pedidos con problema -> gestión de cada uno (acciones, nueva fecha) -> redacción de los avisos.

Los avisos son el único punto con LLM, y están blindados:
  - el LLM escribe con MARCADORES ({numero_pedido}, {nueva_fecha}, {compensacion}); el código los rellena;
  - se rechaza todo texto con un dígito propio, con marcadores desconocidos o al que le falte lo obligatorio;
  - si tras un reintento el texto sigue sin cumplir, el aviso lo arma el código con una plantilla.
Así el modelo no puede inventar una fecha, una cifra ni una compensación.
"""
import asyncio
import re

from app.agents.comun import Dependencias, con_base, fecha_larga, resumen_error, seguro, texto_de
from app.agents.prompts import AVISO
from app.clients import ChatMessage
from app.schemas import EnviarAviso, Gestion, ResultadoLogistica
from app.tools import orders
from app.tools.comun import Parametros
from app.tools.knowledge import buscar_en_manuales

_MARCADORES = {"numero_pedido", "nueva_fecha", "compensacion"}
# A igual atraso, se atiende primero lo que exige una decisión puntual y al final la capacidad, que suele afectar a muchos pedidos a la vez.
_PRIORIDAD_CAUSA = {"courier": 0, "falta_stock": 1, "direccion_erronea": 2, "cliente_sin_datos": 3, "capacidad": 4, None: 5}


def _frase_compensacion(tipo: str, p: Parametros) -> str:
    return "te reembolsamos el costo del envío" if tipo == "reembolso_envio" else f"te dejamos un cupón de {p.cupon_porcentaje} % de descuento para tu próxima compra"


def validar_aviso(texto: str, a: EnviarAviso, p: Parametros) -> list[str]:
    """Devuelve los problemas del texto que propuso el LLM (lista vacía = válido)."""
    errores = []
    usados = set(re.findall(r"\{(\w+)\}", texto))
    if usados - _MARCADORES:
        errores.append(f"marcadores desconocidos: {sorted(usados - _MARCADORES)}")
    if "numero_pedido" not in usados:
        errores.append("falta {numero_pedido}")
    if a.nueva_fecha and "nueva_fecha" not in usados:
        errores.append("falta {nueva_fecha}")
    if not a.nueva_fecha and "nueva_fecha" in usados:
        errores.append("no hay nueva fecha: no uses {nueva_fecha}")
    if a.compensacion and "compensacion" not in usados:
        errores.append("falta {compensacion}")
    if not a.compensacion and "compensacion" in usados:
        errores.append("no hay compensación: no uses {compensacion}")
    if re.search(r"\d", re.sub(r"\{\w+\}", "", texto)):
        errores.append("no escribas dígitos: las fechas y cifras van en marcadores y los números en palabras")
    if a.ofrece_reembolso and "reembols" not in texto.lower():
        errores.append("debes ofrecer el reembolso")
    if a.tipo != "atraso" and a.intento >= p.max_mensajes and "cancel" not in texto.lower():
        errores.append("es el último mensaje: debes avisar que, sin respuesta, el pedido se cancelará")
    if not 30 <= len(texto) <= 700:
        errores.append("el largo debe estar entre 30 y 700 caracteres")
    if not texto.lstrip().startswith("Hola"):
        errores.append('debe empezar con "Hola,"')
    return errores


def rellenar(texto: str, a: EnviarAviso, p: Parametros) -> str:
    valores = {"numero_pedido": a.pedido_id, "nueva_fecha": fecha_larga(a.nueva_fecha) if a.nueva_fecha else "",
               "compensacion": _frase_compensacion(a.compensacion, p) if a.compensacion else ""}
    return re.sub(r"\{(\w+)\}", lambda m: valores[m.group(1)], texto)


def aviso_de_plantilla(a: EnviarAviso, causa: str | None, p: Parametros) -> str:
    """El aviso armado por el código, sin LLM. Basado en los ejemplos del manual logístico."""
    pid, fecha = a.pedido_id, fecha_larga(a.nueva_fecha) if a.nueva_fecha else ""
    comp = f" Como el atraso supera los {p.compensacion_desde} días, {_frase_compensacion(a.compensacion, p)}." if a.compensacion else ""
    if a.tipo == "solicitud_direccion":
        if a.intento >= p.max_mensajes:
            return (f"Hola, te escribimos por última vez por tu pedido {pid}: aún necesitamos tu dirección completa para reprogramar la "
                    "entrega. Si no la recibimos, cancelaremos el pedido y te reembolsaremos el total.")
        return (f"Hola, no pudimos entregar tu pedido {pid} porque la dirección registrada no era válida. ¿Nos confirmas tu dirección "
                "completa? Apenas la recibamos, reprogramamos la entrega.")
    if a.tipo == "solicitud_datos":
        if a.intento >= p.max_mensajes:
            return (f"Hola, te escribimos por última vez por tu pedido {pid}: aún necesitamos tus datos de entrega para despacharlo. "
                    "Si no los recibimos, cancelaremos el pedido y te reembolsaremos el total.")
        return f"Hola, para despachar tu pedido {pid} nos faltan tus datos de entrega. ¿Puedes enviárnoslos? Con eso lo dejamos en preparación de inmediato."
    if causa == "falta_stock":
        if not fecha:
            return (f"Hola, el producto de tu pedido {pid} está agotado y por ahora no tenemos una orden de reposición en camino. "
                    "Podemos reembolsarte el total de inmediato si lo prefieres.")
        cierre = " Si prefieres, puedes pedir el reembolso completo y lo tramitamos de inmediato." if a.ofrece_reembolso else ""
        return f"Hola, el producto de tu pedido {pid} está agotado y ya lo estamos reponiendo. Podemos enviártelo y estimamos la entrega el {fecha}. ¿Te sirve esperar?{cierre}"
    if causa == "capacidad":
        return f"Hola, estamos con alta demanda y tu pedido {pid} se preparará más tarde de lo previsto. La nueva fecha estimada de entrega es el {fecha}.{comp}"
    return (f"Hola, tu pedido {pid} se está demorando porque el courier no cumplió el plazo de entrega. Ya presentamos el reclamo y la nueva "
            f"fecha estimada de entrega es el {fecha}.{comp}")


async def _redactar_aviso(deps: Dependencias, g: Gestion, a: EnviarAviso, p: Parametros, ejemplos: str) -> EnviarAviso:
    hechos = {"causa": g.causa, "tipo_de_aviso": a.tipo, "mensaje_numero": a.intento, "maximo_de_mensajes": p.max_mensajes,
              "nueva_fecha": "(usa {nueva_fecha})" if a.nueva_fecha else None,
              "compensacion": "(usa {compensacion})" if a.compensacion else None,
              "ofrecer_reembolso": a.ofrece_reembolso, "situacion": g.explicacion}
    consulta = f"Hechos del aviso:\n{hechos}\n\nEjemplos de tono del manual:\n{ejemplos or '(sin ejemplos)'}\n\nRedacta el aviso."
    mensajes = [ChatMessage(role="system", content=AVISO), ChatMessage(role="user", content=consulta)]
    for _ in range(2):  # el intento original y un reintento con los errores
        texto = (await deps.texto.generate(mensajes, deps.config_texto)).content.strip().strip('"')
        errores = validar_aviso(texto, a, p)
        if not errores:
            return a.model_copy(update={"texto": rellenar(texto, a, p)})
        mensajes = mensajes + [ChatMessage(role="assistant", content=texto or "(vacío)"),
                               ChatMessage(role="user", content="El aviso no cumple: " + "; ".join(errores) + ". Escríbelo de nuevo.")]
    return a.model_copy(update={"texto": aviso_de_plantilla(a, g.causa, p)})  # el código lo arma: nunca queda sin aviso


def _calcular(con, p, tope: int):
    problemas = sorted(orders.pedidos_con_problema(con, p), key=lambda x: (-x.dias_atraso, _PRIORIDAD_CAUSA[x.causa], x.pedido_id))
    gestiones = [orders.decidir_gestion(con, p, x) for x in problemas[:tope]]
    return (orders.cola_de_preparacion(con, p), gestiones, len(problemas) - len(gestiones), p,
            orders.envios_en_curso(con, p), orders.demanda_vs_capacidad(con, p))


def crear_nodo_logistica(deps: Dependencias):
    @seguro("logistica")
    async def nodo(estado):
        cola, gestiones, omitidos, p, envios, demanda = await con_base(deps, lambda con, p: _calcular(con, p, deps.max_pedidos_logistica))
        ejemplos: dict[str, str] = {}  # un ejemplo de tono por causa, no por aviso: menos consultas y menos tokens
        if deps.retriever:
            for causa in {g.causa for g in gestiones if g.causa}:
                f = await buscar_en_manuales(deps.retriever, f"ejemplo de aviso al cliente por {causa.replace('_', ' ')}", k=2)
                ejemplos[causa] = "\n".join(x.texto[:400] for x in f.fragmentos)

        async def con_texto(g: Gestion) -> Gestion:
            nuevas = [await _redactar_aviso(deps, g, a, p, ejemplos.get(g.causa or "", "")) if a.accion == "enviar_aviso" and not a.texto else a
                      for a in g.acciones]
            return g.model_copy(update={"acciones": nuevas})

        gestiones = list(await asyncio.gather(*(con_texto(g) for g in gestiones)))
        resultado = ResultadoLogistica(cola=cola, envios=envios, demanda_vs_capacidad=demanda, gestiones=gestiones, pedidos_omitidos=omitidos,
                                       revision_humana=[g.pedido_id for g in gestiones if g.requiere_revision])
        return {"resultados": [resultado.model_dump(mode="json")],
                "acciones": [a.model_dump(mode="json") for g in gestiones for a in g.acciones],
                "tareas_pendientes": [t for t in estado["tareas_pendientes"] if t["agente"] != "logistica"], "pasos": 1}

    return nodo
