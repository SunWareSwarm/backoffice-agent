# Registro de iteraciones

Qué se agregó en cada iteración y en qué estado quedó cada pieza.

## Iteración 1: paso 4.1 (repositorio y entorno)

- `app/config.py`: variables de entorno validadas con Pydantic; sin nombres de modelo en el código.
- `app/llm.py`: cliente abstracto para LangChain (chat y embeddings, por proveedor).
- Esqueleto de carpetas, `.env.example`, `requirements.txt`, `.gitignore`, pruebas (8).
- `data/eval/`: conjunto de evaluación del RAG (47 preguntas) y catálogo de 61 fragmentos.

## Iteración 2: pasos 4.1b y 4.2

**4.1b, cliente de texto (el cliente del Módulo 1 integrado, opción C)**

- Paquete `app/clients/`: `base.py` (política común), `errors.py`, `schemas.py`, `retry.py`, `manager.py` y un
  cliente por proveedor (Google, OpenAI, Anthropic).
- Los seis hallazgos de la revisión, aplicados:
  1. Sin nombres de modelo en el código; el modelo es obligatorio.
  2. Errores tipados y reintentables (antes todo era `RuntimeError`).
  3. Roles cerrados (`system`, `user`, `assistant`).
  4. Tiempo máximo y reintentos con espera creciente.
  5. Paquete con importaciones absolutas.
  6. Se define la regla de reparto con `app/llm.py` (ver `decisiones.md`).
- Mejoras extra: se desactivan los reintentos propios de los SDK; se miden latencia e intentos en cada respuesta;
  `GEMINI_API_KEY` se acepta como alias de `GOOGLE_API_KEY`.
- 34 pruebas nuevas, todas con SDK falsos (sin red ni costo).

**4.2, base de datos simulada**

- `app/db/schema.sql`, `app/db/connection.py` (conexión de solo lectura), `app/db/seed.py` y `scripts/seed_db.py`.
- Unos 600 pedidos en 12 semanas, 30 productos y 4 proveedores, con un flujo normal simulado (cola, capacidad y courier).
- Casos sembrados: quiebre próximo, stock estancado, falso ganador, atrasos de las 5 causas, falta de stock con y
  sin orden en camino, stock insuficiente, pico de demanda con cuello de botella y estados borde.
- 32 pruebas nuevas: cada caso se verifica con consultas SQL independientes y con cuatro semillas distintas.

**Estado de las piezas**

| Pieza | Estado |
| --- | --- |
| `config.py`, `llm.py`, `app/clients/`, `app/db/` | Listos y probados |
| `schemas.py`, `state.py`, `graph.py`, `app/rag/*` | Esqueleto vacío |
| `agents/`, `tools/` | Por construir (fases 4.4 y 4.5) |

**Total de pruebas: 74.**

## Iteración 3: paso 4.3 (RAG)

- `app/rag/chunking.py`: corte por estructura (61 fragmentos) validado contra el catálogo.
- `app/rag/ingest.py` y `scripts/ingest_rag.py`: ingesta a Chroma con coseno, en lotes y sin mezclar modelos.
- `app/rag/retriever.py`: búsqueda vectorial, BM25 e híbrida (RRF), con filtros por metadatos.
- `app/rag/evaluacion.py` y `scripts/eval_rag.py`: Acierto@k y MRR con las 47 preguntas.
- `scripts/verificar_entorno.py`: prueba eslabón por eslabón el entorno real (configuración, manuales, modelo de texto,
  embeddings y Chroma) y explica cuál falla.
- Los tres manuales ya están en `data/docs_rag/`.
- 45 pruebas nuevas, con modelos falsos. **Total: 119.**
- Primera medición (solo BM25) en `docs/decisiones.md`.

**Estado de las piezas**

| Pieza | Estado |
| --- | --- |
| `config.py`, `llm.py`, `app/clients/`, `app/db/`, `app/rag/` | Listos y probados |
| `schemas.py`, `state.py`, `graph.py` | Esqueleto vacío |
| `agents/`, `tools/` | Por construir (fases 4.4 y 4.5) |

**Pendiente de verificar con claves reales** (necesita la clave de Google en el `.env`): la ingesta con el modelo de
embeddings real y la medición de los modos vectorial e híbrido. Todo lo demás está probado sin red.

```bash
python -m scripts.verificar_entorno   # 1. ¿funciona cada eslabón?
python -m scripts.ingest_rag          # 2. ingesta a Chroma
python -m scripts.eval_rag --detalle  # 3. medición de los tres modos
```

## Iteración 4: paso 4.4 (herramientas y contratos)

- `app/schemas.py`: contratos de la solicitud, la decisión de ruta, los resultados de las herramientas y las acciones propuestas.
- `app/tools/`: `sales.py`, `inventory.py`, `orders.py` (incluye la matriz de gestión como código), `actions.py`
  (acciones simuladas con comprobación previa) y `knowledge.py` (consulta a los manuales).
- `app/tools/comun.py`: los parámetros de negocio se leen de la base, junto con el reloj simulado.
- 92 pruebas nuevas, cada herramienta contra los casos sembrados. **Total: 211.**

**Estado de las piezas**

| Pieza | Estado |
| --- | --- |
| `config.py`, `llm.py`, `clients/`, `db/`, `rag/`, `schemas.py`, `tools/` | Listos y probados |
| `state.py`, `graph.py`, `agents/` | Por construir (paso 4.5) |
| API, Redis, aprobación, observabilidad | Por construir (reutilizan la pre-entrega 7) |

