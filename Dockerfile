FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH"
WORKDIR /app

COPY --chown=user pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev

ENV SENTENCE_TRANSFORMERS_HOME=/home/user/.cache/st
ENV HF_HOME=/home/user/.cache/hf
ENV TOKENIZERS_PARALLELISM=false

RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('paraphrase-multilingual-MiniLM-L12-v2')"

COPY --chown=user . /app
ENV DATABASE_URL=sqlite+aiosqlite:////tmp/chatbot.db
ENV APP_ENV=development
EXPOSE 7860

CMD ["/bin/bash", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 7860"]
