FROM python:3.12-slim

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
