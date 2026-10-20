# Один образ на все пакеты: клиенту разворачивается тот же образ с его конфигом и .env.
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    AUTOSTUDIO_DATA_DIR=/data

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra sheets --extra llm --no-install-project
COPY src ./src
COPY examples ./examples
RUN uv sync --frozen --no-dev --extra sheets --extra llm

# Не root: процессу нужны только /data (журнал, SQLite) и чтение конфига.
RUN useradd --create-home --uid 10001 autostudio && mkdir -p /data && chown autostudio /data
USER autostudio

ENV PATH="/app/.venv/bin:$PATH"
ENTRYPOINT ["autostudio"]
CMD ["--help"]
