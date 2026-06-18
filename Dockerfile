FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"
WORKDIR /app

COPY --chown=user pyproject.toml uv.lock README.md ./
RUN pip install --no-cache-dir uv && uv sync --no-dev

COPY --chown=user . /app

ENV FASTEMBED_CACHE_PATH=/home/user/.cache/fastembed
ENV APP_ENV=production
EXPOSE 7860

# Secrets requeridos en el Space (Settings → Variables and secrets):
#   DATABASE_URL  → postgresql://...neon... (la búsqueda vectorial necesita pgvector)
#   SECRET_KEY    → openssl rand -hex 32
#   GROQ_API_KEY  → LLM principal
# En producción no hay bypass de tenant: provisiona un tenant en Neon
# (scripts/seed_db.py contra DATABASE_URL) y usa su X-API-Key.

CMD ["/bin/bash", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 7860"]
