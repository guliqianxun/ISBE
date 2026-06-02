# Contributing to ISBE

Thanks for your interest! ISBE is a self-hosted, single-user-first periodic
research pipeline. This guide covers how to set up, test, and submit changes.

> 中文开发者：核心约定与 [`AGENTS.md`](AGENTS.md) 一致，本文件是其面向外部贡献者的英文版。

## Development setup

```bash
git clone <repo> && cd ISBE
cp .env.example .env          # fill DEEPSEEK_API_KEY or ANTHROPIC_API_KEY
uv sync --all-extras
docker compose up -d          # infra (postgres / minio / prefect / ...)
uv run alembic upgrade head
```

## Ground rules (see AGENTS.md for the full red lines)

1. **One-directional data flow**: collect → process → store → retrieve → generate. Never call backwards.
2. **Everything the agent learns is a file**: drafts → review → persist → index. Never hot-swap running code.
3. **Humans win**: on memory conflicts the LLM only suggests (read-only); user edits take priority.
4. **Reproducibility is a hard requirement**: every LLM call is traced; if you change a `templates/*.j2`, regenerate the golden output.

## Workflow

- **TDD**: write a failing test first, then the implementation.
- **Conventional commits**: `feat:` / `fix:` / `docs:` / `chore:` / `test:`.
- Keep PRs focused; link the issue.

## Before you push

```bash
uv run ruff check src/ tests/        # lint
uv run pytest -m "not integration"   # unit tests (no infra needed)
uv run pytest                        # full suite (needs docker compose up)
```

Optionally install the git hooks so this runs automatically:

```bash
uv run pre-commit install
```

## Adding a new topic

A new topic is one `src/isbe/topics/<name>/topic.yaml` plus a built-in
collector preset, then one dispatch line in `scheduler.py`. See the README
"加一个新 topic" section for the template. Do not change the shared 5-section
digest contract — that uniform interface is the core of the project.

## Reporting bugs / requesting features

Use the GitHub issue templates. For security issues, see
[`SECURITY.md`](SECURITY.md) — do not open a public issue.
