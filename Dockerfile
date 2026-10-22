# Avodeck API + worker (same image; App Platform overrides CMD for the worker component).
# Build context: repository root (needs api/ and packs/).
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.1.1 \
    POETRY_VIRTUALENVS_CREATE=false \
    PACKS_DIR=/app/packs

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "poetry==${POETRY_VERSION}"

WORKDIR /app/api

COPY api/pyproject.toml api/poetry.lock ./
RUN poetry install --no-interaction --no-ansi --only main --no-root

COPY api/app ./app
RUN poetry install --no-interaction --no-ansi --only main
COPY packs /app/packs

EXPOSE 8080

# DigitalOcean App Platform sets PORT (often 8080).
CMD ["sh", "-c", "poetry run uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
