# One image for the API, the migration job, the worker and the scheduler (different commands).
FROM python:3.12.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# The slim image already ships ca-certificates and tzdata (needed for TZ=Europe/Warsaw in the scheduler).
RUN pip install --no-cache-dir uv==0.11.32

# pyosmium (OSM power connector) links against libexpat, which the slim image does not ship.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# supercronic runs the scheduler's crontab. Checksum-pinned; for arm64 build with
#   --build-arg SUPERCRONIC_ARCH=arm64 \
#   --build-arg SUPERCRONIC_SHA256=f1f8585c66de020fef494dd636058f99949d108f569fef00016a1c8b9eb145b3
ARG SUPERCRONIC_VERSION=v0.2.33
ARG SUPERCRONIC_ARCH=amd64
ARG SUPERCRONIC_SHA256=feefa310da569c81b99e1027b86b27b51e6ee9ab647747b49099645120cfc671
ADD --chmod=755 --checksum=sha256:${SUPERCRONIC_SHA256} \
    https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${SUPERCRONIC_ARCH} \
    /usr/local/bin/supercronic

WORKDIR /app

# Dependencies first, for layer caching.
COPY pyproject.toml uv.lock .python-version ./
COPY pipeline/pyproject.toml pipeline/pyproject.toml
COPY api/pyproject.toml api/pyproject.toml
RUN uv sync --frozen --no-dev --no-install-workspace

COPY pipeline pipeline
COPY api api
COPY config config
COPY infra/crontab infra/crontab
COPY data/discovery data/discovery
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 dclh && mkdir -p /data && chown dclh:dclh /data
USER dclh

ENV PATH="/app/.venv/bin:$PATH" \
    DCLH_DATA_DIR=/data \
    DCLH_CONFIG_DIR=/app/config

EXPOSE 8000
CMD ["uvicorn", "dclh_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
