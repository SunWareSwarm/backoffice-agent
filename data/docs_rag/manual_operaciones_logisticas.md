# Manual de operaciones logísticas

Documento simulado para el RAG del agente de back office de una tienda online de carteras. Ámbito: logística de pedidos. Versión 1.

## 1. Propósito y alcance

Este manual explica qué hace la tienda cuando un pedido se atrasa: cómo se detecta, a qué causa se asocia, qué se hace y qué se le dice al cliente. Lo usan el agente de logística y el redactor de respuestas.

Las cifras (plazos, fechas y cantidades) las calcula el sistema con reglas fijas. Este manual describe esas reglas y el tono de los avisos, pero nunca se usa para estimar una fecha a mano.

## 2. Parámetros operativos

La operación se rige por siete parámetros fijos. Todo cálculo de atraso, fecha o compensación parte de esta tabla.

| Parámetro | Valor | Qué significa |
| --- | --- | --- |
| Corte de despacho | 15:00 | Un pedido ingresado antes de las 15:00 puede prepararse ese mismo día; después de esa hora cuenta como del día siguiente. |
| Capacidad de preparación | 10 pedidos por día | Máximo de pedidos que la tienda alcanza a preparar en un día. |
| Plazo de entrega al cliente | Máximo 2 días en la Región Metropolitana (RM) y 5 días en regiones | Tiempo del courier desde el despacho hasta la entrega. |
| Lead time del proveedor | 10 días | Tiempo desde que se compra a un proveedor hasta que el producto llega a la tienda. Es igual para todos los proveedores. |
| Compensación por atraso | Desde 2 días de atraso | Ver la sección 6. |
| Espera por falta de stock | 3 días | Ver la causa 2 de la sección 5. |
| Mensajes para pedir datos | Máximo 3 | Después del tercer mensaje sin respuesta, el pedido se cancela y se reembolsa. |

## 3. Estados de un pedido

Un pedido pasa por nueve estados posibles. "Entrega con atraso" no es un estado: es una condición que se calcula (sección 4).

| Estado | Qué significa |
| --- | --- |
| Solicitado | El cliente envió el pedido y falta validarlo. |
| Ingresado | El pedido fue validado y registrado, y espera su preparación. |
| En preparación | La tienda está armando el pedido. |
| En espera de despacho | El pedido está listo y espera que el courier lo retire. |
| En ruta | El courier ya lo retiró y va camino al cliente. |
| Próximo a llegar | El courier está por entregarlo. |
| Entregado | El cliente recibió el pedido. Es un estado final. |
| Entrega fallida | El courier intentó entregar y no pudo. |
| Cancelado | El pedido se canceló y, si corresponde, se reembolsó. Es un estado final. |

## 4. Cuándo un pedido está atrasado

Un pedido está atrasado cuando no está entregado ni cancelado y la fecha de hoy supera su fecha prometida. Los días de atraso son la diferencia entre hoy y esa fecha.

Toda transacción queda registrada con una fecha prometida. Si faltara, no es un caso de negocio sino un error de datos, y se informa como tal.

Un pedido en "entrega fallida" también puede estar atrasado. El atraso es una condición que se calcula cada vez que se revisan los pedidos, no un estado que alguien marque a mano.

## 5. Causas de atraso y qué hace la tienda

Cada pedido atrasado tiene registrada una de cinco causas. Si no tiene causa registrada, no se inventa una: se informa como "causa no determinada" y se revisa (sección 9).

### Causa 1: el courier no cumple la entrega

Se detecta cuando el pedido está en ruta o próximo a llegar y su fecha prometida ya venció. La tienda reclama al courier, avisa al cliente y le informa una nueva fecha de entrega. Si el atraso llega a 2 días o más, compensa según la sección 6.

### Causa 2: falta de stock

Se detecta cuando, al ingresar el pedido, el producto no tiene stock suficiente. Un pedido sin stock termina en reembolso, salvo que haya una orden de compra en camino para ese producto.

- Durante los primeros 3 días de atraso, la tienda le informa al cliente desde qué fecha habrá stock. El cliente decide si le sirve esperar.
- Pasados esos 3 días, la tienda le ofrece el reembolso.
- Sin una orden de compra en camino, no hay nueva fecha y se ofrece el reembolso directamente.
- El pedido queda registrado como demanda no atendida, para considerarlo en la próxima orden de compra.

### Causa 3: dirección errónea

Se detecta cuando el courier no pudo entregar porque la dirección no era válida. La tienda le pide al cliente que corrija la dirección, con un máximo de 3 mensajes automáticos. Cuando el cliente responde, se reprograma la entrega. Si no responde después del tercer mensaje, el pedido se cancela y se reembolsa.

### Causa 4: el cliente no entrega datos de entrega

Se detecta cuando el pedido está ingresado pero le faltan datos para despacharlo. El pedido queda en pausa y se le piden los datos al cliente, con las mismas reglas de la causa 3: máximo 3 mensajes y, sin respuesta, cancelación con reembolso.

### Causa 5: capacidad de preparación

Se detecta cuando hay más pedidos en cola que la capacidad diaria de 10 pedidos. La nueva fecha sale de la cola acumulada y de esa capacidad. Si el atraso llega a 2 días o más, compensa según la sección 6.

## 6. Compensaciones por atraso

Desde 2 días de atraso, la tienda compensa al cliente. Si el cliente pagó el envío, se le reembolsa el costo del envío. Si no lo pagó, recibe un cupón de 30 % de descuento en su siguiente compra.

La compensación aplica cuando el atraso se debe a la tienda o al courier: causa 1 (courier), causa 2 (falta de stock, si el cliente decide esperar) y causa 5 (capacidad). No aplica a las causas 3 y 4, porque el atraso depende de los datos del cliente.

