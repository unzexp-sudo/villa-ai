# syntax=docker/dockerfile:1
#
# Villa AI — production image.
#
# WHY THIS FILE IS NAMED EXACTLY "Dockerfile" AND LIVES AT THE REPOSITORY ROOT:
# Railway auto-detects a file named exactly `Dockerfile` in the service root.
# Any other name (`Dockerfile.booking`, `docker/prod.Dockerfile`) is silently
# ignored, the service falls back to Railpack, and a repo with more than one
# package (engine/ + web/) gives Railpack no way to guess a start command —
# which is exactly the "Railpack failed to prepare the build" failure.
#
# Two stages:
#   1. web   — builds the Vite bundle into /app/web/dist
#   2. engine— Python runtime with the FastAPI app and the built bundle
#
# The engine is the single service; it serves the API under /api and the
# built SPA as static files, so the frontend can call the API same-origin
# (no CORS in production, no hardcoded localhost origin).

# ---------------------------------------------------------------------------
# Stage 1: build the frontend
# ---------------------------------------------------------------------------
FROM node:20-slim AS web

WORKDIR /build

# Install dependencies from the lockfile first so this layer caches across
# source-only changes.
COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./

# `npm run build` is the bundler only (no `tsc`): a type error is a CI gate,
# not a reason to fail an image build. Vite's own module resolution IS enforced
# here — a genuinely missing import must fail the build loudly rather than
# shipping a half-broken bundle.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: python runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Runtime deps only. Keeping the engine's own pyproject install here means the
# container and local dev resolve the same dependency set.
COPY engine/pyproject.toml ./
RUN pip install \
      "fastapi>=0.110" \
      "uvicorn[standard]>=0.27" \
      "pydantic>=2.6" \
      "python-multipart>=0.0.9" \
      "shapely>=2.0" \
      "networkx>=3.2" \
      "numpy>=1.26" \
      "ezdxf>=1.1" \
      "Pillow>=10" \
      "pypdf>=4" \
      "reportlab>=4" \
      "httpx>=0.27"

# Application code. `app/` is the top-level package so that
# `from app...` imports resolve exactly as they do locally (see engine/main.py).
COPY engine/app ./app
COPY engine/fixtures ./fixtures
COPY engine/main.py ./main.py

# Built frontend, served as static files by the same FastAPI process.
COPY --from=web /build/dist ./web/dist

# Writable data directory for the JSON file store (models, runs, leads, events).
# Declared as a volume so it can be attached to a Railway volume later; the
# app falls back to container-local storage when no volume is mounted.
RUN mkdir -p /app/data

EXPOSE 8000

# Railway injects PORT at runtime; uvicorn must read it rather than hardcode.
CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
