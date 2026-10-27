# docker build -t isbe .
# The runtime image: the `isbe` command with the console, data at /data.
# Behind a blocked Docker Hub or a slow PyPI or npm route, e.g. in mainland China:
#   --build-arg REGISTRY=docker.1ms.run/library
#   --build-arg PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
#   --build-arg PYPI_FILES_MIRROR=https://pypi.tuna.tsinghua.edu.cn
#   --build-arg NPM_MIRROR=https://registry.npmmirror.com
ARG REGISTRY=docker.io/library

# The React console. Node is used here only; the runtime image has none.
FROM ${REGISTRY}/node:24-slim AS frontend
ARG NPM_MIRROR=""
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm \
    if [ -n "$NPM_MIRROR" ]; then npm config set registry "$NPM_MIRROR"; fi; \
    npm ci --no-audit --no-fund
COPY frontend ./
# Its stylesheet is shared with the classic console until that is retired.
COPY web/app.css /src/web/app.css
# Type check and build only. The tests replay recordings under tests/fixtures
# (77 MB) and run in CI (web-test) on every merge request, so the image build
# neither copies them nor fails on a test.
RUN npm run build

FROM ${REGISTRY}/python:3.14-slim AS build
ARG PYPI_MIRROR=""
# uv.lock pins its files on files.pythonhosted.org, and `uv sync --frozen`
# downloads them from there whatever the index; a mirror that serves the same
# /packages/ paths replaces that host in the image's copy of the lock (a
# plain https URL: sed reads # and & in it as syntax).
ARG PYPI_FILES_MIRROR=""
RUN pip install --no-cache-dir ${PYPI_MIRROR:+--index-url "$PYPI_MIRROR"} uv==0.13.3
ENV UV_PROJECT_ENVIRONMENT=/opt/isbe UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /src
COPY pyproject.toml uv.lock README.md ./
RUN if [ -n "$PYPI_FILES_MIRROR" ]; then \
      sed -i "s#https://files.pythonhosted.org/#${PYPI_FILES_MIRROR%/}/#g" uv.lock; \
    fi
# Dependencies first, so a code change reinstalls ISBE alone.
RUN --mount=type=cache,target=/root/.cache/uv \
    if [ -n "$PYPI_MIRROR" ]; then export UV_DEFAULT_INDEX="$PYPI_MIRROR"; fi; \
    uv sync --frozen --no-dev --no-install-project
COPY isbe ./isbe
COPY migrations ./migrations
COPY prompts ./prompts
COPY web ./web
COPY docs/configuration.md docs/deployment.md ./docs/
COPY --from=frontend /src/isbe/_assets/app ./isbe/_assets/app
# Non-editable: migrations, prompts and both consoles are copied into the
# package (isbe/_assets), so the runtime needs no checkout. uv keys its
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
