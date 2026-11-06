# Plan de ejecución y pruebas, paso a paso

Esta guía te lleva, en orden, desde abrir el proyecto en VS Code hasta tener todas las evidencias de la entrega.
Cada fase dice qué pruebas, qué debes ver y qué hacer si algo falla. **No avances de fase si una falla.**

## Cómo usarla

- Marca cada `- [ ]` cuando lo cumplas (en VS Code basta con cambiar el espacio por una `x`).
- Todos los comandos se ejecutan **desde la carpeta raíz del proyecto** (la que tiene `README.md`) y con el entorno virtual activo.
- Si algo falla, envíame siempre tres cosas: **(1)** el comando que ejecutaste, **(2)** la salida completa (no solo la última línea) y **(3)** tu sistema operativo y `python --version`.
- Los comandos de PowerShell (Windows) y de macOS/Linux se indican cuando difieren.
- Los números que verás (92 unidades, 54 de orden sugerida...) salen del Anexo A. **Usa siempre `--hoy 2026-11-03`** al crear los datos, o no coincidirán.

## Mapa del recorrido

| Fase | Qué pruebas | Necesita | Tiempo | Imprescindible |
|---|---|---|---|---|
| 0 | Tu máquina y tus cuentas | — | 30–45 min | Sí |
| 1 | Abrir el proyecto e instalar | Internet | 20 min | Sí |
| 2 | Las 364 pruebas automáticas | — | 10 min | Sí |
| 3 | Los datos simulados | — | 25 min | Sí |
| 4 | Configuración y entorno real | Claves | 25 min | Sí |
| 5 | RAG: ingesta y evaluación | Clave de Google | 30 min | Sí |
| 6 | Redis | Docker | 10 min | Sí |
| 7 | Levantar la API | Fases 3–6 | 10 min | Sí |
| 8 | Las 10 pruebas funcionales | Fase 7 | 90–120 min | Sí |
| 9 | Observabilidad en LangSmith | Cuenta LangSmith | 30 min | Sí |
| 10 | Prueba de carga y capturas | Fase 9 | 40 min | Sí |
| 11 | **Todo con un solo comando** (`docker compose up --build`) | Docker y tu `.env` | 30 min | Sí |
| 12 | Entrega: clone limpio y repositorio | — | 30 min | Sí |

**Reparto sugerido:** día 4 = fases 0 a 8 (unas 4 horas). Día 5 = fases 9 a 12 y la entrega. Si ya hiciste las fases 0 a 8, pasa a la **fase 11**: es el requisito del arranque con un comando.

**Ruta mínima si te queda muy poco tiempo:** fases 0, 1, 2, 4, 6, 7; en la 8, solo las pruebas T1, T2 y T10; luego 9, 10 y 12.

---

## Fase 0. Preparar la máquina (30–45 min)

**Objetivo:** tener lo necesario instalado y las claves a mano.

- [ ] **Python 3.12.** Comprueba con `python --version` (en macOS/Linux, `python3 --version`). Si es anterior a 3.11, el proyecto no funcionará.
- [ ] **VS Code** con las extensiones *Python* y *Pylance*. Recomendada: *SQLite Viewer* (para mirar la base simulada).
- [ ] **Docker Desktop abierto** y funcionando: `docker --version` y `docker compose version` deben responder.
- [ ] **Clave de Google AI Studio** (`GOOGLE_API_KEY`). Si usaste la pre-entrega 7, es la misma.
- [ ] **El nombre exacto del modelo de chat** que usaste en la pre-entrega 7 (míralo en su `.env`). No tiene valor por defecto a propósito.
- [ ] **Cuenta de LangSmith** y su clave (`LANGSMITH_API_KEY`): en smith.langchain.com, tu perfil, *API Keys*.
- [ ] Solo Windows: si PowerShell dice *«la ejecución de scripts está deshabilitada»* al activar el entorno, ejecuta una vez
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

**Envíame si falla:** el mensaje de `docker compose version` o de `python --version`.

---

## Fase 1. Abrir el proyecto e instalar (20 min)

**Objetivo:** el proyecto abierto y sus dependencias instaladas.

