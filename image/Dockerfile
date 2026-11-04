# openttd-bench v3 game image: a stock Python base with the pinned OpenTTD content cached.
# The code payload is uploaded per episode by the env (see openttd_bench/payload.py), so
# this server-only image needs the binaries and control-plane dependencies.
# Build from the repo root:  docker build -f image/Dockerfile -t openttd-bench:v3 .
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libgomp1 libglib2.0-0 xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" "pydantic>=2"

COPY image/fetch_content.py /opt/openttd/fetch_content.py
RUN python3 /opt/openttd/fetch_content.py --cache /opt/openttd/cache --install /opt/openttd/install

# A baked copy of the code for manual runs; the env's payload upload replaces it.
COPY image/ /opt/openttd/

WORKDIR /work
