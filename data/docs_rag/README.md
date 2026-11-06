# Documentos del RAG

Los tres manuales en Markdown. Son la fuente que se corta, se vectoriza y se guarda en Chroma.

- `manual_operaciones_logisticas.md`
- `manual_ordenes_compra.md`
- `guia_marca_politica_precios.md`

El corte depende de los títulos y debe coincidir con `data/eval/rag_catalogo_fragmentos.csv`. **Si cambias un título,
agregas o quitas una sección, actualiza también el catálogo:** si no, la ingesta falla con un mensaje que dice qué no coincide.
