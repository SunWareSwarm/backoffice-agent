# Manual de órdenes de compra

Documento simulado para el RAG del agente de back office de una tienda online de carteras. Ámbito: reposición de inventario. Versión 1.

## 1. Propósito y alcance

Este manual explica cuándo y cómo la tienda repone inventario con una orden de compra. Lo usan el agente de inventario y el redactor de respuestas.

La cantidad sugerida y las fechas las calcula el sistema con reglas fijas. Una orden solo se crea después de que el usuario la aprueba.

## 2. Cuándo se propone una orden de compra

Se propone una orden cuando un producto está cerca de quedarse sin stock o cuando hubo demanda que no se pudo atender. Un producto con stock estancado no se repone.

El lead time del proveedor es de 10 días: desde que se compra hasta que el producto llega a la tienda. Por eso se ordena antes de que el stock se agote.

| Situación | Señal | Qué se recomienda |
| --- | --- | --- |
| Quiebre próximo | Los días de cobertura (stock dividido por las ventas diarias recientes) se acercan o bajan de los 10 días de lead time. | Proponer una orden de compra. |
| Stock bajo el punto de reorden | El stock cae bajo la demanda diaria por 10 días, más el stock de seguridad. | Proponer una orden de compra. |
| Demanda no atendida | Hay pedidos que fallaron por falta de stock de ese producto. | Considerar esas unidades al calcular la cantidad. |
| Stock estancado | El producto lleva 14 días sin ventas. | No reponer: alertar para liquidar o promocionar. |

## 3. Datos que lleva una orden

Toda orden lleva seis datos. El producto se llama igual en la tienda y en el proveedor, lo que evita confusiones al ordenar.

| Dato | Qué es |
| --- | --- |
| ID de la orden | Identificador único con el que se hace el seguimiento. |
| Producto | Nombre del producto, igual en la tienda y en el proveedor. |
| Cantidad | Unidades pedidas. No hay pedido mínimo: se puede pedir desde 1 unidad. |
| Fecha de la orden | Día en que se crea la orden. |
| Fecha de entrega esperada | Fecha de la orden más el lead time de 10 días. |
| Costo del pedido | Costo total de la orden. |

La tienda trabaja con varios proveedores, y todos tienen el mismo lead time de 10 días.

## 4. Flujo de una orden de compra

Una orden recorre seis pasos, y la creación solo ocurre después de la aprobación del usuario.

1. El agente de inventario detecta la necesidad y propone la orden, con una cantidad calculada por el sistema.
2. La propuesta pasa a la compuerta de aprobación, junto con las demás acciones de la revisión.
3. El usuario aprueba o rechaza la orden.
4. Si la aprueba, se registra la orden con su ID y queda en estado "creada".
5. La orden se sigue por su ID hasta que el proveedor la despacha y queda "en camino".
6. Cuando llega a la tienda, queda "recibida" y el stock del producto se actualiza.

## 5. Estados de una orden

Una orden registrada tiene cuatro estados posibles. La propuesta existe solo en la compuerta de aprobación y no es un estado.

| Estado | Qué significa |
| --- | --- |
| Creada | El usuario aprobó la orden y quedó registrada con su ID. |
| En camino | El proveedor despachó la orden y viene hacia la tienda. |
| Recibida | La orden llegó y el stock se actualizó. Es un estado final. |
| Cancelada | La orden se anuló antes de llegar. Es un estado final. |

Una orden en camino puede salvar un pedido que falló por falta de stock. El manual de operaciones logísticas explica cómo se le avisa al cliente en ese caso.

## 6. Qué se le comunica al usuario

Tras una orden aprobada, el usuario recibe qué quedó hecho, con qué datos y cómo seguirla. Tras un rechazo, recibe la confirmación de que no se ejecutó nada.

El tono es cercano pero resolutivo, igual que en el resto de las comunicaciones. En los ejemplos, lo que va entre llaves lo completa el sistema con datos reales.

### Ejemplo: orden aprobada

> Tu orden de compra {id_orden} quedó ingresada. Pedimos {cantidad} unidades de {producto} al proveedor {proveedor}, por un costo total de {costo}. Esperamos que lleguen el {fecha_entrega}. Puedes hacerle seguimiento con el ID {id_orden}.

### Ejemplo: orden rechazada

> No creamos la orden de compra de {producto}, tal como lo indicaste. No se ejecutó ninguna acción. Si cambias de idea, la volvemos a proponer en la próxima revisión.

## 7. Casos especiales

Cuatro situaciones cambian lo que se propone o cómo se calcula.

- **Producto con stock estancado:** no se repone. Se alerta para liquidar o promocionar.
- **Varios proveedores:** cada orden va a un solo proveedor. Productos de distintos proveedores generan órdenes separadas.
- **Orden ya en camino para el mismo producto:** la cantidad sugerida descuenta las unidades que ya vienen en camino, para no duplicar la orden.
- **Cantidad mínima:** no hay pedido mínimo. Si el sistema calcula una cantidad pequeña, se puede ordenar incluso 1 unidad.

## 8. Preguntas frecuentes

**¿Cuánto demora una orden de compra en llegar?** 10 días, desde que se crea hasta que el producto llega a la tienda. Es igual para todos los proveedores.

**¿Hay un pedido mínimo?** No. Se puede pedir desde 1 unidad.

**¿Qué datos lleva una orden?** ID de la orden, producto, cantidad, fecha de la orden, fecha de entrega esperada y costo del pedido.

**¿Quién aprueba una orden?** El usuario, en la compuerta de aprobación. Sin su aprobación, la orden no se crea.

**¿Se repone un producto que lleva 14 días sin ventas?** No. Se alerta para liquidar o promocionar.

**¿Cómo se hace el seguimiento?** Con el ID de la orden, que se entrega al usuario cuando la orden queda creada.

**¿Qué pasa con los pedidos que fallaron por falta de stock?** Se consideran demanda no atendida y suman unidades a la cantidad sugerida de la próxima orden.