Toda compensación es una acción que el usuario aprueba antes de ejecutarse.

## 7. Cómo se calcula la nueva fecha

La nueva fecha la calcula siempre el sistema, nunca el redactor. El aviso solo puede incluir una fecha que el sistema haya calculado.

| Causa | Cómo sale la nueva fecha |
| --- | --- |
| 1. Courier | Hoy más el plazo de entrega (2 días en RM, 5 en regiones). |
| 2. Falta de stock, con orden de compra en camino | Fecha de llegada de la orden, más el despacho (según el corte de las 15:00) y el plazo de entrega. |
| 2. Falta de stock, sin orden de compra | No hay nueva fecha: se ofrece el reembolso. |
| 3 y 4. Dirección o datos | No hay fecha hasta que el cliente responda. Después sale de su respuesta, el corte de las 15:00 y el plazo de entrega. |
| 5. Capacidad | Posición del pedido en la cola acumulada, a 10 pedidos por día, más el plazo de entrega. |

## 8. Cómo redactar los avisos al cliente

El tono es cercano pero resolutivo: se tutea al cliente y se usa un lenguaje formal. Cada aviso dice qué pasó, qué hará la tienda y qué viene ahora, en ese orden.

**Estructura de un aviso**

1. Saludo con el nombre del cliente.
2. Qué pasó, en una frase y sin tecnicismos.
3. Qué hará la tienda.
4. La nueva fecha, o lo que necesitamos del cliente.
5. La compensación, si corresponde.
6. Un cierre breve.

**Qué evitar:** jerga, culpar al cliente, prometer fechas que el sistema no calculó y dar explicaciones largas.

En los ejemplos, lo que va entre llaves lo completa el sistema con datos reales. La compensación se completa con "te reembolsamos el costo del envío" o con "te dejamos un cupón de 30 % de descuento para tu próxima compra".

### Ejemplo: courier que no cumple (causa 1)

> Hola {nombre}, tu pedido {numero_pedido} se está demorando porque el courier no cumplió el plazo de entrega. Ya presentamos el reclamo y la nueva fecha estimada de entrega es el {nueva_fecha}. Como el atraso supera los 2 días, {compensacion}. Seguimos atentos a tu pedido hasta que llegue.

### Ejemplo: falta de stock (causa 2)

> Hola {nombre}, el producto de tu pedido {numero_pedido} está agotado y ya lo estamos reponiendo. Tendremos stock desde el {fecha_stock} y podemos enviártelo después de esa fecha. ¿Te sirve esperar? Si prefieres, puedes pedir el reembolso completo y lo tramitamos de inmediato.

### Ejemplo: dirección errónea, primer mensaje (causa 3)

> Hola {nombre}, no pudimos entregar tu pedido {numero_pedido} porque la dirección registrada no era válida. ¿Nos confirmas tu dirección completa? Apenas la recibamos, reprogramamos la entrega.

### Ejemplo: último mensaje antes de cancelar (causas 3 y 4)

> Hola {nombre}, te escribimos por tercera vez por tu pedido {numero_pedido}: aún necesitamos tus datos de entrega para despacharlo. Si no los recibimos, cancelaremos el pedido y te reembolsaremos el total.

### Ejemplo: cliente sin datos, primer mensaje (causa 4)

> Hola {nombre}, para despachar tu pedido {numero_pedido} nos faltan tus datos de entrega. ¿Puedes enviárnoslos? Con eso lo dejamos en preparación de inmediato.

### Ejemplo: capacidad de preparación (causa 5)

> Hola {nombre}, estamos con alta demanda y tu pedido {numero_pedido} se preparará más tarde de lo previsto. La nueva fecha estimada de entrega es el {nueva_fecha}. Como el atraso supera los 2 días, {compensacion}.

## 9. Casos que requieren revisión humana

En cuatro situaciones el sistema no decide por su cuenta: informa el caso y espera la revisión del usuario.

- **Pedido sin causa registrada:** se informa "causa no determinada". No se inventa una causa ni se envía un aviso personalizado.
- **Más de una causa a la vez:** por ejemplo, falta de stock y courier. La gestión depende del criterio del usuario.
- **Cliente que reclama o responde con molestia:** el aviso automático no es la respuesta adecuada.
- **Compensación fuera de las reglas:** cualquier descuento o reembolso distinto de los de la sección 6.

## 10. Preguntas frecuentes

**¿Cuándo se considera atrasado un pedido?** Cuando no está entregado ni cancelado y hoy supera su fecha prometida.

**¿Desde cuántos días se compensa?** Desde 2 días de atraso, si la causa es el courier, la falta de stock (con espera aceptada) o la capacidad.

**¿Cómo se compensa?** Con el reembolso del envío si el cliente lo pagó, o con un cupón de 30 % en su siguiente compra si no lo pagó.

**¿Cuánto espera un cliente cuando falta stock?** Durante los primeros 3 días de atraso se le informa desde cuándo habrá stock y decide si espera. Pasado ese plazo se le ofrece el reembolso.

**¿Qué pasa si el cliente no responde a la solicitud de dirección o datos?** Se le envían hasta 3 mensajes. Si no responde después del tercero, el pedido se cancela y se reembolsa.

**¿Quién calcula la nueva fecha?** El sistema. El redactor nunca la calcula ni la estima.

**¿Cuál es el plazo de entrega según la zona?** Máximo 2 días en la Región Metropolitana y 5 días en regiones, contados desde el despacho.

**¿Qué se hace si el pedido no tiene causa registrada?** Se informa "causa no determinada" y se pasa a revisión humana.
