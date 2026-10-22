# Development image for the Python port: the pinned interpreter plus uv.
# scripts/py-test builds it once and mounts the checkout at /src.
FROM python:3.14-slim
RUN pip install --no-cache-dir uv==0.13.3
# The virtualenv lives in the checkout (ignored by Git) so repeated runs reuse it;
# the uv cache is a host directory mounted at /uv-cache.
ENV UV_PROJECT_ENVIRONMENT=/src/.venv-docker \
    UV_CACHE_DIR=/uv-cache \
    UV_LINK_MODE=copy \
    HOME=/tmp
WORKDIR /src