- [ ] Descomprime `backoffice-agent-esqueleto.zip`. En VS Code: *File, Open Folder* y elige la carpeta `backoffice-agent`.
- [ ] Abre la terminal de VS Code (`` Ctrl+` ``) y crea el entorno virtual:
  ```
  python -m venv .venv
  ```
- [ ] Actívalo. Windows (PowerShell): `.venv\Scripts\Activate.ps1`. macOS/Linux: `source .venv/bin/activate`. El prompt debe empezar con `(.venv)`.
- [ ] Elige el intérprete: `Ctrl+Shift+P`, escribe *Python: Select Interpreter* y elige el de `.venv`.
- [ ] Instala todo: `pip install -r requirements-dev.txt` (tarda de 3 a 6 minutos).
- [ ] Comprueba que el proyecto importa: `python -c "import app.graph; print('ok')"`

**Resultado esperado:** imprime `ok`.

**Si falla:** *«No module named 'app'»* significa que no estás en la carpeta raíz o no activaste el entorno. En Windows, un error de
*«Microsoft Visual C++ 14.0 is required»* al instalar se resuelve instalando las *Build Tools de Visual Studio*.

---

## Fase 2. Las pruebas automáticas (10 min)

**Objetivo:** confirmar que el código funciona en tu máquina, sin claves ni internet.

- [ ] Ejecuta `pytest`

**Resultado esperado:** `364 passed` (unos 8 segundos en Linux; en Windows puede tardar de 30 a 40 s por el antivirus). Estas pruebas usan modelos y Redis simulados: no gastan dinero ni tocan servicios.

- [ ] (Recomendado) Mira qué prueba cada archivo, con `pytest -v tests/test_grafo.py` por ejemplo:

| Archivo | Pruebas | Qué demuestra |
|---|---|---|
| `test_clients.py` | 40 | El cliente de texto: errores tipados, reintentos, tiempo máximo y registro en LangSmith |
| `test_config_llm.py` | 8 | La configuración, y que no hay ningún nombre de modelo escrito en el código |
| `test_seed.py` | 32 | Cada caso sembrado de la base simulada está y está bien |
| `test_schemas.py` | 21 | Los contratos rechazan lo inválido |
| `test_tools_ventas_inventario.py` | 9 | Ventas, cobertura, alertas y cantidad de la orden de compra |
| `test_tools_logistica.py` | 34 | La matriz de gestión caso por caso, los envíos y la demanda frente a la capacidad |
| `test_tools_acciones.py` | 28 | Las acciones solo se ejecutan si las reglas las siguen recomendando |
| `test_tools_conocimiento.py` | 3 | La consulta a los manuales |
| `test_resiliencia.py` | 33 | Reintentos ante 503 y 429, modelo de respaldo y listado de modelos |
| `test_rag_chunking.py` | 13 | El corte de los manuales coincide con el catálogo |
| `test_rag_retriever.py` | 18 | Ingesta y búsqueda (vectorial, BM25 e híbrida) |
| `test_rag_evaluacion.py` | 8 | Las métricas del RAG |
| `test_agentes.py` | 20 | Cada agente por separado, incluidas las reglas que impone el código al LLM |
| `test_grafo.py` | 14 | El grafo completo: pausas, ejecución, cierres controlados y orden fijo |
| `test_jobs.py` | 5 | El estado de los trabajos en Redis y el candado por pausa |
| `test_api.py` | 32 | La API por HTTP, de punta a punta |
| `test_observability.py` | 12 | Trazas, y que el `.env` llegue al entorno |
| `test_load_test.py` | 4 | El script de carga |
| `test_verificar_entorno.py` | 10 | El verificador de entorno |
| `test_arranque.py` | 20 | El arranque con un solo comando y la coherencia de la configuración de Docker |

**Si falla alguna:** envíame la salida completa de `pytest`.

---

## Fase 3. Los datos simulados (25 min)

**Objetivo:** crear la tienda ficticia y entender los casos sembrados.

- [ ] Ejecuta `python -m scripts.seed_db --hoy 2026-11-03`

**Resultado esperado:**
```
Base creada en data/tienda.db
  productos: 30
  pedidos: 595
  lineas_pedido: 617
  historial_estados: 3914
  ordenes_compra: 7
  bandeja_avisos: 14
  compensaciones: 2
  reembolsos: 1
  respuestas_cliente: 2
  fecha_referencia: 2026-11-03
```
- [ ] Abre `data/tienda.db` en VS Code (con *SQLite Viewer*) o usa las consultas del Anexo A. **Revisa con tu SQL** si los datos te parecen realistas: es la revisión más valiosa que puedes hacer.
- [ ] Ejecuta las 7 consultas del Anexo A y compara con los resultados que ahí se indican.

**Para reiniciar los datos** después de cualquier prueba que apruebe acciones: vuelve a ejecutar el mismo comando. Si Windows dice que el archivo está en uso, detén la API, reinicia los datos y vuelve a levantarla.

**Los casos sembrados** (lo que el agente debe descubrir):

| Producto o pedido | Qué es | Qué debe hacer el agente |
|---|---|---|
| BAG-001 | Quiebre próximo: 18 en stock, vende 3 por día, cobertura de 6 días | Proponer una orden de 54 unidades |
| BAG-002 | Stock estancado: 40 unidades, última venta hace 21 días | Alertar; **no** reponer |
| BAG-003 vs BAG-004 | Falso ganador: BAG-003 vende 60 pero tiene 140 en stock (0,30 por unidad); BAG-004 vende 12 y las agotó (1,00) | Que BAG-004 no se declare ganador (12 ventas, menos de 4 semanas) |
| BAG-005 | Agotado, con una orden de 25 en camino | **No** proponer otra orden |
| BAG-006 | Agotado, sin orden en camino | Proponer una orden de 8 |
| BAG-007 | 3 en stock y 8 unidades por preparar | Alertar quiebre; proponer 11 |
| PED-C001, PED-C002 | Courier atrasado, con y sin envío pagado | Aviso y compensación (reembolso del envío o cupón de 30 %) |
| PED-C008 | Dirección errónea con 3 mensajes sin respuesta | Cancelar y reembolsar |
| PED-C007, PED-C010 | Dirección o datos con 1 o 2 mensajes | Pedir el dato (mensaje 2 o 3) |
| 18 de octubre en adelante | Pico de demanda: hasta 24 pedidos por día con capacidad de 10 | Verse como cuello de botella |

---

## Fase 4. Configuración y entorno real (25 min)

**Objetivo:** que cada eslabón con servicios externos funcione antes de construir nada encima.

- [ ] Copia el ejemplo: `copy .env.example .env` (Windows) o `cp .env.example .env` (macOS/Linux).
- [ ] Abre `.env` y completa **como mínimo**:
  ```
  LLM_PROVIDER=google
  LLM_MODEL=<el modelo de tu pre-entrega 7>
  EMBEDDING_PROVIDER=google
  EMBEDDING_MODEL=gemini-embedding-001
  GOOGLE_API_KEY=<tu clave>
  LANGSMITH_TRACING=true
  LANGSMITH_API_KEY=<tu clave de LangSmith>
  LANGSMITH_PROJECT=backoffice-agent
  ```
  Deja el resto como viene. **Nunca subas este archivo a un repositorio.**
- [ ] **(Recomendado en el plan gratuito) Elige un modelo de respaldo.** Los modelos populares se saturan y responden `503`; con un respaldo, el sistema pasa a él solo.
  Mira qué puedes usar con tu clave: `python -m scripts.listar_modelos --filtro flash`. Elige uno **distinto** al principal y agrégalo al `.env`:
  ```
  LLM_FALLBACK_MODEL=<otro modelo de la lista>
  ```
  Si el modelo principal te da `503` seguido, prueba con uno más liviano (los «Flash-Lite» suelen tener más holgura).
- [ ] Ejecuta `python -m scripts.verificar_entorno`

**Resultado esperado** (cada línea con ✓, salvo que las trazas aparezcan con un punto si no las configuraste):
```
✓ Manuales y catálogo: 61 fragmentos, coinciden con el catálogo
✓ Configuración (.env): chat: google/<tu modelo> · embeddings: google/gemini-embedding-001
✓ Trazas (LangSmith): activas, proyecto 'backoffice-agent'
✓ Modelo de texto: respondió en ... ms (... tokens de entrada, ... de salida)
✓ Embeddings: dimensión ..., modelo gemini-embedding-001
✓ Chroma: escribe y busca con distancia coseno

Todo listo para la ingesta.
```
Esta verificación hace 2 llamadas a la API de embeddings y 1 al modelo de texto. Con `--sin-chat` omite la del modelo de texto.

**Si falla**, la línea con ✗ dice cuál eslabón. Causas habituales: *Field required* (falta una variable en el `.env`);
*LLMAuthError* (clave inválida); *LLMBadRequestError* con 404 (nombre de modelo equivocado); *LLMRateLimitError* (cuota: espera un minuto y repite).

**Envíame:** la salida completa del verificador.

---

## Fase 5. RAG: ingesta y evaluación (30 min)

**Objetivo:** que los manuales queden indexados y medir qué modo de búsqueda funciona mejor.

- [ ] Ingesta: `python -m scripts.ingest_rag`

**Resultado esperado:** `Colección rag_google_gemini-embedding-001: 61 fragmentos, ... dimensiones (modelo gemini-embedding-001)`.
Es repetible: si falla a la mitad (por cuota), espera y vuelve a ejecutarla.

- [ ] Evaluación sin claves, como referencia: `python -m scripts.eval_rag --solo-bm25` (debe dar acierto@3 de 100 %).
- [ ] Evaluación completa: `python -m scripts.eval_rag --detalle`

**Cómo leerla:** imprime 6 filas (vectorial, BM25 e híbrido, cada uno con y sin filtro de agente) con *acierto@1, @3, @5* (¿el
fragmento correcto aparece entre los primeros 1, 3 o 5?) y *MRR* (1 = siempre primero). Referencia de BM25 sin embeddings:
83 %, 100 %, 100 % y 0,92. Con `--detalle` ves qué preguntas fallan.

- [ ] **Decide y anota** (esto va a tu `.env`):
  - `RAG_MODO`: el modo con mejor acierto@3. Si el híbrido empata o gana, elígelo.
  - El **filtro de agente**: si baja el acierto@5 (en BM25 hace fallar la pregunta Q12 porque un fragmento está etiquetado de otro agente), **no lo uses**.
  - `RAG_TOP_K` es el valor por defecto, pero BI y Logística piden 2 o 3 fragmentos explícitamente, así que `RAG_MODO` es el que más importa.

**Envíame:** la tabla completa de la evaluación.

---

## Fase 6. Redis (10 min)

**Objetivo:** tener el almacén donde viven los trabajos y las pausas.

- [ ] `docker compose up -d redis`
- [ ] `docker compose ps` debe mostrar `redis` como *healthy*.
- [ ] `docker compose exec redis redis-cli ping` debe responder `PONG`.
- [ ] `docker compose exec redis redis-cli MODULE LIST` debe listar los módulos `search` y `ReJSON` (los necesita LangGraph).

**Si falla:** si tienes **otro Redis** ocupando el puerto 6379, la API fallará después con *«unknown command FT.CREATE»*. Detén el otro Redis.

---

## Fase 7. Levantar la API (10 min)

**Objetivo:** el servicio funcionando.

- [ ] `uvicorn app.api.main:app`

**Resultado esperado en el log:** `LangSmith: trazas ACTIVAS -> proyecto 'backoffice-agent'` y `Application startup complete`.
Si no hiciste la ingesta, verás una advertencia de que el RAG no está disponible: la API arranca igual.

- [ ] Abre http://localhost:8000/health. Debe responder `{"status":"ok","rag_disponible":true}`.
- [ ] Abre http://localhost:8000/docs (Swagger). **Úsalo para todas las pruebas de la fase 8**: evita problemas de tildes en PowerShell.

**Si falla:** *«No existe la base simulada»* (ejecuta la fase 3); *«Connection refused»* (Redis no está levantado); *«Address already in use»* (usa `--port 8001`).

---

## Fase 8. Las pruebas funcionales (90–120 min)

**Objetivo:** ver el sistema completo funcionando, con modelos reales.

**El método, para cada prueba:**
1. En Swagger: `POST /tasks`, *Try it out*, pega el cuerpo `{"query": "..."}`, *Execute*. Copia el `job_id`.
2. `GET /tasks/{job_id}`: repite hasta que el estado deje de ser `PENDING` o `RUNNING` (tarda de 10 a 60 segundos).
3. Si queda en `WAITING_APPROVAL`: revisa `approval_request.acciones` y responde en `POST /tasks/{job_id}/approve`.
4. Si queda en `WAITING_CLARIFICATION`: responde en `POST /tasks/{job_id}/clarify` con `{"texto": "..."}`.
5. Compara con lo esperado. **El texto del modelo variará; las cifras, no.**

**Antes de cada prueba que apruebe acciones,** reinicia los datos (fase 3).

| # | Consulta | Debe pasar | Verifica |
|---|---|---|---|
| T1 | `¿Cuántas ventas tuvimos la última semana y cuánto fue el ingreso?` | `DONE` sin pausas, `answer_origin: llm`, `steps: 3` | La respuesta dice **92 unidades** y **2.578.080** pesos |
| T2 | `¿Cuántas ventas tuvimos la última semana y qué cartera deberíamos ordenar pronto según la tendencia de ventas?` | `WAITING_APPROVAL` con **4 propuestas**: posición 0 = BAG-001 (54), 1 = BAG-004 (16), 2 = BAG-006 (8), 3 = BAG-007 (11). **Sin** BAG-005 ni BAG-002 | Aprueba `{"aprobadas": [0]}`. Termina en `DONE`. En SQL: 8 órdenes de compra y la nueva es BAG-001, 54 unidades, costo 594000, llegada 2026-11-13 |
| T3 | `¿Cuántos envíos tenemos en curso y qué carteras van en ellos?` | `WAITING_APPROVAL` (Logística propone **11** acciones: 6 avisos, 3 compensaciones y 2 cancelaciones) | Aprueba `{"aprobadas": []}`. La respuesta menciona **43 pedidos en tránsito**, el desglose por estado (82 sin terminar) y que **12 pedidos quedaron fuera**. La base no cambia |
| T4 | `¿Puedes cruzar nuestra estrategia de escalamiento de ventas con nuestra capacidad de preparación de pedidos?` | Participan BI y Logística | La respuesta cruza ventas y capacidad: **10 por día**, **18 en cola**, **2 días** para vaciarla. Aprueba ninguna |
| T5 | `¿Qué nos dicen nuestras ventas sobre posibles cuellos de botella en la logística?` | Participan BI y Logística | La respuesta cita **8 días sobre la capacidad**, máximo de **24 pedidos** (18 de octubre), espera de **1,9** días en picos contra **0,5** normales y **59 de 511** entregas con atraso. Aprueba ninguna |
| T6 | `¿Tenemos stock para cubrir los pedidos por preparar esta semana?` | Participan Inventario y Logística: **15 propuestas** (4 órdenes y 11 acciones de logística) | Aprueba `{"aprobar_todo": true}`. Las 15 salen `ok: true`. En SQL (Anexo A): 11 órdenes de compra, 20 avisos, 5 compensaciones, 3 reembolsos y 4 pedidos cancelados |
| T7 | `¿Cómo vamos?` | `WAITING_CLARIFICATION` con la pregunta en `clarification_request` | Responde `{"texto": "las ventas de la semana"}`: continúa. Luego prueba una consulta sin sentido (`asdf`) y responde dos veces `no sé`: la tercera vez responde *«No logré entender»* y termina en `DONE` |
| T8 | `¿Qué tiempo hará mañana?` | `DONE` al instante, `answer_origin: plantilla`, texto *«fuera de lo que puedo hacer»* | En LangSmith, solo hay una llamada al modelo |
| T9 | Las pruebas de borde, en Swagger | `422` con cuerpos inválidos (`{"query": "x"}`, vacío); `404` con un `job_id` inexistente; `409` al aprobar un trabajo que no espera aprobación; `422` con `{"aprobadas": [99]}` y con `{"aprobadas": [0,0]}`; `409` al aprobar dos veces la misma pausa | Cada código es el esperado |
| T10 | Persistencia: lanza T2 y, **con el trabajo en `WAITING_APPROVAL`, detén la API** (`Ctrl+C`) y vuelve a levantarla | El estado sigue ahí | Aprueba `{"aprobadas": [0]}` y termina en `DONE`. Es la prueba clave de que el estado vive en Redis |

- [ ] T1  - [ ] T2  - [ ] T3  - [ ] T4  - [ ] T5  - [ ] T6  - [ ] T7  - [ ] T8  - [ ] T9  - [ ] T10

**Prueba de fallo (opcional pero valiosa):** pon una `GOOGLE_API_KEY` incorrecta en el `.env`, reinicia la API y lanza T1.
Debe terminar en `FAILED` en pocos segundos, con `error` que empieza por `orquestador:`, y **no** quedarse en `RUNNING`. Restaura la clave.

**Si la respuesta final sale con `answer_origin: plantilla` en una consulta normal:** significa que el texto del modelo no pasó la
validación de cifras dos veces y el código armó la respuesta. No es un error grave, pero anótalo: dime qué consulta fue.

**Envíame:** el JSON completo de `GET /tasks/{job_id}` de las pruebas que no den lo esperado.

---

## Fase 9. Observabilidad en LangSmith (30 min)

**Objetivo:** ver las trazas, el costo y la latencia.

- [ ] En smith.langchain.com, abre el proyecto `backoffice-agent`. Deben estar las ejecuciones de la fase 8.
- [ ] Hay trazas raíz llamadas **`orquestador`** (una por consulta) y **`orquestador-reanudacion`** (las que vienen de una aprobación o una aclaración).
- [ ] Abre la de T2. Verifica que ves: los nodos `orquestador`, `bi`, `inventario`, `compuerta`; las llamadas al modelo; y **`cliente_de_texto`** (la llamada del Redactor) como hija de un nodo, con sus tokens.
- [ ] Verifica que la columna **Cost** no esté vacía. Si lo está, LangSmith no tiene el precio de tu modelo: agrégalo en la configuración del espacio de trabajo (busca *Model pricing*; el nombre exacto puede variar) y repite una consulta.
- [ ] Cada traza tiene el `job_id` en los metadatos. Comprueba que coincide con el de la API.
- [ ] Ejecuta `python -m scripts.latency_report --minutes 60`: muestra latencia p50 y p95 y costo por ejecución, desde las mismas trazas.

**Si no aparece ninguna traza:** revisa que la línea de arranque de la API diga *trazas ACTIVAS* y que el `.env` tenga `LANGSMITH_TRACING=true` y la clave.

---

## Fase 10. Prueba de carga y capturas (40 min)

**Objetivo:** la evidencia de que aguanta peticiones concurrentes.

- [ ] Reinicia los datos (fase 3). Con la API levantada, **sube de a poco**, porque las cuotas gratuitas de los modelos pueden limitar:
  ```
  python -m scripts.load_test --n 1
  python -m scripts.load_test --n 2
  python -m scripts.load_test --n 5
  ```
- [ ] **Resultado esperado de la de 5:** `Terminaron en DONE: 5/5`, con la ventana de tiempo de la corrida al final. Son 5 consultas de ventas, que no se pausan.
- [ ] Si alguna termina `FAILED` con un error de cuota (`LLMRateLimitError`), espera un minuto y repite con menos; el cliente ya reintenta 3 veces con espera creciente.
- [ ] Ejecuta `python -m scripts.latency_report --minutes 30` y anota p50, p95 y costo promedio.
- [ ] **Guarda estas capturas en la carpeta `screenshots/`:**
  - [ ] `01-pytest-verde.png`: la terminal con `364 passed`
  - [ ] `02-costo-por-ejecucion.png`: el dashboard de LangSmith con el costo por ejecución
  - [ ] `03-latencia-p95.png`: el dashboard con la latencia p95 de la ventana de la prueba de carga
  - [ ] `04-traza-detalle.png`: el árbol de una traza (nodos, modelos y `cliente_de_texto`)
  - [ ] `05-reanudacion-aprobacion.png`: una traza `orquestador-reanudacion` (la de T2)
  - [ ] `06-load-test-terminal.png`: la salida de `load_test --n 5`

---

## Fase 11. Todo con un solo comando (30 min)

**Objetivo:** comprobar que **cualquiera** puede levantar el sistema completo con `docker compose up --build`, sin pasos previos. Es un requisito
del curso («arranque con un comando») y es lo que hará quien revise tu proyecto, así que **pruébalo como si fueras esa persona**.

**Qué hace el comando:** levanta Redis y la API; la API, antes de atender, **crea la base simulada y indexa los manuales en el RAG** (solo lo que falte).
Los datos viven en un volumen de Docker, no en tu carpeta.

- [ ] **Revisa tu `.env`.** Si lo copiaste de una versión anterior del ejemplo, puede tener comentarios al final de una línea
  (`LLM_PROVIDER=google   # google | openai`). Python los entiende, pero **Docker Compose puede dejarlos como parte del valor**. Bórralos: el comentario va en su propia línea.
  Necesitas como mínimo `LLM_PROVIDER`, `LLM_MODEL`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` y tu clave. `RAG_MODO` debe ser `hibrido`.
- [ ] **Detén todo lo que corra por tu cuenta:** la API local (`Ctrl+C` en su terminal) y el Redis de antes (`docker compose down`).
  Así probarás el recorrido completo, desde cero: `docker compose down -v` (borra también los datos del volumen).
- [ ] Ejecuta: `docker compose up --build`
- [ ] **Resultado esperado en los registros del servicio `api`** (la primera vez tarda 1 o 2 minutos):
  ```
  [1/2] Creando la base simulada en /code/storage/tienda.db (fecha simulada: 2026-11-03)...
        listo: 30 productos y 595 pedidos
  [2/2] Indexando los manuales en el RAG (61 fragmentos; la primera vez tarda un poco)...
        listo: colección rag_google_gemini-embedding-001, 61 fragmentos
  Todo listo en ... s.
  Levantando la API en http://0.0.0.0:8000  (documentación en /docs)
  INFO:     LangSmith: trazas ACTIVAS -> proyecto 'backoffice-agent'
  INFO:     Application startup complete.
  ```
- [ ] En **otra terminal**: `docker compose ps`. La API debe pasar a **healthy** (puede aparecer antes como *starting*: el período de gracia es de 3 minutos).
- [ ] Abre http://localhost:8000/health: debe decir `{"status":"ok","rag_disponible":true}`. Luego repite la prueba **T1** en http://localhost:8000/docs.
- [ ] **Segundo arranque (idempotencia):** `Ctrl+C`, luego `docker compose up`. Ahora debes ver `Base simulada: ya existe` y `RAG: ya está indexado`, y debe arrancar en segundos.
- [ ] **El ensayo definitivo, como el revisor:** copia la carpeta del proyecto a otro lugar **sin** `.venv`, sin `data/tienda.db` y sin `data/chroma` (solo el código y tu `.env`),
  ejecuta `docker compose down -v` y luego `docker compose up --build`. Debe funcionar igual.

**Notas:**
- Las pruebas automáticas vigilan la configuración de Docker (rutas, volumen, dependencia de Redis, healthcheck), pero **no pueden ejecutar Docker**: esta fase es la verificación real.
- Los registros de un servicio se ven con `docker compose logs api` (con `-f` para seguirlos en vivo).
- Sin Docker, el equivalente es `docker compose up -d redis` y luego `python -m scripts.arrancar`.

**Si falla**, la última línea de los registros de `api` suele decir qué pasó:

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `env file ... .env not found` | No existe el `.env` | `copy .env.example .env` y complétalo |
| `Falta configuración en el .env o en el entorno: LLM_MODEL` | Variable obligatoria vacía | Complétala (o quita el comentario pegado al valor) |
| `No se pudo preparar el sistema: ... LLMAuthError` o `404` | Clave o nombre de modelo equivocado | Corrige el `.env` y `docker compose up --build` |
| `...el proveedor no respondió (ServerError); reintento en 5 s` | Saturación pasajera al indexar | Es normal: reintenta solo (3 intentos) |
| `No se pudo preparar el sistema: ... ServerError` tras los reintentos | El proveedor sigue saturado | Espera unos minutos y repite `docker compose up` (es idempotente) |
| `api` queda *unhealthy* | La preparación sigue en curso, o falló | `docker compose logs api` |
| `port is already allocated` (8000 o 6379) | Algo local usa ese puerto | Detén la API local o el Redis anterior (`docker compose down`) |
| Quieres rehacer los datos | | `docker compose down -v` y `docker compose up --build` |

---

## Fase 12. Entrega (30 min)

- [ ] **Clone limpio:** copia la carpeta a otro lugar *sin* `.venv` ni `.env`, crea un entorno nuevo, ejecuta `pip install -r requirements-dev.txt` y `pytest`. Debe dar `364 passed`.
- [ ] **Y el ensayo del arranque con un comando** de la fase 11 (`docker compose down -v` y `docker compose up --build`), también desde esa copia limpia.
- [ ] Revisa `git status`: el `.env` **no** debe aparecer (`git check-ignore .env` debe devolver `.env`).
- [ ] Revisa que `screenshots/` tenga las 6 capturas y que el README se lea bien.
- [ ] Sube el repositorio y abre el enlace en una ventana de incógnito para comprobar que es accesible.

---

## Anexo A. La verdad de los datos

Con `--hoy 2026-11-03` y la semilla por defecto. Compara lo que responda el modelo con estas cifras.

| Dato | Valor |
|---|---|
| Ventas de la última semana (27 oct al 2 nov) | **92 unidades** y **$2.578.080** |
| Alertas de inventario | BAG-001 (cobertura 6,0), BAG-002 (estancado), BAG-004, BAG-005 y BAG-006 (agotados), BAG-007 (cobertura 5,2) |
| Órdenes sugeridas | BAG-001: 54 · BAG-004: 16 · BAG-006: 8 · BAG-007: 11 · BAG-005 y BAG-002: ninguna |
| Pedidos con problema | 22 en total: capacidad 11, falta de stock 4, dirección 3, courier 2, sin datos 2 |
| Logística gestiona | Los 10 más urgentes; los otros 12 se informan como omitidos |
| Pedidos sin terminar | 82: en ruta 29, ingresado 24, próximo a llegar 14, en preparación 12, entrega fallida 3 |
| En tránsito | 43 (29 en ruta y 14 próximo a llegar) |
| Cola de preparación | 18 pedidos, capacidad 10 por día, 2 días para vaciarla |
| Demanda frente a capacidad | 8 días sobre la capacidad, máximo 24 pedidos en un día, espera de 1,9 días en picos y 0,5 normales, 59 de 511 entregas con atraso |
| Tras aprobar todo en T6 | 11 órdenes de compra, 20 avisos, 5 compensaciones, 3 reembolsos, 4 pedidos cancelados |

Nota: BAG-007 da cobertura 5,2 en la herramienta y 5,3 en la consulta 2 de abajo, porque la consulta redondea las ventas diarias antes de dividir. Es solo redondeo.

**Consultas SQL de verificación** (en VS Code con *SQLite Viewer*, o con `python -c` sobre `data/tienda.db`):

```sql
-- 1. Ventas de la última semana: debe dar 92 y 2578080
SELECT SUM(l.cantidad) AS unidades, SUM(l.cantidad*l.precio_unitario) AS ingresos
FROM lineas_pedido l JOIN pedidos p ON p.pedido_id=l.pedido_id
WHERE p.estado<>'cancelado' AND p.fecha_ingreso>='2026-10-27 00:00:00' AND p.fecha_ingreso<'2026-11-03 00:00:00';

-- 2. Cobertura (stock / ventas diarias de 14 días): BAG-001 = 6.0
SELECT i.sku, i.stock_actual,
  ROUND(COALESCE((SELECT SUM(cantidad) FROM ventas v WHERE v.sku=i.sku AND v.fecha>='2026-10-20 00:00:00' AND v.fecha<'2026-11-03 00:00:00'),0)/14.0,2) AS ventas_diarias,
  ROUND(i.stock_actual/NULLIF(COALESCE((SELECT SUM(cantidad) FROM ventas v WHERE v.sku=i.sku AND v.fecha>='2026-10-20 00:00:00' AND v.fecha<'2026-11-03 00:00:00'),0)/14.0,0),1) AS cobertura_dias
FROM inventario i WHERE i.sku IN ('BAG-001','BAG-007','BAG-004') ORDER BY i.sku;

-- 3. Pedidos con problema por causa
SELECT causa_atraso, COUNT(*) AS pedidos FROM pedidos
WHERE estado NOT IN ('entregado','cancelado') AND causa_atraso IS NOT NULL GROUP BY causa_atraso ORDER BY 2 DESC;

-- 4. Pedidos sin terminar por estado
SELECT estado, COUNT(*) AS pedidos FROM pedidos WHERE estado NOT IN ('entregado','cancelado') GROUP BY estado ORDER BY 2 DESC;

-- 5. Órdenes de compra (antes: 7 filas)
SELECT orden_id, sku, cantidad, costo_total, estado, fecha_entrega_esperada FROM ordenes_compra ORDER BY orden_id;

-- 6. Conteos para comparar antes y después de aprobar
SELECT (SELECT COUNT(*) FROM bandeja_avisos) AS avisos, (SELECT COUNT(*) FROM compensaciones) AS compensaciones,
       (SELECT COUNT(*) FROM reembolsos) AS reembolsos, (SELECT COUNT(*) FROM pedidos WHERE estado='cancelado') AS cancelados;
-- antes: 14, 2, 1, 2 · después de T6: 20, 5, 3, 4

-- 7. Los pedidos de los casos
SELECT p.pedido_id, p.estado, p.causa_atraso, (SELECT COUNT(*) FROM bandeja_avisos a WHERE a.pedido_id=p.pedido_id) AS avisos
FROM pedidos p WHERE p.pedido_id IN ('PED-C001','PED-C002','PED-C008');
```

---

## Anexo B. Si algo falla

| Síntoma | Causa probable | Qué hacer |
|---|---|---|
| `No module named 'app'` | No estás en la raíz del proyecto, o falta el entorno | `cd` a la carpeta con `README.md`, activa `.venv` y usa `python -m scripts.xxx` (no `python scripts/xxx.py`) |
| `ValidationError ... llm_model` | `LLM_MODEL` vacío en el `.env` | Complétalo con el nombre exacto de tu modelo |
| `LLMAuthError` | Clave inválida | Revisa `GOOGLE_API_KEY` |
| `LLMBadRequestError` con 404 | Nombre de modelo equivocado | Corrige `LLM_MODEL` o `EMBEDDING_MODEL` |
| `LLMRateLimitError` | Cuota del plan gratuito | Espera un minuto; reduce la concurrencia |
| `503 UNAVAILABLE ... high demand` | El modelo está saturado en ese momento (no es tu configuración ni tu cuota) | El sistema ya reintenta 3 veces con espera creciente. Si persiste: define `LLM_FALLBACK_MODEL` o cambia `LLM_MODEL` por uno más liviano, reinicia la API y repite |
| `No existe la colección` | Falta la ingesta | `python -m scripts.ingest_rag` |
| `La consulta tiene N dimensiones y la colección M` | Cambiaste el modelo de embeddings | Repite la ingesta |
| `No existe la base simulada` | Falta la fase 3 | `python -m scripts.seed_db --hoy 2026-11-03` |
| `Connection refused` al arrancar | Redis no está levantado | `docker compose up -d redis` |
| `unknown command FT.CREATE` | Otro Redis (sin módulos) ocupa el puerto 6379 | Detén ese Redis; debe ser el de `docker compose` |
| La API no arranca por el puerto | El 8000 está ocupado | `uvicorn app.api.main:app --port 8001` |
| Un trabajo se queda en `RUNNING` | La ejecución es lenta, o falló algo | Mira el terminal de `uvicorn`; a los 300 s pasa a `FAILED` solo |
| Tildes rotas al enviar con PowerShell | Codificación de Windows PowerShell 5 | Usa Swagger (`/docs`) |
| No hay trazas en LangSmith | Variables sin definir | Mira la línea *LangSmith* del arranque de la API y ejecuta el verificador |
| Costo en blanco en LangSmith | No tiene el precio de tu modelo | Agrégalo en su tabla de precios |
| `answer_origin: plantilla` en una consulta normal | El texto del modelo no pasó la validación de cifras | Anota la consulta y envíamela |

---

## Anexo C. Qué evidencia respalda cada punto de la rúbrica

| Criterio | Peso | Qué lo demuestra |
|---|---|---|
| API asíncrona | 30 % | T1 a T9 (ticket y consulta, `202` inmediato), prueba de carga 5/5, Swagger |
| Redis | 25 % | T10 (el estado sobrevive al reinicio), el candado por pausa (T9), `MODULE LIST` de la fase 6 |
| Observabilidad | 25 % | Capturas 02 a 05: costo, p95, traza detallada y reanudación |
| Aprobación humana | 15 % | T2, T6 y T10 |
| README | 5 % | Fase 12 |

---

## Anexo D. Preguntas de comprensión (para ti, sin mirar el código)

Contéstalas con tus palabras y mándamelas: es como evaluamos si dominas la arquitectura.

1. ¿Por qué `LLM_MODEL` no tiene valor por defecto? ¿Y qué hace `rag_collection_name`?
2. ¿Por qué el LLM nunca calcula una fecha? ¿Quién la calcula y cómo llega al aviso?
3. ¿Qué pasa si aprobaste una compensación y, antes de ejecutarse, otra persona ya la emitió? ¿Dónde se impide el duplicado?
4. ¿Por qué hay un candado **por pausa** y no uno por trabajo?
5. ¿Qué diferencia hay entre `WAITING_CLARIFICATION` y `WAITING_APPROVAL`, y qué tienen en común en el grafo?
6. ¿Por qué el `.env` no basta para activar LangSmith y qué hace `init_observability` al respecto?
7. En T5 el agente dice que 59 de 511 entregas llegaron tarde. ¿Cómo comprobarías esa cifra con SQL?
8. ¿Qué parte de la respuesta final puede inventar el LLM y qué parte no? ¿Cómo se garantiza?

---

## Anexo E. Qué todavía no está verificado

Todo el código se probó con modelos y Redis simulados. Esta guía es justamente la primera vez que corre contra los servicios reales. Lo que puede aparecer:
el comportamiento de los prompts con tu modelo, el arranque real con `redis-stack`, la dimensión y las cuotas de los embeddings, y que LangSmith
conozca el precio de tu modelo. Para cada hallazgo: envíame la salida y lo corrijo en la misma pasada.

---

## Registro de seguimiento

| Fase | Fecha y hora | Resultado | Notas |
|---|---|---|---|
| 0 | | | |
| 1 | | | |
| 2 | | | |
| 3 | | | |
| 4 | | | |
| 5 | | | |
| 6 | | | |
| 7 | | | |
| 8 | | | |
| 9 | | | |
| 10 | | | |
| 11 | | | |
| 12 | | | |
