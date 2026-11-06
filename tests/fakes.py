"""Dobles de prueba compartidos."""
import hashlib
import math

from langchain_core.embeddings import Embeddings

from app.rag.retriever import tokenizar


class EmbeddingsHash(Embeddings):
    """Embeddings falsos y deterministas: bolsa de palabras proyectada a `dim` dimensiones.

    No entienden significado (dos frases con las mismas palabras quedan cerca, nada más), pero
    permiten probar toda la mecánica sin red ni costo.
    """

    def __init__(self, dim: int = 128):
        self.dim = dim
        self.llamadas_documentos: list[int] = []  # tamaño de cada lote pedido
        self.llamadas_consulta = 0

    def _vector(self, texto: str) -> list[float]:
        v = [0.0] * self.dim
        for ficha in tokenizar(texto):
            v[int(hashlib.md5(ficha.encode()).hexdigest(), 16) % self.dim] += 1.0
        norma = math.sqrt(sum(x * x for x in v)) or 1.0
        if norma == 1.0 and not any(v):
            v[0] = 1.0
        return [x / norma for x in v]

    def embed_documents(self, texts):
        return [self._vector(t) for t in texts]

    def embed_query(self, text):
        return self._vector(text)

    async def aembed_documents(self, texts):
        self.llamadas_documentos.append(len(texts))
        return self.embed_documents(texts)

    async def aembed_query(self, text):
        self.llamadas_consulta += 1
        return self.embed_query(text)


# ---------------------------------------------------------------- modelos falsos para los agentes
import json
from types import SimpleNamespace

from app.clients import ModelResponse, Provider


class ChatFalso:
    """Modelo de chat de LangChain falso. `respuesta` es una lista (se consume en orden) o una función(mensajes) -> str."""

    def __init__(self, respuesta):
        self.respuesta, self.llamadas = respuesta, []

    async def ainvoke(self, mensajes):
        self.llamadas.append(mensajes)
        r = self.respuesta(mensajes) if callable(self.respuesta) else self.respuesta.pop(0)
        return SimpleNamespace(content=r)

    def sistema(self, i: int) -> str:
        return self.llamadas[i][0][1]


class TextoFalso:
    """Cliente de texto falso (como app.clients). `respuesta` es una función(mensajes) -> str."""

    def __init__(self, respuesta):
        self.respuesta, self.llamadas = respuesta, []

    async def generate(self, mensajes, config):
        self.llamadas.append(mensajes)
        return ModelResponse(content=self.respuesta(mensajes), provider=Provider.GOOGLE, model_name=config.model_name)


def ruta(decision, *agentes, pregunta=None, motivo="decisión de prueba"):
    """Una DecisionRuta como JSON."""
    return json.dumps({"decision": decision, "tareas": [{"agente": a, "instruccion": f"resuelve lo tuyo ({a})"} for a in agentes],
                       "pregunta_aclaratoria": pregunta, "motivo": motivo})


def aviso_valido(mensajes) -> str:
    """Un redactor de avisos de mentira que cumple las reglas según los hechos que recibe en el prompt."""
    hechos = mensajes[-1].content if hasattr(mensajes[-1], "content") else mensajes[-1][1]
    texto = "Hola, tu pedido {numero_pedido} tiene un inconveniente que estamos resolviendo."
    if "'nueva_fecha': '(usa {nueva_fecha})'" in hechos:
        texto += " La nueva fecha estimada de entrega es el {nueva_fecha}."
    if "'compensacion': '(usa {compensacion})'" in hechos:
        texto += " Como el atraso supera los dos días, {compensacion}."
    if "'ofrecer_reembolso': True" in hechos:
        texto += " Si prefieres, te reembolsamos el total."
    if "'mensaje_numero': 3" in hechos and "solicitud" in hechos:
        texto += " Si no recibimos respuesta, cancelaremos el pedido."
    return texto



# ---------------------------------------------------------------- un "negocio" completo de mentira, sin estado (sirve con jobs concurrentes)
TEXTO_RESPUESTA = "Revisé lo que pediste y dejé el detalle de lo hecho y de lo pendiente en esta respuesta, para que lo revises con calma."


def chat_negocio(mensajes) -> str:
    """Un LLM de mentira que reacciona al prompt: orquestador, pasos de BI y dictamen. No guarda estado."""
    sistema, usuario = mensajes[0][1], mensajes[1][1]
    if sistema.startswith("Eres el orquestador"):
        consulta = usuario.lower()
        if "falla" in consulta:
            return "esto no es un JSON"
        if "chiste" in consulta or "clima" in consulta:
            return ruta("fuera_de_alcance", motivo="no es del negocio")
        if "cómo vamos" in consulta and "aclaración" not in consulta:
            return ruta("aclarar", pregunta="¿Qué quieres revisar: ventas, inventario o pedidos?")
        if "inventario" in consulta or "ordenar" in consulta:
            return ruta("derivar", "inventario")
        if "pedidos" in consulta:
            return ruta("derivar", "logistica")
        return ruta("derivar", "bi")
    if "Decide el siguiente paso" in usuario:  # BI: sin estado, decide según lo que ya observó
        if "[ventas_por_producto]" in usuario:
            return json.dumps({"paso": "terminar", "motivo": "tengo lo necesario"})
        if "[resumen_ventas]" in usuario:
            return json.dumps({"paso": "herramienta", "nombre": "ventas_por_producto"})
        return json.dumps({"paso": "herramienta", "nombre": "resumen_ventas", "dias": 7})
    return json.dumps({"resumen": "Las ventas de la semana se concentran en pocos productos con buen desempeño.",
                       "recomendaciones": [{"sku": "BAG-003", "accion": "incluir_en_drop", "motivo": "tiene un gran volumen de ventas"}]})


def texto_negocio(mensajes) -> str:
    """Texto de mentira: avisos válidos para el redactor de avisos y una respuesta final sin cifras para el redactor."""
    return aviso_valido(mensajes) if mensajes[0].content.startswith("Redactas avisos") else TEXTO_RESPUESTA


# ---------------------------------------------------------------- LangSmith de mentira: registra en vez de enviar
from langsmith import Client


class ClienteEspia(Client):
    """Cliente de LangSmith que guarda las trazas en memoria. Permite probar la observabilidad sin red ni cuenta."""

    def __init__(self):
        super().__init__(api_url="http://127.0.0.1:9", api_key="clave-falsa", auto_batch_tracing=False)
        self.registro: list[tuple[str, dict]] = []

    def create_run(self, name, inputs, run_type, **kw):
        self.registro.append(("create", {"name": name, "run_type": run_type, "inputs": inputs, **kw}))

    def update_run(self, run_id, **kw):
        self.registro.append(("update", kw))

    def creadas(self):
        return [d for t, d in self.registro if t == "create"]

    def actualizadas(self):
        return [d for t, d in self.registro if t == "update"]
