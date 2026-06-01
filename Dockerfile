# Cloud Run image for the Lumina marketplace (FastAPI UI + escrow + in-process agent).
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

# fonts-dejavu-core gives Pillow a real TTF for crisp product-card text.
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY lumina/ ./lumina/
COPY marketplace/ ./marketplace/

# Cloud Run injects $PORT.
ENV PORT=8080
CMD ["sh", "-c", "uvicorn marketplace.app:app --host 0.0.0.0 --port ${PORT}"]
