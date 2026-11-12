FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    HOME=/tmp/home

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash libreoffice-writer libreoffice-calc poppler-utils fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/runtime-requirements.txt
RUN pip install --no-cache-dir -r /tmp/runtime-requirements.txt \
    && rm /tmp/runtime-requirements.txt \
    && mkdir /workspace \
    && chmod 0777 /workspace

WORKDIR /workspace
USER 65534:65534
CMD ["sleep", "86400"]
