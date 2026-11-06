"""Los contratos rechazan lo inválido en la frontera donde ocurre."""
import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas import AccionPropuesta, DecisionRuta, EnviarAviso, Solicitud, Tarea

ACCION = TypeAdapter(AccionPropuesta)


def tarea(agente="bi", instruccion="analiza las ventas de la semana"):
    return {"agente": agente, "instruccion": instruccion}


# ---------------------------------------------------------------- solicitud
def test_la_solicitud_se_limpia_y_se_acota():
    assert Solicitud(texto="   ¿Cuántas ventas hubo?  ").texto == "¿Cuántas ventas hubo?"
    for malo in ("", "  ", "ab", "x" * 501):
        with pytest.raises(ValidationError):
            Solicitud(texto=malo)
    with pytest.raises(ValidationError):
        Solicitud(texto="hola mundo", extra="no")


# ---------------------------------------------------------------- decisión de ruta
def test_decisiones_validas():
    d = DecisionRuta(decision="derivar", tareas=[tarea("bi"), tarea("inventario", "revisa la cobertura")], motivo="cruza áreas")
    assert [t.agente for t in d.tareas] == ["bi", "inventario"]
    assert DecisionRuta(decision="aclarar", pregunta_aclaratoria="¿De qué período?", motivo="ambigua").tareas == []
    assert DecisionRuta(decision="fuera_de_alcance", motivo="no es del negocio").decision == "fuera_de_alcance"


@pytest.mark.parametrize("datos,fragmento", [
    ({"decision": "derivar", "tareas": []}, "al menos una tarea"),
    ({"decision": "derivar", "tareas": [tarea("inventario"), tarea("bi")]}, "orden fijo"),
    ({"decision": "derivar", "tareas": [tarea("bi"), tarea("bi")]}, "dos veces"),
    ({"decision": "aclarar"}, "exige una pregunta"),
    ({"decision": "fuera_de_alcance", "tareas": [tarea()]}, "solo 'derivar' lleva tareas"),
    ({"decision": "derivar", "tareas": [tarea()], "pregunta_aclaratoria": "¿qué?"}, "solo 'aclarar'"),
    ({"decision": "derivar", "tareas": [tarea("finanzas")]}, "bi"),  # agente que no existe
    ({"decision": "derivar", "tareas": [tarea()], "confianza": 0.9}, "Extra inputs"),  # campo inventado
    ({"decision": "quizas", "tareas": []}, "derivar"),
])
def test_decisiones_invalidas_se_rechazan(datos, fragmento):
    with pytest.raises(ValidationError, match=fragmento):
        DecisionRuta(**{"motivo": "prueba", **datos})


def test_el_orden_fijo_se_cumple_aunque_se_salte_un_especialista():
    d = DecisionRuta(decision="derivar", tareas=[tarea("bi"), tarea("logistica", "revisa los pedidos atrasados")], motivo="pregunta 3")
    assert [t.agente for t in d.tareas] == ["bi", "logistica"]


# ---------------------------------------------------------------- acciones propuestas
def test_la_union_de_acciones_elige_el_contrato_por_el_campo_accion():
    a = ACCION.validate_python({"accion": "crear_orden_compra", "sku": "BAG-001", "cantidad": 54, "motivo": "quiebre próximo"})
    assert type(a).__name__ == "CrearOrdenCompra"
    assert type(ACCION.validate_python({"accion": "emitir_compensacion", "pedido_id": "P", "tipo": "cupon_30"})).__name__ == "EmitirCompensacion"
    assert type(ACCION.validate_python({"accion": "cancelar_con_reembolso", "pedido_id": "P", "motivo": "datos"})).__name__ == "CancelarConReembolso"


@pytest.mark.parametrize("malo", [
    {"accion": "volar"},
    {"accion": "crear_orden_compra", "sku": "X", "cantidad": -5, "motivo": "xxx"},
    {"accion": "crear_orden_compra", "sku": "X", "cantidad": 0, "motivo": "xxx"},
    {"accion": "emitir_compensacion", "pedido_id": "P", "tipo": "descuento_50"},
    {"accion": "cancelar_con_reembolso", "pedido_id": "P", "motivo": "porque_si"},
    {"accion": "enviar_aviso", "pedido_id": "P", "tipo": "atraso", "intento": 0},
    {"accion": "crear_orden_compra", "sku": "X", "cantidad": 1, "motivo": "xxx", "descuento": 1},
])
def test_acciones_invalidas_se_rechazan(malo):
    with pytest.raises(ValidationError):
        ACCION.validate_python(malo)


def test_el_aviso_puede_proponerse_sin_texto_y_se_redacta_despues():
    assert EnviarAviso(accion="enviar_aviso", pedido_id="P", tipo="atraso", intento=1).texto is None
