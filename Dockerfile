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
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