## Iteración 5: paso 4.5 (agentes, state y grafo)

- `app/state.py` y `app/graph.py`: el grafo completo, con dos pausas (aclaración y aprobación) y cierre controlado.
- `app/agents/`: orquestador, BI (bucle acotado), inventario (código), logística (matriz + avisos) y redactor (validado).
- `docs/grafo.mmd`: el diagrama, generado desde el código (`python -m scripts.make_diagram`).
- 30 pruebas nuevas con modelos falsos. **Total: 241.**

**Qué está probado y qué no**

- Probado: el ruteo, el orden fijo, las dos pausas, la ejecución de lo aprobado, el rechazo de acciones viejas, los límites,
  los cierres controlados y todas las validaciones, con modelos falsos.
- **No probado todavía:** el comportamiento de los prompts con modelos reales y el checkpointer de Redis. Es lo primero que
  se verifica en la integración real.

**Estado de las piezas**

| Pieza | Estado |
| --- | --- |
| Todo `app/` salvo la API | Listo y probado con modelos falsos |
| API, worker, Redis, aprobación por HTTP, observabilidad | Por construir (reutilizan la pre-entrega 7) |

## Iteración 6: API, worker, Redis y observabilidad

- `app/api/`: `main.py` (arranque y endpoints), `jobs.py` (estado en Redis), `worker.py`, `hitl.py` (aclaración y
  aprobación), `models.py` y `deps.py`. Reciclado de la pre-entrega 7 y adaptado al grafo nuevo.
- `app/observability.py` y el cliente de texto decorado para LangSmith (con proveedor, modelo y tokens).
- `scripts/load_test.py` (5 consultas de ventas), `scripts/latency_report.py`, `Dockerfile` y `docker-compose.yml`.
- `README.md` completo, con cómo levantarlo, cómo usar la API y qué evidencias guardar.
- 52 pruebas nuevas (API, estado de los trabajos, observabilidad, carga y trazas del cliente). **Total: 296.**

**Qué está probado y qué no**

- Probado, sin red: todos los endpoints, las dos pausas, el candado por pausa, los fallos, el tiempo máximo, la concurrencia
  y el arranque con errores. La API se prueba por HTTP con el grafo real, modelos falsos y Redis simulado.
- **No probado todavía** (necesita tus claves y Docker): el arranque real con `redis-stack` (el checkpointer crea índices ahí),
  los prompts con modelos reales, la ingesta con embeddings reales, y que LangSmith calcule el costo del modelo.

**Estado de las piezas**

| Pieza | Estado |
| --- | --- |
| Todo el código del proyecto | Escrito y probado con modelos y Redis simulados |
| Verificación con servicios reales | Pendiente: `verificar_entorno`, `ingest_rag`, `eval_rag`, `load_test` |
| Evidencias (capturas de costo, p95 y trazas) | Pendiente: dependen de la corrida real |

## Iteración 7: cierre de huecos antes de la verificación real

- Dos herramientas nuevas (`envios_en_curso`, `demanda_vs_capacidad`) integradas a Logística y al Redactor: ahora las cinco
  preguntas cruzadas del proyecto se pueden responder con datos.
- Criterio de prioridad de Logística corregido (ver `decisiones.md`).
- `docs/plan-de-ejecucion.md`: la guía paso a paso para probar todo desde VS Code.
- Corrección: el `.env` ahora llega al entorno, para que LangSmith quede activo; el verificador de entorno lo comprueba.
- 11 pruebas nuevas. **Total: 307.**

## Iteración 8: primeros hallazgos en la máquina del usuario (Windows)

- El verificador informaba `✗ Chroma` en Windows por un `PermissionError` al borrar su carpeta temporal: corregido.
- Las pruebas leían el `.env` real de quien las ejecutaba: aislado con un fixture automático.
- Una advertencia de LangSmith cuando una llamada al modelo fallaba: corregida.
- 4 pruebas nuevas. **Total: 311.**

## Iteración 9: primer error real con un modelo (503) y README

- Reintentos con espera creciente y modelo de respaldo, para el modelo de chat y para el cliente de texto.
- `python -m scripts.listar_modelos` y verificación del modelo de respaldo en `verificar_entorno`.
- README completo y detallado, con la correspondencia con los requisitos del curso y 5 diagramas Mermaid
  (vista general, grafo, colaboración entre agentes, flujo de la API y ciclo de vida de un trabajo).
- 33 pruebas nuevas. **Total: 344.**

## Iteración 10: arranque con un solo comando

- `scripts/arrancar.py`: prepara lo que falte (base simulada y RAG) y levanta la API; idempotente y con reintentos.
- `Dockerfile` y `docker-compose.yml` reescritos: `docker compose up --build` desde un clone limpio, con los datos en un volumen.
- El modo del RAG por defecto es `hibrido`; `.env.example` sin comentarios pegados a los valores.
- 20 pruebas nuevas. **Total: 364.**
- **Pendiente de verificar con Docker real** (no se puede ejecutar en el entorno de pruebas): fase 11 del plan.

## Próxima iteración: verificación con servicios reales

Correr todo con claves reales, corregir lo que aparezca (prompts, cuotas, nombres de modelo) y reunir las evidencias.
