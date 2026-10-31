# One image, two Railway services (the eventtz and osceguide pattern, ADR 0021):
#   api      the default CMD below; health check path /v1/cell
#   worker   its Railway start command is `python -m app.worker.main`
# Both are dashboard settings (docs/runbooks/railway.md); there is no railway.toml.
# Migrations are not run here: the founder pushes them with `supabase db push --linked`.
#
# Built from the repository root, not from `api/`: the app reads the packs from the
# repository's `packs/` folder (core/packs.py, REPO = parents[3]), so the image keeps
# the same layout (/srv/api, /srv/packs).
#   docker build -f api/Dockerfile .
#
# Three stages. `dependencies` holds the build tools and installs every package into
# /opt/venv; `models` downloads the pinned model files. The final stage starts from a clean base
# and copies only /opt/venv and /opt/models.

# ---- Stage 1: install the dependencies (throwaway) ------------------------
FROM python:3.12-slim AS dependencies

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    POETRY_VERSION=2.1.4 \
    POETRY_VIRTUALENVS_CREATE=false \
    POETRY_NO_INTERACTION=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && pip install --no-cache-dir "poetry==${POETRY_VERSION}" \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /build
COPY api/pyproject.toml api/poetry.lock ./
RUN poetry install --only main --no-root

# ---- Stage 2: the in-process models (throwaway) ---------------------------
# The embedder and reranker files, pinned by revision and checked by SHA-256 (api/models.lock.json,
# ADR 0029). Railway has no volumes and the worker never downloads at run time.
FROM python:3.12-slim AS models

COPY scripts/fetch_models.py /build/fetch_models.py
COPY api/models.lock.json /build/models.lock.json
RUN python /build/fetch_models.py /opt/models /build/models.lock.json

# ---- Stage 3: the image that ships ----------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:${PATH}" \
    MODELS_DIR=/opt/models

COPY --from=dependencies /opt/venv /opt/venv
COPY --from=models /opt/models /opt/models

WORKDIR /srv
COPY packs/ packs/
COPY api/ api/
WORKDIR /srv/api

# The app fails here rather than at the first request if the packs or imports are broken.
RUN python -c "from app.core.packs import default_library; default_library()"

RUN useradd --system --uid 10001 --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /srv
USER appuser

EXPOSE 8000
# `exec` so Railway's SIGTERM reaches uvicorn. Keep-alive outlasts the proxies in front.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --timeout-keep-alive 75 --timeout-graceful-shutdown 110"]
