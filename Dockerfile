FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip && pip install .

COPY app ./app
COPY scripts ./scripts

# Ledger lives on a mounted volume.
ENV LEDGER_DB_PATH=/data/ledger.db
EXPOSE 8080

# Shell form so ${PORT} is expanded at runtime (Railway injects PORT; defaults to
# 8080 locally). An exec-form CMD or a JSON start command passes "$PORT" literally,
# which makes uvicorn fail with: Invalid value for '--port': '$PORT'.
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
