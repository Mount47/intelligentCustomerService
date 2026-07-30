# SupportFlow 镜像（api 与 worker 共用）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv：快速安装依赖
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Hatchling 构建项目时必须能看到 app 包；先复制元数据与后端源码，再安装。
COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts
COPY alembic.ini ./
COPY migrations ./migrations
RUN uv pip install --system --no-cache ".[observability]"

RUN groupadd --system supportflow \
    && useradd --system --gid supportflow --home-dir /app --no-create-home supportflow \
    && chown -R supportflow:supportflow /app

USER supportflow

EXPOSE 8000

# 默认起 API；worker 在 compose 里覆盖 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
