# syntax=docker/dockerfile:1

ARG PYTHON_VERSION=3.11
ARG NODE_VERSION=22

FROM node:${NODE_VERSION}-bookworm-slim AS frontend-build
ARG NPM_REGISTRY=https://registry.npmmirror.com
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --registry=${NPM_REGISTRY}
COPY frontend/ ./
RUN npm run build

FROM python:${PYTHON_VERSION}-slim-bookworm AS api
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        bubblewrap \
        curl \
        fonts-noto-cjk \
        libseccomp2 \
        libreoffice-calc \
        libreoffice-writer \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --index-url=${PYPI_INDEX} uv==0.10.12
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./backend/
RUN set -eux; \
    uv export --project backend --frozen --extra rag --extra materials \
      --format requirements.txt --no-hashes --no-annotate \
      --output-file /tmp/requirements.full.txt; \
    grep -Ev '^[[:space:]]*(torch|triton|cuda-|nvidia-)' \
      /tmp/requirements.full.txt > /tmp/requirements.cpu.txt; \
    uv pip sync --system --default-index=${PYPI_INDEX} /tmp/requirements.cpu.txt; \
    torch_spec="$(grep -E '^torch==' /tmp/requirements.full.txt | head -n 1)"; \
    test -n "${torch_spec}"; \
    uv pip install --system --torch-backend cpu --default-index=${PYPI_INDEX} "${torch_spec}"; \
    rm -f /tmp/requirements.full.txt /tmp/requirements.cpu.txt

COPY backend/ ./backend/
COPY start/ ./start/
COPY opensearch/opensearch_delivery/ ./opensearch/opensearch_delivery/
EXPOSE 8010
CMD ["uvicorn", "backend.interfaces.api.app:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]

FROM nginx:1.27-alpine AS web
COPY --from=frontend-build /src/frontend/dist/ /usr/share/nginx/html/
COPY deploy/app/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
