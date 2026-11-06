# Registro de decisiones de arquitectura

Cada decisión lleva su motivo. Lo marcado como **propuesta** espera validación.

## Decididas

| Decisión | Motivo |
| --- | --- |
| Base vectorial local: Chroma, con distancia coseno fijada al crear la colección | Reproducible con un solo comando y sin claves externas. Chroma usa L2 por defecto |
| Base de datos simulada: SQLite | Simple y suficiente para datos simulados |
| El nombre de la colección de Chroma incluye proveedor y modelo de embeddings | Los vectores de modelos distintos no se pueden mezclar |
| **Opción C: dos clientes, repartidos por responsabilidad.** `app/clients` genera texto y hace streaming (por ejemplo, el Redactor); `app/llm.py` (LangChain) hace lo que necesita salida estructurada, herramientas o embeddings | La rúbrica pide integrar el cliente del Módulo 1 y las cadenas del Módulo 2. Se usa cada uno donde aporta, sin reconstruir lo que LangChain ya resuelve |
| Los reintentos viven en un solo lugar (`app/clients/base.py`) y se desactivan los de los SDK de OpenAI y Anthropic | Si no, los intentos se multiplican (3 del cliente por 3 del SDK) |
| Solo se reintentan los errores que se declaran reintentables (cuota, tiempo agotado, conexión, servidor) | Reintentar con una clave inválida solo gasta tiempo |
| El streaming reintenta solo antes del primer fragmento | Después, repetir la llamada duplicaría el texto que el usuario ya vio |
| Ningún nombre de modelo en el código, y `ModelConfig.model_name` es obligatorio | Lo exige la rúbrica. Una prueba automática lo vigila |
| Reloj simulado: la fecha «hoy» es un dato (`parametros.fecha_referencia`) | Resultados reproducibles; las herramientas no usan `date.today()` |
| Las ventas son una vista (no una tabla) y excluyen los pedidos cancelados | Una sola fuente de verdad |
| Las herramientas de consulta usan una conexión de solo lectura | Aunque un LLM generara un INSERT o un DELETE, la base lo rechaza |
| Los parámetros de negocio viven en la tabla `parametros` | Se pueden cambiar sin tocar el código |
| `causa_atraso` registra la causa del problema detectado, aunque la fecha aún no haya vencido | Permite actuar antes del atraso, como en la falta de stock |

## Decididas en el paso 4.3 (RAG)

| Decisión | Motivo |
| --- | --- |
| Cortador propio por títulos, no el de LangChain | Hacen falta ids exactos y la regla de las preguntas frecuentes. En una prueba, el cortador genérico no separaba las preguntas |
| El catálogo (`data/eval/rag_catalogo_fragmentos.csv`) es la fuente de verdad de los ids y de los metadatos de agente y causa | La ingesta falla con un mensaje claro si un manual y el catálogo no coinciden, así no se ingesta nada a medias |
| El título (la ruta «Documento > Sección > Subsección») viaja dentro del texto que se vectoriza | Da contexto al fragmento. Se compara contra no ponerlo en la evaluación de la fase 4 |
| Los embeddings se pasan siempre explícitos y la colección se crea sin función de embeddings propia | Si no, Chroma usaría en silencio su modelo por defecto y mezclaría modelos |
| Las llamadas a Chroma (síncronas) se ejecutan en un hilo (`asyncio.to_thread`) | No bloquear el event loop, como pide la rúbrica |
| BM25 en memoria sobre los fragmentos, con minúsculas, sin tildes, sin palabras vacías y con plural simplificado | Con decenas de fragmentos es instantáneo y no necesita otra base |
| La búsqueda híbrida combina los dos órdenes con RRF (constante 60) | No obliga a comparar puntajes de escalas distintas |
| El tipo de tarea de los embeddings de Gemini no se configura | LangChain usa documento para la ingesta y consulta para la búsqueda (verificado en el código) |

## Decididas en el paso 4.4 (herramientas y contratos)

