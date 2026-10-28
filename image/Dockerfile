# openttd-bench v3 game image: a stock Python base with the pinned OpenTTD content cached.
# The code payload is uploaded per episode by the env (see openttd_bench/payload.py), so
# this image only needs the binaries, the Python deps, setpriv and the agent users.
# Build from the repo root:  docker build -f image/Dockerfile -t openttd-bench:v3 .
FROM python:3.12-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates curl libgomp1 libglib2.0-0 procps util-linux xz-utils \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "fastapi>=0.110" "uvicorn>=0.29" "pydantic>=2" \
    && for i in 1 2 3 4; do useradd --create-home --shell /bin/bash agent$i; done

COPY image/fetch_content.py /opt/openttd/fetch_content.py
RUN python3 /opt/openttd/fetch_content.py --cache /opt/openttd/cache --install /opt/openttd/install

# A baked copy of the code for manual runs; the env's payload upload replaces it.
COPY image/ /opt/openttd/
RUN chmod 700 /opt/openttd/server /opt/openttd/game /opt/openttd/ai /opt/openttd/install \
    && chmod -R a+rX /opt/openttd/client

WORKDIR /work
