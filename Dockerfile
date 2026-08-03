FROM python:3.12-slim

# Optional PyPI mirror — set via build-arg for networks where files.pythonhosted.org
# is slow/unreachable (e.g. CN servers). Default is empty (use upstream PyPI).
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PIP_INDEX_URL=""
ENV PIP_INDEX_URL=${PIP_INDEX_URL}
# uv reads UV_DEFAULT_INDEX (UV_INDEX_URL is the deprecated alias — keep both).
# Empty build-arg falls back to upstream PyPI explicitly (uv rejects "").
ENV UV_DEFAULT_INDEX=${PIP_INDEX_URL:-https://pypi.org/simple}
ENV UV_INDEX_URL=${PIP_INDEX_URL:-https://pypi.org/simple}
# Generous timeout: CN routes to pythonhosted/mirrors stall intermittently
ENV UV_HTTP_TIMEOUT=600

# uv installer (pin to match host)
RUN pip install --no-cache-dir uv==0.10.4

WORKDIR /app

# Install deps first to leverage cache
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --all-extras --no-install-project

# Copy source + alembic
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./

# Install project itself
RUN uv sync --frozen --all-extras

ENV PATH="/app/.venv/bin:$PATH"
# Use venv binary directly — avoids `uv run` re-syncing on bind-mounted /app/src
ENTRYPOINT ["/app/.venv/bin/radar"]
CMD ["scheduler", "serve"]