| Decisión | Motivo |
| --- | --- |
| Todos los contratos viven en `app/schemas.py`, con `extra="forbid"`, vocabularios cerrados y validadores entre campos | Un campo inventado por un LLM se rechaza en la frontera donde ocurre |
| **La matriz de gestión es código** (`tools/orders.py: decidir_gestion`), no un prompt | Son casos claros y deben ser deterministas y probables. El LLM solo redacta con lo que la matriz decide |
| Las fechas las calcula el código y viajan ya calculadas en los contratos | El LLM nunca calcula una fecha |
| Las acciones proponen, no ejecutan: todas pasan por la aprobación | Es la compuerta única decidida |
| Antes de ejecutar una acción aprobada se vuelve a comprobar que las reglas aún la recomiendan | Defensa en profundidad: una acción vieja, repetida o inventada se rechaza aunque alguien la apruebe |
| Cada acción es atómica y repetible sin duplicar efectos | Una orden o una compensación no se crea dos veces |
| Un pedido con causa registrada cuenta como «con problema» aunque aún no venza | Permite avisar antes del atraso, por ejemplo en la falta de stock |
| La tendencia de ventas compara la mitad reciente de la serie con la mitad anterior (±20 %) | Comparar solo las dos últimas semanas no detectaba un salto que ocurrió antes y se mantiene (lo encontró la revisión de los resultados) |
| Alerta «agotado» (stock cero) distinta de «quiebre próximo»; «bajo punto de reorden» es estrictamente menor | Evita falsas alarmas por igualdad |
| Si ya se envió un aviso de atraso, no se repite | El cliente no debe recibir el mismo mensaje dos veces |
| Un pedido sin causa registrada va a revisión humana y no se inventa una causa | Regla del manual logístico |

## Propuestas por validar (paso 4.4)

| Propuesta | Motivo |
| --- | --- |
| Cantidad de la orden = ventas diarias × (lead time + cobertura objetivo) − stock − en camino + demanda no atendida, con cobertura objetivo de 14 días (parámetro `cobertura_objetivo_dias`) | Falta una regla de cantidad; la cobertura objetivo es un parámetro de negocio nuevo |
| Pedido atrasado por capacidad que ya salió de la cola: nueva fecha = hoy + 2 + plazo (en preparación), hoy + 1 + plazo (en espera de despacho) u hoy + plazo (en ruta) | Consistente con cómo se promete la fecha al ingresar el pedido |

## Decididas en el paso 4.5 (agentes, state y grafo)

| Decisión | Motivo |
| --- | --- |
| El state guarda diccionarios simples y cada nodo valida con los contratos al leer y escribir | El checkpointer los serializa sin depender de clases propias; los contratos siguen siendo la única definición de la forma |
| Las salidas estructurales del LLM se piden como JSON y se validan con los contratos, con un reintento que le devuelve el error | Los contratos estrictos (`extra="forbid"`) no los aceptan todos los proveedores en su modo nativo; así se comporta igual con cualquiera |
| BI es un bucle acotado (máximo 4 llamadas a herramientas) y sus cifras las agrega el código | Es el único especialista con ruta abierta, pero el LLM no calcula ni inventa números |
| Las reglas del negocio las impone el código aunque el prompt las pida | Un producto con menos de 15 ventas o de 4 semanas de historial no queda como «incluir en el drop» aunque el LLM lo proponga |
| Inventario es código puro, sin LLM | No hay nada que juzgar: son reglas |
| Logística: la matriz decide; el LLM solo redacta los avisos, con marcadores que rellena el código | El modelo no puede inventar una fecha, una cifra ni una compensación |
| Un aviso con dígitos propios, marcadores desconocidos o sin lo obligatorio se rechaza; tras un reintento, lo arma el código con una plantilla | Nunca queda un pedido sin aviso ni con datos no calculados |
| El Redactor valida que cada cifra, fecha e identificador exista en los hechos; si no, reintenta y, si no, responde la plantilla | El usuario nunca recibe datos que el sistema no calculó |
| Las consultas fuera de alcance o no entendidas se responden con texto fijo | No gastan una llamada al LLM |
| Las aclaraciones y la aprobación usan la misma pausa del grafo (`interrupt`), con máximo 2 aclaraciones | Un solo mecanismo para esperar al humano |
| Cualquier error en un nodo lleva a un cierre controlado: el error queda a la vista y el usuario recibe un mensaje honesto | Nada queda colgado ni falla en silencio |
| Logística gestiona como máximo 10 pedidos por consulta (los más atrasados primero) y avisa cuántos quedaron fuera | Acota el costo y el tamaño de la aprobación, sin esconder lo omitido |
| Los avisos se redactan ANTES de la aprobación | El usuario aprueba el texto exacto que se va a enviar |

