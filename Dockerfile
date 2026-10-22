# docker build -t isbe .
# The runtime image: the `isbe` command with the console, data at /data.
# Behind a blocked Docker Hub or a slow PyPI route, e.g. in mainland China:
#   --build-arg REGISTRY=docker.1ms.run/library
#   --build-arg PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
ARG REGISTRY=docker.io/library

FROM ${REGISTRY}/python:3.14-slim AS build
ARG PYPI_MIRROR=""
RUN pip install --no-cache-dir uv==0.13.3
ENV UV_PROJECT_ENVIRONMENT=/opt/isbe UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
# Dependencies first, so a code change reinstalls ISBE alone.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PYPI_MIRROR" ]; then export UV_DEFAULT_INDEX="$PYPI_MIRROR"; fi; \
    uv sync --frozen --no-dev --no-install-project
COPY isbe ./isbe
COPY migrations ./migrations
COPY prompts ./prompts
COPY web ./web
COPY README.en.md ./
COPY docs ./docs
COPY deploy/searxng/README.md ./deploy/searxng/
# Non-editable: migrations, prompts, the console and its help pages are
# copied into the package (isbe/_assets), so the runtime needs no checkout. uv keys its
# cached build of a local project on pyproject.toml alone, so without
# --refresh-package a rebuild reuses the wheel of an earlier build and ships
# its code; the diff refuses an image whose package is not this source.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PYPI_MIRROR" ]; then export UV_DEFAULT_INDEX="$PYPI_MIRROR"; fi; \
    uv sync --frozen --no-dev --no-editable --refresh-package isbe \
    && diff -r -x __pycache__ -x _assets isbe /opt/isbe/lib/python3.14/site-packages/isbe

FROM ${REGISTRY}/python:3.14-slim
COPY --from=build /opt/isbe /opt/isbe
ENV PATH=/opt/isbe/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Mount the data directory at /data, writable by uid 1000.
ENV ISBE_DATA=/data
USER 1000:1000
WORKDIR /data
ENTRYPOINT ["isbe"]
CMD ["--help"]
