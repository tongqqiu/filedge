# Reference container image for Filedge.
#
# Builds `filedge`, `filedge-fetch`, and `filedge-materialize` into a slim
# image. The core install carries only the file-ingestion path (SQLite); add
# destination/source extras at build time, e.g.:
#
#   docker build -f deploy/Dockerfile --build-arg EXTRAS="[snowflake]" -t filedge .
#
# `filedge run` is not a daemon — schedule the container (cron, a Kubernetes
# CronJob, or the looping services in deploy/docker-compose.yml).
FROM python:3.13-slim

LABEL org.opencontainers.image.title="Filedge" \
      org.opencontainers.image.description="Atomic file ingestion with content-hash idempotency and a full audit trail." \
      org.opencontainers.image.source="https://github.com/tongqqiu/filedge" \
      org.opencontainers.image.licenses="Apache-2.0"

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN pip install --no-cache-dir uv

# Install dependencies/package. Comma-free EXTRAS like "[snowflake]" or
# "[bigquery,s3]" select optional destinations/sources; empty = core (SQLite).
ARG EXTRAS=""
COPY pyproject.toml uv.lock README.md ./
COPY filedge ./filedge
RUN uv pip install --system ".${EXTRAS}"

# Run as a non-root user. Pre-create and own /data so an empty mounted volume
# (named volume, k8s emptyDir/PVC) inherits writable ownership — otherwise the
# non-root user cannot create the staging/landing directories on first write.
RUN useradd --create-home --uid 10001 filedge \
    && mkdir -p /data \
    && chown filedge:filedge /data
USER filedge
VOLUME ["/data"]

ENTRYPOINT ["filedge"]
CMD ["--help"]