## Decididas en la capa de API (reciclando la pre-entrega 7)

| Decisión | Motivo |
| --- | --- |
| Se recicla el patrón aprobado: ticket y consulta, estado de los trabajos en Redis, worker en segundo plano y trazas | Ya estaba validado; solo cambia lo que hay detrás |
| El grafo tiene DOS tipos de pausa (aclaración y aprobación) y un trabajo puede pausarse hasta tres veces | Una consulta ambigua pregunta antes de proponer acciones |
| El candado que evita reanudar dos veces es POR PAUSA, no por trabajo | Con un solo candado por trabajo, la segunda pausa quedaba bloqueada |
| La aprobación es por posición (`aprobadas: [0, 2]`), con atajo `aprobar_todo` | El usuario puede aprobar solo una parte del lote |
| La decisión de aprobación se valida en el borde de la API (posiciones repetidas, negativas o inexistentes dan 422) | Lo encontró una prueba: las posiciones repetidas llegaban al contrato interno y daban un error 500 |
| Las reanudaciones son una traza raíz aparte | No mezclar sus costos y latencias con las ejecuciones normales al calcular el p95 |
| El cliente de texto se registra en LangSmith como una ejecución de tipo LLM, con proveedor, modelo y tokens | No pasa por LangChain: sin esto, su costo no aparecía y el costo por ejecución salía incompleto |
| Si todavía no se hizo la ingesta de los manuales, la API arranca igual y lo avisa | El sistema funciona sin los manuales; la ingesta no debe impedir probar lo demás |
| Si falta la base simulada o el `.env` está incompleto, la API no arranca y explica qué hacer | Fallar pronto y con claridad |
| La prueba de carga usa 5 consultas de ventas | No proponen acciones, así que no se pausan y no distorsionan el costo y la latencia |

## Decididas al cerrar los huecos antes de la verificación real

| Decisión | Motivo |
| --- | --- |
| Dos herramientas nuevas en Logística: `envios_en_curso` y `demanda_vs_capacidad` | Dos de las cinco preguntas cruzadas del proyecto no se podían responder con evidencia: la 2 (envíos en curso y qué carteras van en ellos) y la 4 (cuellos de botella logísticos). Se detectó al preparar el plan de pruebas |
| «Envío en tránsito» = pedido en espera de despacho, en ruta o próximo a llegar | Ya salió de la bodega o está a punto de salir |
| «Día pico» = día con más pedidos que la capacidad de preparación | Es la definición operativa del cuello de botella |
| Las pruebas nunca leen el `.env` real de quien las ejecuta (fixture automático en `conftest.py`) | Con un `.env` real, una prueba que borraba una variable del entorno seguía viéndola y fallaba. Lo detectó la primera ejecución en la máquina del usuario |
| El verificador ignora los errores al borrar su carpeta temporal | En Windows, Chroma mantiene abierto su archivo y el borrado falla con `PermissionError` aunque todo funcione: se informaba como una falla de Chroma |
| `init_observability` carga el `.env` en el entorno del proceso (sin pisar lo ya definido) y el verificador lo comprueba | LangSmith lee `LANGSMITH_*` del entorno y pydantic-settings no vuelca el `.env` al entorno: sin esto, las trazas quedaban apagadas en silencio. Lo detectó la preparación del plan de pruebas |
| A igual atraso, Logística prioriza: courier, falta de stock, dirección, datos y, al final, capacidad | Sin esto, los 11 pedidos de capacidad desplazaban a los demás del tope de 10 y la consulta mostraba solo 3 de las 5 causas |

## Decididas tras el primer error real con un modelo (503 por alta demanda)

