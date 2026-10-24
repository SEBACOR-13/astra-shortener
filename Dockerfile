# Imagen unica para local, CI y Render: lo que pasa las pruebas es
# exactamente lo que se despliega.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Las dependencias se copian primero para aprovechar la cache de capas:
# si solo cambia el codigo, esta capa no se reconstruye.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Usuario sin privilegios.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Render inyecta PORT en tiempo de ejecucion; en local cae a 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
