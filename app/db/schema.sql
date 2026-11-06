-- Tienda simulada. Los nombres y columnas siguen el diagrama entidad-relación del proyecto.
-- Las fechas se guardan como texto ISO (AAAA-MM-DD o AAAA-MM-DD HH:MM:SS), que SQLite ordena bien.

CREATE TABLE parametros (
    clave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);

CREATE TABLE proveedores (
    proveedor_id INTEGER PRIMARY KEY,
    nombre       TEXT NOT NULL
);

CREATE TABLE productos (
    sku          TEXT PRIMARY KEY,
    nombre       TEXT NOT NULL,
    categoria    TEXT NOT NULL,
    tamano       TEXT NOT NULL CHECK (tamano IN ('mini', 'mediano', 'grande')),
    color        TEXT,
    material     TEXT,
    estilo       TEXT,
    precio       NUMERIC NOT NULL CHECK (precio BETWEEN 15000 AND 40000),
    costo        NUMERIC NOT NULL CHECK (costo > 0),
    proveedor_id INTEGER NOT NULL REFERENCES proveedores (proveedor_id)
);

CREATE TABLE inventario (
    sku             TEXT PRIMARY KEY REFERENCES productos (sku),
    stock_actual    INTEGER NOT NULL CHECK (stock_actual >= 0),
    stock_seguridad INTEGER NOT NULL CHECK (stock_seguridad >= 0)
);

CREATE TABLE pedidos (
    pedido_id       TEXT PRIMARY KEY,
    fecha_ingreso   TEXT NOT NULL,
    region_destino  TEXT NOT NULL CHECK (region_destino IN ('RM', 'regiones')),
    estado          TEXT NOT NULL CHECK (estado IN (
        'solicitado', 'ingresado', 'en_preparacion', 'espera_despacho', 'en_ruta',
        'proximo_a_llegar', 'entregado', 'entrega_fallida', 'cancelado')),
    envio_pagado    INTEGER NOT NULL CHECK (envio_pagado IN (0, 1)),
    fecha_prometida TEXT NOT NULL,
    -- Causa del problema detectado en el pedido, aunque todavía no haya vencido su fecha.
    causa_atraso    TEXT CHECK (causa_atraso IN (
        'courier', 'falta_stock', 'direccion_erronea', 'cliente_sin_datos', 'capacidad'))
);

CREATE TABLE lineas_pedido (
    linea_id        INTEGER PRIMARY KEY,
    pedido_id       TEXT NOT NULL REFERENCES pedidos (pedido_id),
    sku             TEXT NOT NULL REFERENCES productos (sku),
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC NOT NULL
);

CREATE TABLE historial_estados (
    evento_id  INTEGER PRIMARY KEY,
    pedido_id  TEXT NOT NULL REFERENCES pedidos (pedido_id),
    estado     TEXT NOT NULL,
    fecha_hora TEXT NOT NULL
);

CREATE TABLE ordenes_compra (
    orden_id               INTEGER PRIMARY KEY,
    sku                    TEXT NOT NULL REFERENCES productos (sku),
    proveedor_id           INTEGER NOT NULL REFERENCES proveedores (proveedor_id),
    cantidad               INTEGER NOT NULL CHECK (cantidad > 0),
    costo_total            NUMERIC NOT NULL,
    estado                 TEXT NOT NULL CHECK (estado IN ('creada', 'en_camino', 'recibida', 'cancelada')),
    creada_en              TEXT NOT NULL,
    fecha_entrega_esperada TEXT NOT NULL
);

CREATE TABLE bandeja_avisos (
    aviso_id  INTEGER PRIMARY KEY,
    pedido_id TEXT NOT NULL REFERENCES pedidos (pedido_id),
    tipo      TEXT NOT NULL CHECK (tipo IN ('atraso', 'solicitud_direccion', 'solicitud_datos')),
    texto     TEXT NOT NULL,
    estado    TEXT NOT NULL
);

CREATE TABLE compensaciones (
    compensacion_id INTEGER PRIMARY KEY,
    pedido_id       TEXT NOT NULL REFERENCES pedidos (pedido_id),
    tipo            TEXT NOT NULL CHECK (tipo IN ('reembolso_envio', 'cupon_30')),
    estado          TEXT NOT NULL,
    creada_en       TEXT NOT NULL
);

CREATE TABLE reembolsos (
    reembolso_id INTEGER PRIMARY KEY,
    pedido_id    TEXT NOT NULL REFERENCES pedidos (pedido_id),
    motivo       TEXT NOT NULL CHECK (motivo IN ('sin_stock', 'direccion', 'datos', 'otro')),
    estado       TEXT NOT NULL,
    creado_en    TEXT NOT NULL
);

CREATE TABLE respuestas_cliente (
    respuesta_id INTEGER PRIMARY KEY,
    pedido_id    TEXT NOT NULL REFERENCES pedidos (pedido_id),
    tipo         TEXT NOT NULL CHECK (tipo IN ('acepta_espera', 'rechaza_espera', 'envia_direccion', 'envia_datos')),
    recibida_en  TEXT NOT NULL
);

CREATE INDEX idx_lineas_pedido ON lineas_pedido (pedido_id);
CREATE INDEX idx_lineas_sku ON lineas_pedido (sku);
CREATE INDEX idx_historial_pedido ON historial_estados (pedido_id);
CREATE INDEX idx_avisos_pedido ON bandeja_avisos (pedido_id);

-- Las ventas no son una tabla: son una vista sobre pedidos y líneas, para tener una sola fuente de
-- verdad. Un pedido cancelado no es una venta.
CREATE VIEW ventas AS
SELECT p.fecha_ingreso AS fecha, l.sku, l.cantidad, l.precio_unitario
FROM lineas_pedido l
JOIN pedidos p ON p.pedido_id = l.pedido_id
WHERE p.estado <> 'cancelado';
