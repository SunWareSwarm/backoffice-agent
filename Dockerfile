FROM python:3.12-slim

WORKDIR /code
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY scripts ./scripts
# Los manuales y el conjunto de evaluación son insumos del proyecto y viajan en la imagen. La base simulada y la base vectorial
# NO: las crea el arranque y viven en el volumen "datos" (ver docker-compose.yml). El .dockerignore deja fuera data/*.db y data/chroma.
COPY data ./data

EXPOSE 8000
# Un solo comando: prepara lo que falte (base simulada y RAG) y levanta la API. Es idempotente: si ya está hecho, lo omite.
CMD ["python", "-m", "scripts.arrancar", "--host", "0.0.0.0", "--port", "8000"]