| Decisión | Motivo |
| --- | --- |
| El modelo de chat y el cliente de texto reintentan con espera creciente ante 503 y 429 (por defecto 2, 4 y 8 s) | El primer error real fue un `503 UNAVAILABLE`: el modelo estaba saturado. Es un error pasajero y un solo fallo no debe tumbar la consulta |
| Modelo de respaldo opcional (`LLM_FALLBACK_MODEL`) del mismo proveedor, usado solo tras agotar los reintentos | Cualquier modelo puede saturarse; cambiar de modelo no es una garantía |
| Los errores definitivos (clave inválida, modelo inexistente, petición mal formada) no se reintentan ni activan el respaldo | Con otro modelo fallarían igual y solo se gastaría tiempo |
| La resiliencia es un envoltorio (`ChatResiliente`, `ClienteConRespaldo`), sin tocar los agentes | Los agentes siguen llamando `ainvoke` y `generate` como antes |
| El verificador de entorno prueba también el modelo de respaldo | Un respaldo mal escrito solo fallaría justo cuando se lo necesita |
| `python -m scripts.listar_modelos` lista los modelos que la clave puede usar | Elegir el modelo con datos de la propia cuenta, no de un artículo |

## Decididas al cerrar el arranque con un solo comando

| Decisión | Motivo |
| --- | --- |
| `docker compose up --build` levanta Redis y la API, y la API **prepara lo que falte** (base simulada y RAG) antes de atender | Requisito del curso («arranque con un comando»): quien revise el proyecto no debe seguir pasos previos |
| La preparación es **idempotente** y se **repara sola**: una base a medias o un RAG con fragmentos de menos cuentan como no listos | Un arranque interrumpido no debe dejar el sistema roto ni obligar a borrar nada a mano |
| Si falta un solo manual o el catálogo no coincide, el arranque se detiene con un mensaje claro | No indexar nada a medias |
| La ingesta reintenta con espera (5 y 15 s) ante errores pasajeros del proveedor | La primera preparación depende de la API de embeddings, a menudo con cuota limitada |
| La base y Chroma viven en un **volumen de Docker**; los manuales viajan en la imagen | Sin problemas de permisos ni de bloqueo de archivos en Windows, y la carpeta del usuario queda limpia. Los datos generados nunca entran a la imagen |
| La fecha simulada es una variable (`FECHA_SIMULADA`, por defecto 2026-11-03) | Las cifras de la documentación son reproducibles desde cualquier clone |
| `.env.example` sin comentarios al final de una línea | El `env_file` de Docker Compose (en versiones antiguas) los deja como parte del valor |
| El modo del RAG por defecto es `hibrido` | Fue el de mejor desempeño en la evaluación con embeddings reales |
| La configuración de Docker se vigila con pruebas estáticas | No se puede ejecutar Docker en el entorno de pruebas: así un cambio que rompa el arranque se detecta igual |

## Hallazgos de la primera medición

Solo con BM25, sin embeddings ni claves (`python -m scripts.eval_rag --solo-bm25`), sobre las 42 preguntas con respuesta:

| Configuración | Acierto@1 | Acierto@3 | Acierto@5 | MRR |
| --- | --- | --- | --- | --- |
| BM25 | 83 % | 100 % | 100 % | 0,92 |
| BM25 con filtro de agente | 93 % | 98 % | 98 % | 0,95 |

- **La base es optimista.** Las preguntas las escribió quien escribió los manuales, así que el vocabulario coincide más de lo normal. Las preguntas propias del usuario serían una prueba más exigente.
- **El filtro duro por agente sube la precisión del primer resultado, pero excluye conocimiento que servía.** La Q12 la hace el Redactor, y su respuesta está etiquetada como de logística. Se decide en la fase 4: filtro blando, o fragmentos con varios agentes (esta versión de Chroma admite listas en metadatos).
- **Un umbral de puntaje no basta para decir «no hay información».** Los puntajes de BM25 de las preguntas sin respuesta llegan a 8,4 y los de las preguntas con respuesta bajan a 3,4. Hace falta otra verificación, por ejemplo que el agente compruebe que el fragmento realmente responde.
- **Falta medir los modos vectorial e híbrido:** necesitan la clave de Google (`python -m scripts.eval_rag`).

## Propuestas por validar

| Propuesta | Motivo |
| --- | --- |
| Columna nueva `productos.tamano` (mini, mediano, grande) | La guía de marca habla de variedad de tamaños, y el modelo de datos no tenía dónde registrarlo |
| Ventas por unidad de stock = ventas del período ÷ (ventas + stock actual) | No existe un stock inicial en el modelo. Es una aproximación que corrige el sesgo de disponibilidad |
| Días de cobertura = stock ÷ ventas diarias de las últimas 2 semanas (de hoy-14 a ayer) | Es la señal de «ordenar pronto» sin usar el punto de reorden (pregunta 1) |
