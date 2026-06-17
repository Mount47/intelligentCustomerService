# SupportFlow 镜像（api 与 worker 共用）
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# uv：快速安装依赖
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml ./
RUN uv pip install --system --no-cache .

# 再拷代码
COPY app ./app

EXPOSE 8000

# 默认起 API；worker 在 compose 里覆盖 command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
