# P1 — Nowcasting Topic MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端跑通 ISBE 的第一个 topic — 临近降水科研订阅 — 验证 spec 中通用 Topic 抽象 / Digester 三段 / review 流 / memory lifecycle 在真实场景下能落地。NVDA 域放 Plan #2 复用同一抽象。

**Architecture:** SQLAlchemy + alembic 管 Postgres facts schema；Prefect 3 flow 写 collector / digester；anthropic SDK + Langfuse 包 LLM 调用 + trace；MinIO 存 artifact 与 PDF；jinja2 渲染 digest 模板；新增 CLI 子命令 `radar memory reindex/archive`、`radar topics list/run`，并实化 `radar review memory`。memory 文件按 §2.4 周分档归档；MVP 先不接 Qdrant（YAGNI——digester 仅按时间窗筛，无语义检索）。

**Tech Stack:** Python 3.11+ (uv)、Pydantic v2、SQLAlchemy 2、alembic、Prefect 3、anthropic Python SDK、langfuse Python SDK、jinja2、httpx、minio Python SDK、Typer、pytest、ruff。

**Spec reference:** `docs/superpowers/specs/2026-05-07-p1-sample-topics-design.md`（重点：§1 三层模型、§2 Topic 抽象与三段约定、§2.4 memory lifecycle、§3 nowcasting 域、§5 哪些抽象、哪些不抽象）。

---

## File Structure

```
F:/codes/ISBE/
├── pyproject.toml                          # 追加 deps: sqlalchemy, alembic, jinja2, anthropic, langfuse, minio
├── alembic.ini                             # alembic 配置
├── alembic/
│   ├── env.py                              # alembic env (sync mode, isbe.facts.db.metadata)
│   └── versions/
│       ├── 001_common_facts.py             # artifacts 表 + topic_runs 表
│       └── 002_nowcasting.py               # papers / repos / events 表
│
├── src/isbe/
│   ├── facts/
│   │   ├── __init__.py
│   │   ├── db.py                           # SQLAlchemy engine + sessionmaker + Base
│   │   └── artifacts.py                    # Artifact ORM model (跨 topic)
│   │
│   ├── topics/
│   │   ├── __init__.py
│   │   ├── base.py                         # Topic Protocol + Digester Protocol + dataclasses
│   │   ├── registry.py                     # 扫 src/isbe/topics/<id>/topic.yaml 注册
│   │   └── nowcasting/
│   │       ├── __init__.py
│   │       ├── topic.yaml                  # id/label/cadence
│   │       ├── facts.py                    # ORM: Paper, Repo, Event
│   │       ├── collectors/
│   │       │   ├── __init__.py
│   │       │   ├── arxiv.py                # @flow arxiv_collector
│   │       │   └── github.py               # @flow github_repo_collector
│   │       ├── digester.py                 # @flow weekly_digester
│   │       └── templates/
│   │           └── weekly.j2               # 三段模板
│   │
│   ├── memory/
│   │   ├── (existing)
│   │   ├── lifecycle.py                    # archive_old_reading + reindex_memory_md
│   │   └── pending.py                      # 写入 .pending 草稿、移动到正式目录
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py                       # AnthropicClient + Langfuse tracing wrapper
│   │   └── prompts.py                      # build_digest_prompt
│   │
│   ├── artifacts/
│   │   ├── __init__.py
│   │   └── store.py                        # save_artifact: MinIO + PG index 双写
│   │
│   └── cli/
│       ├── (existing main.py, review.py — review.py 实化)
│       ├── memory_cmd.py                   # radar memory reindex/archive
│       └── topics_cmd.py                   # radar topics list/run
│
├── tests/
│   ├── (existing)
│   ├── conftest.py                         # 追加 db_session / minio_client fixtures（mark integration）
│   ├── test_facts_db.py
│   ├── test_topics_base.py
│   ├── test_topics_registry.py
│   ├── test_memory_lifecycle.py
│   ├── test_memory_pending.py
│   ├── test_llm_client.py                  # 用 monkeypatched anthropic
│   ├── test_artifacts_store.py             # mock MinIO
│   ├── test_cli_memory_cmd.py
│   ├── test_cli_topics_cmd.py
│   ├── test_cli_review_real.py             # 取代 P0 placeholder 测试
│   ├── test_nowcasting_facts.py
│   ├── test_nowcasting_arxiv_collector.py  # mock httpx
│   ├── test_nowcasting_github_collector.py # mock httpx
│   └── test_nowcasting_digester.py         # mock LLM + mock collectors
│
└── memory/me/
    ├── topics/
    │   ├── nowcasting.md                   # bootstrap 内容
    │   └── nowcasting.theses.md            # bootstrap 占位
    ├── feedback/
    │   └── research_digest_style.md
    ├── user/
    │   └── research_focus.md
    └── reading/
        └── 2026/                           # 由 collectors / digester 创建
```

**职责切分**：

| 文件 | 唯一职责 |
|---|---|
| `facts/db.py` | engine + session 工厂，**不**含 ORM 模型定义 |
| `facts/artifacts.py` | Artifact ORM（跨 topic），**不**做存储 IO |
| `topics/base.py` | Topic / Digester Protocol，**不**含具体实现 |
| `topics/registry.py` | 扫盘注册，**不**包含 collector 调用逻辑 |
| `topics/nowcasting/facts.py` | 该域 ORM 模型，**不**做 IO |
| `topics/nowcasting/collectors/*` | 写 facts 表，**不**触碰 memory / artifact |
| `topics/nowcasting/digester.py` | 读 facts + memory → 调 LLM → 写 artifact + .pending；**不**改 facts |
| `memory/lifecycle.py` | reindex MEMORY.md / 归档老 reading；**不**改文件内容（只移位 + 更新索引） |
| `memory/pending.py` | 写 .pending 草稿、accept/reject 移动；**不**做校验（lint 已存在） |
| `llm/client.py` | LLM 调用 + Langfuse trace；**不**构建 prompt |
| `llm/prompts.py` | prompt 构建（纯函数）；**不**做 IO |
| `artifacts/store.py` | MinIO put + PG index 双写；**不**含 digest 生成逻辑 |

---

## Conventions

- **TDD** — 每个 task 先写 failing test
- **Commits** — 每 task 一次；conventional commits (`feat:` / `chore:` / `test:` / `docs:`)
- **运行环境** — 测试默认从 `F:/codes/ISBE/` 跑；`uv run pytest` / `uv run radar`
- **Docker 服务依赖** — 集成测试 task 跑前需 `docker compose up -d`（具体 task 会写明）
- **Mock 边界** — 单元测试 mock httpx / anthropic；集成测试连真 Postgres + MinIO；smoke task 才连真 LLM
- **Pytest mark** — 集成测试标 `@pytest.mark.integration`，CI 默认跑全部，本地可 `uv run pytest -m "not integration"` 跳过
- **不 push** — 仅本地 commit；用户决定何时 push

---

## Task 1: Add P1 dependencies + Alembic skeleton

**Files:**
- Modify: `pyproject.toml`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako` (auto-generated by `alembic init`，留默认)
- Create: `src/isbe/facts/__init__.py`
- Create: `src/isbe/facts/db.py`

- [ ] **Step 1: Append P1 deps in `pyproject.toml`**

把 `dependencies` 段替换为：

```toml
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "pyyaml>=6.0",
    "python-frontmatter>=1.1",
    "prefect>=3.0",
    "httpx>=0.27",
    "sqlalchemy>=2.0",
    "alembic>=1.13",
    "psycopg[binary]>=3.2",
    "minio>=7.2",
    "anthropic>=0.40",
    "langfuse>=2.50,<3",
    "jinja2>=3.1",
    "feedparser>=6.0",
]
```

(`feedparser` 用于解析 arxiv RSS；`langfuse<3` 因为 compose 用 v2)

- [ ] **Step 2: `uv sync --all-extras`**

```bash
uv sync --all-extras
```

Expected: 所有新依赖装上。

- [ ] **Step 3: Init alembic**

```bash
uv run alembic init -t generic alembic
```

Expected: 创建 `alembic.ini` + `alembic/` 目录。

- [ ] **Step 4: Write `src/isbe/facts/__init__.py`**

```python
```

(empty)

- [ ] **Step 5: Write `src/isbe/facts/db.py`**

```python
import os

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


def db_url() -> str:
    user = os.getenv("POSTGRES_USER", "isbe")
    pw = os.getenv("POSTGRES_PASSWORD", "changeme")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "isbe")
    return f"postgresql+psycopg://{user}:{pw}@{host}:{port}/{db}"


metadata = MetaData()


class Base(DeclarativeBase):
    metadata = metadata


def make_engine():
    return create_engine(db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False, future=True)
```

- [ ] **Step 6: Replace `alembic/env.py`**

整体替换内容为：

```python
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from isbe.facts.db import db_url, metadata

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", db_url())
target_metadata = metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 7: Smoke check db url builds**

```bash
uv run python -c "from isbe.facts.db import db_url; print(db_url())"
```

Expected: `postgresql+psycopg://isbe:changeme@localhost:5432/isbe`

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock alembic.ini alembic/ src/isbe/facts/
git commit -m "chore(p1): add P1 deps + alembic skeleton + facts.db"
```

---

## Task 2: Common facts migration (artifacts + topic_runs)

**Files:**
- Create: `alembic/versions/001_common_facts.py`
- Create: `src/isbe/facts/artifacts.py`
- Test: `tests/test_facts_db.py`

`artifacts` 表对应 spec §1 第三层"产出/归档"；`topic_runs` 是 collector / digester 执行历史。

- [ ] **Step 1: Write the failing test**

`tests/test_facts_db.py`:

```python
import pytest

pytestmark = pytest.mark.integration

from sqlalchemy import inspect

from isbe.facts.db import make_engine


def test_artifacts_and_topic_runs_tables_exist():
    """After alembic upgrade head, both common tables exist."""
    eng = make_engine()
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    assert "artifacts" in tables
    assert "topic_runs" in tables
```

- [ ] **Step 2: Run test to verify it fails**

```bash
docker compose up -d postgres && uv run pytest tests/test_facts_db.py -v
```

Expected: FAIL — tables 不存在（migration 还没跑）。

- [ ] **Step 3: Write `src/isbe/facts/artifacts.py`**

```python
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # "weekly_digest" / "daily_digest" ...
    period_label: Mapped[str] = mapped_column(String(32))  # "2026-W19" / "2026-05-06"
    body_uri: Mapped[str] = mapped_column(String(512))  # MinIO key
    fingerprint: Mapped[dict] = mapped_column(JSON)  # facts ids / memory revs / trace_id
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class TopicRun(Base):
    __tablename__ = "topic_runs"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    topic_id: Mapped[str] = mapped_column(String(64), index=True)
    flow_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # "ok" / "failed"
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
```

- [ ] **Step 4: Generate alembic migration**

```bash
uv run alembic revision --autogenerate -m "common facts"
```

把生成的文件重命名为 `alembic/versions/001_common_facts.py`（删 hash 前缀，仅留 001 前缀以便有序）。

> 若 autogenerate 不识别（因 import 漏 isbe.facts.artifacts），手写 migration body：

```python
"""common facts

Revision ID: 001
Revises:
Create Date: 2026-05-07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", sa.String(64), nullable=False, index=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("period_label", sa.String(32), nullable=False),
        sa.Column("body_uri", sa.String(512), nullable=False),
        sa.Column("fingerprint", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_artifacts_topic_id", "artifacts", ["topic_id"])
    op.create_table(
        "topic_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("topic_id", sa.String(64), nullable=False, index=True),
        sa.Column("flow_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("topic_runs")
    op.drop_index("ix_artifacts_topic_id", table_name="artifacts")
    op.drop_table("artifacts")
```

- [ ] **Step 5: Apply migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade -> 001, common facts`

- [ ] **Step 6: Run test to verify pass**

```bash
uv run pytest tests/test_facts_db.py -v
```

Expected: PASS。

- [ ] **Step 7: Commit**

```bash
git add alembic/versions/001_common_facts.py src/isbe/facts/artifacts.py tests/test_facts_db.py
git commit -m "feat(facts): common artifacts + topic_runs schema"
```

---

## Task 3: Topic Protocol + Digester Protocol (TDD)

**Files:**
- Create: `src/isbe/topics/__init__.py`
- Create: `src/isbe/topics/base.py`
- Test: `tests/test_topics_base.py`

Spec ref: §2.1 Topic 接口、§2.2 Digester 三段约定。

- [ ] **Step 1: Write the failing test**

`tests/test_topics_base.py`:

```python
from datetime import datetime, timezone

from isbe.topics.base import (
    DigestResult,
    DigestSection,
    PendingMemoryDraft,
    TopicMetadata,
)


def test_topic_metadata_minimal():
    m = TopicMetadata(id="nowcasting", label="临近降水预报", cadence="weekly", active=True)
    assert m.id == "nowcasting"
    assert m.cadence == "weekly"


def test_digest_result_three_sections():
    sections = [
        DigestSection(kind="facts", body="本周 12 篇..."),
        DigestSection(kind="analysis", body="重点新增..."),
        DigestSection(kind="distillation", body="新论点候选..."),
    ]
    result = DigestResult(
        topic_id="nowcasting",
        period_label="2026-W19",
        generated_at=datetime.now(timezone.utc),
        sections=sections,
        fingerprint={"facts": [1, 2, 3], "memory": {"nowcasting": 1}},
        pending_drafts=[],
    )
    assert {s.kind for s in result.sections} == {"facts", "analysis", "distillation"}


def test_pending_memory_draft_shape():
    draft = PendingMemoryDraft(
        target_type="topic",
        target_path="topics/nowcasting.theses.md",
        body="新论点：...",
        rationale="本期新证据",
    )
    assert draft.target_type == "topic"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_topics_base.py -v
```

Expected: FAIL — `ModuleNotFoundError: isbe.topics.base`。

- [ ] **Step 3: Write `src/isbe/topics/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/isbe/topics/base.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

DigestSectionKind = Literal["facts", "analysis", "distillation"]


@dataclass(frozen=True)
class TopicMetadata:
    id: str
    label: str
    cadence: str  # "weekly" / "daily_after_close" / ...
    active: bool


@dataclass(frozen=True)
class DigestSection:
    kind: DigestSectionKind
    body: str  # markdown


@dataclass(frozen=True)
class PendingMemoryDraft:
    target_type: str  # "topic" / "feedback" / "user" / "reading" / "reference"
    target_path: str  # 相对 memory/<uid>/ 的路径，例 "topics/nowcasting.theses.md"
    body: str  # 完整的 markdown including frontmatter
    rationale: str  # 为什么 agent 提议这条草稿


@dataclass(frozen=True)
class DigestResult:
    topic_id: str
    period_label: str  # "2026-W19" or "2026-05-06"
    generated_at: datetime
    sections: list[DigestSection]
    fingerprint: dict  # {"facts": [...], "memory": {"name": rev}, "trace_id": "..."}
    pending_drafts: list[PendingMemoryDraft] = field(default_factory=list)


class Collector(Protocol):
    """A Prefect flow that writes facts to DB, returns count of new rows."""

    def __call__(self, *args, **kwargs) -> int:
        ...


class Digester(Protocol):
    """A Prefect flow that reads facts + memory → DigestResult."""

    def __call__(self, period_label: str) -> DigestResult:
        ...
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_topics_base.py -v
```

Expected: PASS（3 tests）。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/topics/__init__.py src/isbe/topics/base.py tests/test_topics_base.py
git commit -m "feat(topics): Protocol + dataclasses for Topic / Digester"
```

---

## Task 4: Topic registry (TDD)

**Files:**
- Create: `src/isbe/topics/registry.py`
- Test: `tests/test_topics_registry.py`

注册策略：扫 `src/isbe/topics/<id>/topic.yaml`。

- [ ] **Step 1: Write the failing test**

`tests/test_topics_registry.py`:

```python
from pathlib import Path

import pytest

from isbe.topics.registry import discover_topics


def test_discover_topics_reads_yaml(tmp_path: Path):
    pkg = tmp_path / "topics_pkg"
    (pkg / "alpha").mkdir(parents=True)
    (pkg / "alpha" / "topic.yaml").write_text(
        "id: alpha\nlabel: Alpha\ncadence: weekly\nactive: true\n",
        encoding="utf-8",
    )
    (pkg / "beta").mkdir()
    (pkg / "beta" / "topic.yaml").write_text(
        "id: beta\nlabel: Beta\ncadence: daily_after_close\nactive: false\n",
        encoding="utf-8",
    )
    (pkg / "_not_a_topic").mkdir()  # 没 topic.yaml，跳过
    topics = discover_topics(pkg)
    ids = {t.id for t in topics}
    assert ids == {"alpha", "beta"}
    alpha = next(t for t in topics if t.id == "alpha")
    assert alpha.cadence == "weekly"
    assert alpha.active is True


def test_discover_topics_empty_when_no_dir(tmp_path: Path):
    assert discover_topics(tmp_path / "missing") == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_topics_registry.py -v
```

Expected: FAIL — `ModuleNotFoundError: isbe.topics.registry`。

- [ ] **Step 3: Implement `src/isbe/topics/registry.py`**

```python
from pathlib import Path

import yaml

from isbe.topics.base import TopicMetadata


def discover_topics(root: Path) -> list[TopicMetadata]:
    """Scan root for <id>/topic.yaml files."""
    if not root.exists():
        return []
    out: list[TopicMetadata] = []
    for sub in sorted(p for p in root.iterdir() if p.is_dir()):
        manifest = sub / "topic.yaml"
        if not manifest.exists():
            continue
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        out.append(
            TopicMetadata(
                id=raw["id"],
                label=raw["label"],
                cadence=raw["cadence"],
                active=bool(raw.get("active", True)),
            )
        )
    return out


def default_topics_root() -> Path:
    """Return the in-tree topics package dir (src/isbe/topics)."""
    return Path(__file__).parent
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_topics_registry.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/topics/registry.py tests/test_topics_registry.py
git commit -m "feat(topics): registry that scans <id>/topic.yaml"
```

---

## Task 5: Memory lifecycle — reindex MEMORY.md (TDD)

**Files:**
- Create: `src/isbe/memory/lifecycle.py`
- Test: `tests/test_memory_lifecycle.py`

Spec ref: §2.4。MEMORY.md 是派生物（spec §4.2），由 reindex 工具维护，每行 `- [name](path) — description` 格式。

- [ ] **Step 1: Write the failing tests**

`tests/test_memory_lifecycle.py`（追加，本 task 只写 reindex 部分）：

```python
from pathlib import Path

from isbe.memory.lifecycle import reindex_memory_md


def test_reindex_writes_one_line_per_file(memory_dir: Path, sample_feedback_file: Path):
    extra = memory_dir / "user" / "research_focus.md"
    extra.write_text(
        """---
name: research_focus
description: 我在写关于 nowcasting 的论文
type: user
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
focus body""",
        encoding="utf-8",
    )
    reindex_memory_md(memory_dir)
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "feedback/digest_style.md" in content
    assert "user/research_focus.md" in content
    assert "用户偏好的日报风格" in content


def test_reindex_skips_pending_and_audit_and_archive(memory_dir: Path, sample_feedback_file: Path):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "x.md").write_text(
        """---
name: x
description: should be excluded
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    archive = memory_dir / "reading" / ".archive" / "2025" / "W01"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text(
        """---
name: old
description: archived reading
type: reading
created: 2025-01-01
updated: 2025-01-01
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    reindex_memory_md(memory_dir)
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "should be excluded" not in content
    assert "archived reading" not in content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory_lifecycle.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement `src/isbe/memory/lifecycle.py`**

```python
from pathlib import Path

from isbe.memory.loader import EXCLUDED_DIRS, load_index

ARCHIVE_DIR = ".archive"


def reindex_memory_md(memory_root: Path) -> None:
    """Rewrite memory_root/MEMORY.md from current in-tree files.

    Excludes .pending, .audit, .archive directories.
    Active reading entries (not archived) are included.
    """
    entries = load_index(memory_root)
    # extra exclusion: .archive (load_index already excludes .pending/.audit)
    entries = [e for e in entries if ARCHIVE_DIR not in e.path.parts]
    lines = ["# MEMORY index (auto-generated by lifecycle.reindex_memory_md)\n", ""]
    for e in sorted(entries, key=lambda x: str(x.path)):
        rel = e.path.relative_to(memory_root).as_posix()
        lines.append(f"- [{e.frontmatter.name}]({rel}) — {e.frontmatter.description}")
    (memory_root / "MEMORY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_lifecycle.py -v
```

Expected: PASS（2 tests）。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/memory/lifecycle.py tests/test_memory_lifecycle.py
git commit -m "feat(memory): reindex MEMORY.md from current in-tree files"
```

---

## Task 6: Memory lifecycle — archive old reading (TDD)

**Files:**
- Modify: `src/isbe/memory/lifecycle.py`
- Modify: `tests/test_memory_lifecycle.py`

Spec ref: §2.4 — 周龄 > 8 周自动归档；归档目录 `reading/.archive/<YYYY>/W##/`。

- [ ] **Step 1: Append failing test**

在 `tests/test_memory_lifecycle.py` 顶部加 import：

```python
from datetime import date

from isbe.memory.lifecycle import archive_old_reading
```

追加 test：

```python
def test_archive_moves_files_older_than_8_weeks(memory_dir: Path):
    # Create reading/2026/W10/<id>.md with updated=2026-03-09 (周一)
    week_dir = memory_dir / "reading" / "2026" / "W10"
    week_dir.mkdir(parents=True)
    old = week_dir / "old_paper.md"
    old.write_text(
        """---
name: old_paper
description: x
type: reading
created: 2026-03-09
updated: 2026-03-09
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    # 假装"今天"是 2026-05-15（W19）即 W10+9周 = 应归档
    moved = archive_old_reading(memory_root=memory_dir, today=date(2026, 5, 15), age_weeks=8)
    assert moved == 1
    assert not old.exists()
    archived = memory_dir / "reading" / ".archive" / "2026" / "W10" / "old_paper.md"
    assert archived.exists()


def test_archive_leaves_recent_files(memory_dir: Path):
    week_dir = memory_dir / "reading" / "2026" / "W18"
    week_dir.mkdir(parents=True)
    recent = week_dir / "recent_paper.md"
    recent.write_text(
        """---
name: recent_paper
description: x
type: reading
created: 2026-05-04
updated: 2026-05-04
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    moved = archive_old_reading(memory_root=memory_dir, today=date(2026, 5, 15), age_weeks=8)
    assert moved == 0
    assert recent.exists()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_memory_lifecycle.py::test_archive_moves_files_older_than_8_weeks -v
```

Expected: FAIL — `ImportError: archive_old_reading`。

- [ ] **Step 3: Add `archive_old_reading` to `src/isbe/memory/lifecycle.py`**

末尾追加：

```python
import re
import shutil
from datetime import date, timedelta

WEEK_DIR_RE = re.compile(r"^W(\d{2})$")


def _iso_week_dirs(reading_root: Path):
    """Yield (year:int, week:int, path) for reading_root/<YYYY>/W## dirs."""
    if not reading_root.exists():
        return
    for year_dir in reading_root.iterdir():
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        if year_dir.name == ARCHIVE_DIR:
            continue
        for week_dir in year_dir.iterdir():
            if not week_dir.is_dir():
                continue
            m = WEEK_DIR_RE.match(week_dir.name)
            if not m:
                continue
            yield int(year_dir.name), int(m.group(1)), week_dir


def archive_old_reading(memory_root: Path, today: date, age_weeks: int = 8) -> int:
    """Move reading/<YYYY>/W##/* dirs older than age_weeks into reading/.archive/<YYYY>/W##/.

    Returns the number of files moved.
    """
    reading_root = memory_root / "reading"
    archive_root = reading_root / ARCHIVE_DIR
    cutoff = today - timedelta(weeks=age_weeks)
    cutoff_year, cutoff_week, _ = cutoff.isocalendar()
    moved = 0
    for year, week, week_dir in list(_iso_week_dirs(reading_root)):
        if (year, week) >= (cutoff_year, cutoff_week):
            continue
        dest_dir = archive_root / str(year) / f"W{week:02d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for f in week_dir.iterdir():
            if f.is_file():
                shutil.move(str(f), str(dest_dir / f.name))
                moved += 1
        # remove empty source week dir
        try:
            week_dir.rmdir()
        except OSError:
            pass
    return moved
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_lifecycle.py -v
```

Expected: 4 tests PASS（含 reindex 2 + archive 2）。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/memory/lifecycle.py tests/test_memory_lifecycle.py
git commit -m "feat(memory): archive reading entries older than 8 weeks"
```

---

## Task 7: Memory pending/accept/reject (TDD)

**Files:**
- Create: `src/isbe/memory/pending.py`
- Test: `tests/test_memory_pending.py`

Spec ref: §2.3 review path。`.pending/` 是 agent 草稿；review 流移入正式目录或 `.audit/rejected/`。

- [ ] **Step 1: Write the failing test**

`tests/test_memory_pending.py`:

```python
from pathlib import Path

from isbe.memory.pending import (
    accept_pending,
    list_pending,
    reject_pending,
    write_pending,
)
from isbe.topics.base import PendingMemoryDraft


def _draft(target_path: str = "topics/nowcasting.theses.md") -> PendingMemoryDraft:
    return PendingMemoryDraft(
        target_type="topic",
        target_path=target_path,
        body="""---
name: nowcasting.theses
description: bull/bear theses for nowcasting
type: topic
created: 2026-05-07
updated: 2026-05-07
source: agent-inferred
---
新论点：diffusion 在 lead-time>90min 仍 mode-collapse""",
        rationale="本期新证据触发",
    )


def test_write_pending_creates_file_under_pending(memory_dir: Path):
    draft = _draft()
    p = write_pending(memory_dir, draft)
    assert p.exists()
    assert p.is_relative_to(memory_dir / ".pending")
    assert p.parts[-2:] == ("topics", "nowcasting.theses.md")


def test_list_pending_returns_all_drafts(memory_dir: Path):
    write_pending(memory_dir, _draft("topics/a.md"))
    write_pending(memory_dir, _draft("feedback/b.md"))
    drafts = list_pending(memory_dir)
    assert len(drafts) == 2


def test_accept_moves_to_real_dir(memory_dir: Path):
    draft = _draft()
    pend = write_pending(memory_dir, draft)
    accepted = accept_pending(memory_dir, pend)
    assert not pend.exists()
    assert accepted.exists()
    assert accepted.parts[-2:] == ("topics", "nowcasting.theses.md")
    assert ".pending" not in accepted.parts


def test_reject_moves_to_audit(memory_dir: Path):
    draft = _draft()
    pend = write_pending(memory_dir, draft)
    rejected = reject_pending(memory_dir, pend)
    assert not pend.exists()
    assert rejected.exists()
    assert ".audit" in rejected.parts and "rejected" in rejected.parts
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_memory_pending.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement `src/isbe/memory/pending.py`**

```python
import shutil
from pathlib import Path

from isbe.topics.base import PendingMemoryDraft

PENDING = ".pending"
AUDIT_REJECTED = (".audit", "rejected")


def write_pending(memory_root: Path, draft: PendingMemoryDraft) -> Path:
    target = memory_root / PENDING / draft.target_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(draft.body, encoding="utf-8")
    return target


def list_pending(memory_root: Path) -> list[Path]:
    pending_root = memory_root / PENDING
    if not pending_root.exists():
        return []
    return sorted(p for p in pending_root.rglob("*.md") if p.is_file())


def accept_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = pending_path.relative_to(memory_root / PENDING)
    target = memory_root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target


def reject_pending(memory_root: Path, pending_path: Path) -> Path:
    rel = pending_path.relative_to(memory_root / PENDING)
    target = memory_root / AUDIT_REJECTED[0] / AUDIT_REJECTED[1] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pending_path), str(target))
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_pending.py -v
```

Expected: 4 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/memory/pending.py tests/test_memory_pending.py
git commit -m "feat(memory): pending drafts + accept/reject workflow"
```

---

## Task 8: CLI `radar memory reindex|archive` (TDD)

**Files:**
- Create: `src/isbe/cli/memory_cmd.py`
- Modify: `src/isbe/cli/main.py`
- Test: `tests/test_cli_memory_cmd.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_memory_cmd.py`:

```python
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_memory_reindex_writes_index(memory_dir: Path, sample_feedback_file: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["memory", "reindex"])
    assert result.exit_code == 0
    content = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "digest_style" in content


def test_radar_memory_archive_moves_old(memory_dir: Path, monkeypatch):
    week_dir = memory_dir / "reading" / "2026" / "W10"
    week_dir.mkdir(parents=True)
    (week_dir / "x.md").write_text(
        """---
name: x
description: x
type: reading
created: 2026-03-09
updated: 2026-03-09
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["memory", "archive", "--today", "2026-05-15"])
    assert result.exit_code == 0
    assert "1 file" in result.stdout
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_cli_memory_cmd.py -v
```

Expected: FAIL — `No such command 'memory'`。

- [ ] **Step 3: Implement `src/isbe/cli/memory_cmd.py`**

```python
import os
from datetime import date
from pathlib import Path

import typer

from isbe.memory.lifecycle import archive_old_reading, reindex_memory_md

memory_app = typer.Typer(help="Memory 维护命令（索引、归档）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@memory_app.command("reindex")
def reindex() -> None:
    """Rewrite MEMORY.md from current in-tree files."""
    root = _memory_root()
    reindex_memory_md(root)
    typer.echo(f"reindexed {root / 'MEMORY.md'}")


@memory_app.command("archive")
def archive(
    today: str = typer.Option(None, help="ISO date (YYYY-MM-DD); defaults to today"),
    age_weeks: int = typer.Option(8, help="Archive entries older than N weeks"),
) -> None:
    """Archive old reading/ entries to reading/.archive/."""
    root = _memory_root()
    today_d = date.fromisoformat(today) if today else date.today()
    moved = archive_old_reading(root, today=today_d, age_weeks=age_weeks)
    typer.echo(f"{moved} file(s) archived")
```

- [ ] **Step 4: Wire into `src/isbe/cli/main.py`**

替换内容为：

```python
import typer

from isbe.cli.memory_cmd import memory_app
from isbe.cli.review import review_app

app = typer.Typer(help="ISBE radar — 自我成长情报雷达 CLI。")
app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_cli_memory_cmd.py -v
```

Expected: 2 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/cli/memory_cmd.py src/isbe/cli/main.py tests/test_cli_memory_cmd.py
git commit -m "feat(cli): radar memory reindex/archive"
```

---

## Task 9: Real `radar review memory` implementation (TDD)

**Files:**
- Modify: `src/isbe/cli/review.py`
- Replace: `tests/test_cli_review.py`

P0 写的 review CLI 是占位（只列文件 + 计数）。本 task 实化为 accept/reject 循环。

- [ ] **Step 1: Replace `tests/test_cli_review.py` 整体**

```python
from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def _put_pending(memory_dir: Path, name: str, body_extra: str = "body"):
    p = memory_dir / ".pending" / "feedback" / f"{name}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        f"""---
name: {name}
description: agent draft
type: feedback
created: 2026-05-07
updated: 2026-05-07
source: agent-inferred
---
{body_extra}""",
        encoding="utf-8",
    )
    return p


def test_review_memory_lists_when_no_action(memory_dir: Path, monkeypatch):
    _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "x.md" in result.stdout
    assert "1 pending" in result.stdout


def test_review_memory_empty_when_nothing_pending(memory_dir: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "0 pending" in result.stdout


def test_review_memory_accept_moves_file(memory_dir: Path, monkeypatch):
    pend = _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory", "--accept", "feedback/x.md"])
    assert result.exit_code == 0
    assert not pend.exists()
    assert (memory_dir / "feedback" / "x.md").exists()


def test_review_memory_reject_moves_file(memory_dir: Path, monkeypatch):
    pend = _put_pending(memory_dir, "x")
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory", "--reject", "feedback/x.md"])
    assert result.exit_code == 0
    assert not pend.exists()
    assert (memory_dir / ".audit" / "rejected" / "feedback" / "x.md").exists()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_cli_review.py -v
```

Expected: 2 new tests FAIL — `--accept` / `--reject` 不识别。

- [ ] **Step 3: Replace `src/isbe/cli/review.py`**

```python
import os
from pathlib import Path

import typer

from isbe.memory.pending import accept_pending, list_pending, reject_pending

review_app = typer.Typer(help="审核 agent 提议的草稿（memory / tools / workflows）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@review_app.command("memory")
def review_memory(
    accept: str = typer.Option(None, "--accept", help="Accept a pending draft (relative path under .pending/)"),
    reject: str = typer.Option(None, "--reject", help="Reject a pending draft"),
) -> None:
    """Review pending memory drafts; without flags, list all pending."""
    root = _memory_root()
    pending_root = root / ".pending"

    if accept:
        target = pending_root / accept
        if not target.exists():
            typer.echo(f"no such pending: {accept}", err=True)
            raise typer.Exit(code=1)
        moved = accept_pending(root, target)
        typer.echo(f"accepted -> {moved.relative_to(root)}")
        return

    if reject:
        target = pending_root / reject
        if not target.exists():
            typer.echo(f"no such pending: {reject}", err=True)
            raise typer.Exit(code=1)
        moved = reject_pending(root, target)
        typer.echo(f"rejected -> {moved.relative_to(root)}")
        return

    files = list_pending(root)
    for p in files:
        typer.echo(str(p.relative_to(root)))
    typer.echo(f"{len(files)} pending")


@review_app.command("tools")
def review_tools() -> None:
    """List pending skill drafts (P1 placeholder; wired up in P3)."""
    typer.echo("(not implemented in P1)")
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_cli_review.py -v
```

Expected: 4 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/cli/review.py tests/test_cli_review.py
git commit -m "feat(cli): real radar review memory with --accept/--reject"
```

---

## Task 10: Nowcasting fact models + migration (TDD)

**Files:**
- Create: `src/isbe/topics/nowcasting/__init__.py`
- Create: `src/isbe/topics/nowcasting/topic.yaml`
- Create: `src/isbe/topics/nowcasting/facts.py`
- Create: `alembic/versions/002_nowcasting.py`
- Test: `tests/test_nowcasting_facts.py`

- [ ] **Step 1: Write the failing test**

`tests/test_nowcasting_facts.py`:

```python
import pytest

pytestmark = pytest.mark.integration

from datetime import datetime, timezone

from sqlalchemy import inspect, select

from isbe.facts.db import make_engine, make_session_factory
from isbe.topics.nowcasting.facts import Event, Paper, Repo


def test_tables_created():
    insp = inspect(make_engine())
    tables = set(insp.get_table_names())
    assert {"papers", "repos", "events"} <= tables


def test_paper_roundtrip():
    Session = make_session_factory()
    with Session() as s:
        p = Paper(
            arxiv_id="2604.12345",
            title="Test paper",
            authors=["Alice", "Bob"],
            abstract="abstract body",
            primary_category="cs.LG",
            submitted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            pdf_uri="minio://papers/2604.12345.pdf",
            source_url="https://arxiv.org/abs/2604.12345",
        )
        s.add(p)
        s.commit()
        got = s.scalar(select(Paper).where(Paper.arxiv_id == "2604.12345"))
        assert got is not None
        assert got.authors == ["Alice", "Bob"]
        s.delete(got)
        s.commit()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_nowcasting_facts.py -v
```

Expected: FAIL — `ModuleNotFoundError` 或 `tables not created`。

- [ ] **Step 3: Write `src/isbe/topics/nowcasting/__init__.py`**

```python
```

- [ ] **Step 4: Write `src/isbe/topics/nowcasting/topic.yaml`**

```yaml
id: nowcasting
label: 临近降水预报科研订阅
cadence: weekly
active: true
```

- [ ] **Step 5: Write `src/isbe/topics/nowcasting/facts.py`**

```python
from datetime import datetime

from sqlalchemy import JSON, ARRAY, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class Paper(Base):
    __tablename__ = "papers"

    arxiv_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    authors: Mapped[list[str]] = mapped_column(ARRAY(String))
    abstract: Mapped[str] = mapped_column(Text)
    primary_category: Mapped[str] = mapped_column(String(32))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    pdf_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_url: Mapped[str] = mapped_column(String(512))


class Repo(Base):
    __tablename__ = "repos"

    github_url: Mapped[str] = mapped_column(String(512), primary_key=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    stars: Mapped[int] = mapped_column(Integer, default=0)
    last_commit_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_release_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_paper_ids: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32), index=True)  # preprint / repo_update / blog_post / conf_accept
    payload: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64))
```

- [ ] **Step 6: Generate alembic migration**

```bash
uv run alembic revision --autogenerate -m "nowcasting facts"
```

把生成文件移名为 `alembic/versions/002_nowcasting.py`，确保 `down_revision = "001"`。若 autogenerate 不完整（ARRAY 类型常被漏），手写 body：

```python
"""nowcasting facts

Revision ID: 002
Revises: 001
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "papers",
        sa.Column("arxiv_id", sa.String(32), primary_key=True),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("authors", sa.ARRAY(sa.String), nullable=False),
        sa.Column("abstract", sa.Text, nullable=False),
        sa.Column("primary_category", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pdf_uri", sa.String(512), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=False),
    )
    op.create_index("ix_papers_submitted_at", "papers", ["submitted_at"])
    op.create_table(
        "repos",
        sa.Column("github_url", sa.String(512), primary_key=True),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("stars", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_commit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_release_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("linked_paper_ids", sa.ARRAY(sa.String), nullable=False, server_default="{}"),
    )
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
    )
    op.create_index("ix_events_type", "events", ["type"])
    op.create_index("ix_events_observed_at", "events", ["observed_at"])


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("repos")
    op.drop_table("papers")
```

- [ ] **Step 7: Apply migration**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 001 -> 002, nowcasting facts`。

- [ ] **Step 8: Run test to verify pass**

```bash
uv run pytest tests/test_nowcasting_facts.py -v
```

Expected: PASS。

- [ ] **Step 9: Commit**

```bash
git add src/isbe/topics/nowcasting/__init__.py src/isbe/topics/nowcasting/topic.yaml src/isbe/topics/nowcasting/facts.py alembic/versions/002_nowcasting.py tests/test_nowcasting_facts.py
git commit -m "feat(nowcasting): facts ORM + migration (papers/repos/events)"
```

---

## Task 11: Nowcasting arxiv collector flow (TDD)

**Files:**
- Create: `src/isbe/topics/nowcasting/collectors/__init__.py`
- Create: `src/isbe/topics/nowcasting/collectors/arxiv.py`
- Test: `tests/test_nowcasting_arxiv_collector.py`

arxiv 提供 RSS-style listing；用 feedparser 解析。

- [ ] **Step 1: Write the failing test**

`tests/test_nowcasting_arxiv_collector.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock

from isbe.topics.nowcasting.collectors.arxiv import (
    parse_atom_entry,
    upsert_papers,
)


SAMPLE_ATOM_ENTRY = {
    "id": "http://arxiv.org/abs/2604.12345v1",
    "title": "Diffusion-based Nowcasting via Radar Conditioning",
    "summary": "We present a new method...",
    "authors": [{"name": "Alice"}, {"name": "Bob"}],
    "published": "2026-04-30T12:00:00Z",
    "updated": "2026-05-01T08:00:00Z",
    "tags": [{"term": "cs.LG"}, {"term": "physics.ao-ph"}],
    "links": [{"rel": "alternate", "href": "https://arxiv.org/abs/2604.12345"}],
}


def test_parse_atom_entry_extracts_fields():
    paper = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    assert paper.arxiv_id == "2604.12345"
    assert paper.authors == ["Alice", "Bob"]
    assert paper.primary_category == "cs.LG"
    assert paper.submitted_at == datetime(2026, 4, 30, 12, 0, tzinfo=timezone.utc)


def test_upsert_papers_inserts_new_and_skips_dup():
    session = MagicMock()
    # Simulate "Alice" paper already exists for second call
    session.get.side_effect = [None, MagicMock()]  # first: None; second: existing
    paper1 = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    paper2 = parse_atom_entry(SAMPLE_ATOM_ENTRY)
    n_new = upsert_papers(session, [paper1, paper2])
    assert n_new == 1
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_nowcasting_arxiv_collector.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement collectors `__init__.py` (empty) + `arxiv.py`**

`src/isbe/topics/nowcasting/collectors/__init__.py`:

```python
```

`src/isbe/topics/nowcasting/collectors/arxiv.py`:

```python
"""arxiv collector — fetches recent papers in target categories."""

from datetime import datetime, timezone

import feedparser
import httpx
from prefect import flow, task
from sqlalchemy.orm import Session

from isbe.facts.db import make_session_factory
from isbe.topics.nowcasting.facts import Paper

ARXIV_QUERY_URL = (
    "http://export.arxiv.org/api/query"
    "?search_query=cat:cs.LG+AND+(abs:nowcasting+OR+abs:precipitation+OR+abs:radar)"
    "&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
)


def _parse_iso(s: str) -> datetime:
    # arxiv uses ISO with 'Z'
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_atom_entry(entry: dict) -> Paper:
    """Parse one feedparser entry dict into a Paper ORM object (not yet attached to session)."""
    raw_id = entry["id"]  # "http://arxiv.org/abs/2604.12345v1"
    arxiv_id = raw_id.rsplit("/", 1)[-1].split("v")[0]
    authors = [a["name"] for a in entry.get("authors", [])]
    tags = entry.get("tags", [])
    primary = tags[0]["term"] if tags else "unknown"
    alt = next((l["href"] for l in entry.get("links", []) if l.get("rel") == "alternate"), "")
    return Paper(
        arxiv_id=arxiv_id,
        title=entry["title"].strip(),
        authors=authors,
        abstract=entry.get("summary", "").strip(),
        primary_category=primary,
        submitted_at=_parse_iso(entry["published"]),
        updated_at=_parse_iso(entry.get("updated", entry["published"])),
        pdf_uri=None,  # P1 不下载 PDF
        source_url=alt or raw_id,
    )


def upsert_papers(session: Session, papers: list[Paper]) -> int:
    """Insert papers that don't exist yet. Returns count of inserts."""
    n = 0
    for p in papers:
        existing = session.get(Paper, p.arxiv_id)
        if existing is None:
            session.add(p)
            n += 1
    session.commit()
    return n


@task
def fetch_arxiv_atom(max_results: int = 50) -> list[dict]:
    url = ARXIV_QUERY_URL.format(max_results=max_results)
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    parsed = feedparser.parse(resp.text)
    return parsed.entries  # list of dicts


@flow(name="arxiv-collector")
def arxiv_collector(max_results: int = 50) -> int:
    entries = fetch_arxiv_atom(max_results)
    papers = [parse_atom_entry(e) for e in entries]
    Session = make_session_factory()
    with Session() as s:
        n = upsert_papers(s, papers)
    return n


if __name__ == "__main__":
    print(arxiv_collector(max_results=10))
```

- [ ] **Step 4: Run test to verify pass**

```bash
uv run pytest tests/test_nowcasting_arxiv_collector.py -v
```

Expected: 2 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/topics/nowcasting/collectors/ tests/test_nowcasting_arxiv_collector.py
git commit -m "feat(nowcasting): arxiv collector flow"
```

---

## Task 12: Nowcasting GitHub repo collector (TDD)

**Files:**
- Create: `src/isbe/topics/nowcasting/collectors/github.py`
- Test: `tests/test_nowcasting_github_collector.py`

跟踪一个白名单仓库列表（在 topic.yaml 之外存到代码常量；P3 做配置化）。MVP 版本：硬编码 1-2 个 repo（NowcastNet、DGMR 类）。

- [ ] **Step 1: Write the failing test**

`tests/test_nowcasting_github_collector.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from isbe.topics.nowcasting.collectors.github import (
    TRACKED_REPOS,
    fetch_repo_meta,
    upsert_repo,
)
from isbe.topics.nowcasting.facts import Repo


def test_tracked_repos_nonempty():
    assert len(TRACKED_REPOS) >= 1


def test_fetch_repo_meta_parses_response():
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "html_url": "https://github.com/foo/bar",
        "name": "bar",
        "description": "Nowcasting toy",
        "stargazers_count": 42,
        "pushed_at": "2026-05-01T10:00:00Z",
    }
    with patch("isbe.topics.nowcasting.collectors.github.httpx.get", return_value=fake_resp):
        repo = fetch_repo_meta("foo/bar")
    assert isinstance(repo, Repo)
    assert repo.stars == 42
    assert repo.last_commit_at == datetime(2026, 5, 1, 10, 0, tzinfo=timezone.utc)


def test_upsert_repo_inserts_or_updates():
    session = MagicMock()
    session.get.return_value = None  # not exist
    r = Repo(
        github_url="https://github.com/foo/bar",
        title="bar",
        description=None,
        stars=10,
        last_commit_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        last_release_at=None,
        linked_paper_ids=[],
    )
    inserted = upsert_repo(session, r)
    assert inserted is True
    session.add.assert_called_once_with(r)
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_nowcasting_github_collector.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement `src/isbe/topics/nowcasting/collectors/github.py`**

```python
"""github collector — tracks a hardcoded list of nowcasting-related repos."""

from datetime import datetime, timezone

import httpx
from prefect import flow, task
from sqlalchemy.orm import Session

from isbe.facts.db import make_session_factory
from isbe.topics.nowcasting.facts import Repo

TRACKED_REPOS: list[str] = [
    # owner/name; expand by editing this list (P3 makes it config)
    "openclimatefix/skillful_nowcasting",
    "google-research/google-research",  # placeholder; many subdirs
]


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def fetch_repo_meta(owner_repo: str) -> Repo:
    """GET https://api.github.com/repos/<owner>/<name> → Repo (not attached)."""
    resp = httpx.get(f"https://api.github.com/repos/{owner_repo}", timeout=30.0)
    resp.raise_for_status()
    data = resp.json()
    return Repo(
        github_url=data["html_url"],
        title=data["name"],
        description=data.get("description"),
        stars=int(data["stargazers_count"]),
        last_commit_at=_parse_iso(data["pushed_at"]) if data.get("pushed_at") else None,
        last_release_at=None,  # release endpoint is separate; skip in MVP
        linked_paper_ids=[],
    )


def upsert_repo(session: Session, repo: Repo) -> bool:
    """Insert or update. Returns True if inserted, False if updated."""
    existing = session.get(Repo, repo.github_url)
    if existing is None:
        session.add(repo)
        session.commit()
        return True
    existing.title = repo.title
    existing.description = repo.description
    existing.stars = repo.stars
    existing.last_commit_at = repo.last_commit_at
    session.commit()
    return False


@task
def fetch_one(owner_repo: str) -> Repo:
    return fetch_repo_meta(owner_repo)


@flow(name="github-collector")
def github_collector() -> int:
    Session = make_session_factory()
    n_new = 0
    with Session() as s:
        for owner_repo in TRACKED_REPOS:
            try:
                r = fetch_one(owner_repo)
                if upsert_repo(s, r):
                    n_new += 1
            except httpx.HTTPError as e:
                print(f"skip {owner_repo}: {e}")
    return n_new


if __name__ == "__main__":
    print(github_collector())
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_nowcasting_github_collector.py -v
```

Expected: 3 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add src/isbe/topics/nowcasting/collectors/github.py tests/test_nowcasting_github_collector.py
git commit -m "feat(nowcasting): github repo collector flow"
```

---

## Task 13: LLM client with Langfuse tracing (TDD)

**Files:**
- Create: `src/isbe/llm/__init__.py`
- Create: `src/isbe/llm/client.py`
- Create: `src/isbe/llm/prompts.py`
- Test: `tests/test_llm_client.py`

P1 锁定 anthropic claude-sonnet-4-6（按 system 信息）；如 ANTHROPIC_API_KEY 空，client 在 init 时不报错，仅在调用时报错——便于 unit test mock。

- [ ] **Step 1: Write the failing test**

`tests/test_llm_client.py`:

```python
from unittest.mock import MagicMock, patch

from isbe.llm.client import LLMResponse, complete
from isbe.llm.prompts import build_digest_prompt


def test_build_digest_prompt_includes_facts_and_memory():
    prompt = build_digest_prompt(
        topic_label="Nowcasting",
        period_label="2026-W19",
        facts_block="paper1 / paper2",
        memory_block="topic@rev1: keywords=...",
    )
    assert "Nowcasting" in prompt
    assert "2026-W19" in prompt
    assert "paper1" in prompt
    assert "topic@rev1" in prompt
    # 三段约定
    assert "事实" in prompt or "facts" in prompt.lower()
    assert "分析" in prompt or "analysis" in prompt.lower()
    assert "蒸馏" in prompt or "distillation" in prompt.lower()


def test_complete_calls_anthropic_and_returns_text():
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(text="generated text")]
    fake_msg.id = "msg_123"
    fake_msg.usage = MagicMock(input_tokens=10, output_tokens=5)
    fake_client = MagicMock()
    fake_client.messages.create.return_value = fake_msg
    with patch("isbe.llm.client._get_anthropic_client", return_value=fake_client):
        resp = complete(
            system="sys prompt", user="user prompt",
            model="claude-sonnet-4-6", trace_id="t1",
        )
    assert isinstance(resp, LLMResponse)
    assert resp.text == "generated text"
    assert resp.message_id == "msg_123"
    assert resp.input_tokens == 10
    fake_client.messages.create.assert_called_once()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Write `src/isbe/llm/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Implement `src/isbe/llm/prompts.py`**

```python
SYSTEM_PROMPT = """你是 ISBE 的 digest 助手。

输出严格分三段，用 markdown level-2 标题分隔（顺序固定）：

## 事实
当周期内 facts 的客观摘要（数字、事件、列表）；不做判断、不做推断。

## 分析
基于 facts × memory 的当期判断；引用所用 memory 条目时用 (memory: name@rev) 标注。

## 蒸馏
本期产出中应进 memory 的候选；每条单独一行，前缀 `- DRAFT[<target_path>]:` 然后内容。

不要输出三段以外的任何内容（包括前后致辞、总结、emoji）。
"""

USER_TEMPLATE = """主题：{topic_label}
周期：{period_label}

=== Facts (本周期) ===
{facts_block}

=== Memory (当前) ===
{memory_block}

请按 system 指令输出三段。"""


def build_digest_prompt(
    *, topic_label: str, period_label: str, facts_block: str, memory_block: str
) -> str:
    """Returns the user-message body. system prompt is constant SYSTEM_PROMPT."""
    return USER_TEMPLATE.format(
        topic_label=topic_label,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
```

- [ ] **Step 5: Implement `src/isbe/llm/client.py`**

```python
import os
from dataclasses import dataclass
from functools import lru_cache

from anthropic import Anthropic

DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    message_id: str
    input_tokens: int
    output_tokens: int
    trace_id: str | None


@lru_cache(maxsize=1)
def _get_anthropic_client() -> Anthropic:
    return Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


def complete(
    *, system: str, user: str, model: str = DEFAULT_MODEL, trace_id: str | None = None,
    max_tokens: int = 4096,
) -> LLMResponse:
    client = _get_anthropic_client()
    msg = client.messages.create(
        model=model,
        system=system,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in msg.content if hasattr(block, "text"))
    return LLMResponse(
        text=text,
        message_id=msg.id,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        trace_id=trace_id,
    )
```

> Langfuse 追踪在 P1 用 SDK 的 `@observe` 装饰器走，但本 task 不集成（避免在 unit test 拉 langfuse client）。Task 16 集成测试时再加 trace 上下文。

- [ ] **Step 6: Run tests to verify pass**

```bash
uv run pytest tests/test_llm_client.py -v
```

Expected: 2 tests PASS。

- [ ] **Step 7: Commit**

```bash
git add src/isbe/llm/ tests/test_llm_client.py
git commit -m "feat(llm): anthropic client + digest prompt builder"
```

---

## Task 14: Artifact store (MinIO + PG index, TDD)

**Files:**
- Create: `src/isbe/artifacts/__init__.py`
- Create: `src/isbe/artifacts/store.py`
- Test: `tests/test_artifacts_store.py`

- [ ] **Step 1: Write the failing test**

`tests/test_artifacts_store.py`:

```python
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from isbe.artifacts.store import save_artifact


def test_save_artifact_writes_minio_and_pg(monkeypatch):
    fake_minio = MagicMock()
    fake_session = MagicMock()
    fake_session_factory = MagicMock(return_value=fake_session)
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)

    with patch("isbe.artifacts.store._get_minio_client", return_value=fake_minio), \
         patch("isbe.artifacts.store.make_session_factory", return_value=fake_session_factory):
        artifact_id = save_artifact(
            topic_id="nowcasting",
            kind="weekly_digest",
            period_label="2026-W19",
            body_markdown="# Test\nbody",
            fingerprint={"facts": [1, 2], "memory": {"a": 1}, "trace_id": "t1"},
            generated_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        )
    assert artifact_id is not None
    fake_minio.put_object.assert_called_once()
    fake_session.add.assert_called_once()
    fake_session.commit.assert_called_once()
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_artifacts_store.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Write `src/isbe/artifacts/__init__.py`** (empty)

```python
```

- [ ] **Step 4: Implement `src/isbe/artifacts/store.py`**

```python
import io
import os
from datetime import datetime
from functools import lru_cache
from uuid import UUID, uuid4

from minio import Minio

from isbe.facts.artifacts import Artifact
from isbe.facts.db import make_session_factory

ARTIFACT_BUCKET = "isbe-artifacts"


@lru_cache(maxsize=1)
def _get_minio_client() -> Minio:
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ROOT_USER", "isbe"),
        secret_key=os.getenv("MINIO_ROOT_PASSWORD", "changeme123"),
        secure=False,
    )


def _ensure_bucket(client: Minio) -> None:
    if not client.bucket_exists(ARTIFACT_BUCKET):
        client.make_bucket(ARTIFACT_BUCKET)


def save_artifact(
    *,
    topic_id: str,
    kind: str,
    period_label: str,
    body_markdown: str,
    fingerprint: dict,
    generated_at: datetime,
) -> UUID:
    artifact_id = uuid4()
    body_bytes = body_markdown.encode("utf-8")
    object_name = f"{topic_id}/{period_label}/{artifact_id}.md"

    client = _get_minio_client()
    _ensure_bucket(client)
    client.put_object(
        ARTIFACT_BUCKET,
        object_name,
        data=io.BytesIO(body_bytes),
        length=len(body_bytes),
        content_type="text/markdown; charset=utf-8",
    )

    Session = make_session_factory()
    with Session() as s:
        s.add(
            Artifact(
                id=artifact_id,
                topic_id=topic_id,
                kind=kind,
                period_label=period_label,
                body_uri=f"minio://{ARTIFACT_BUCKET}/{object_name}",
                fingerprint=fingerprint,
                created_at=generated_at,
            )
        )
        s.commit()
    return artifact_id
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_artifacts_store.py -v
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/artifacts/ tests/test_artifacts_store.py
git commit -m "feat(artifacts): MinIO + PG index dual-write"
```

---

## Task 15: Memory bootstrap files

**Files:**
- Create: `memory/me/topics/nowcasting.md`
- Create: `memory/me/topics/nowcasting.theses.md`
- Create: `memory/me/feedback/research_digest_style.md`
- Create: `memory/me/user/research_focus.md`

实写 4 份样本 memory，digester 才有东西可读。

- [ ] **Step 1: Write `memory/me/topics/nowcasting.md`**

```markdown
---
name: nowcasting
description: 临近降水预报科研订阅，关注 0-6h 雷达/卫星驱动方法
type: topic
tags: [research, weather, deeplearning]
created: 2026-05-07
updated: 2026-05-07
source: user-edited
---

# 关注子域权重
- radar-based: 0.8（核心）
- satellite-derived: 0.4
- NWP-coupled hybrid: 0.3
- pure NWP: 0.0（不属本主题）

# 关键词
nowcasting, precipitation, radar echo, satellite, lead-time extension, diffusion, MetNet, NowcastNet, DGMR

# 推荐机构/作者
DeepMind, Google Research, NVIDIA, 清华, ECMWF

# 排除
纯气候建模、纯 NWP 模式开发
```

- [ ] **Step 2: Write `memory/me/topics/nowcasting.theses.md`**

```markdown
---
name: nowcasting.theses
description: nowcasting 方向当前持有的研究观点，允许并列与矛盾
type: topic
tags: [theses]
created: 2026-05-07
updated: 2026-05-07
source: user-edited
revision: 1
---

# 论点 #1
diffusion-based nowcasting 在 lead-time > 90min 仍易 mode-collapse；需更强 conditioning 或物理约束。
- 重要性：mid
- 证据：尚少（待积累）

# 论点 #2
雷达 mosaic 自身的 latency 成为 < 10min lead-time 的瓶颈；模型再快也无用。
- 重要性：low
- 证据：经验观察
```

- [ ] **Step 3: Write `memory/me/feedback/research_digest_style.md`**

```markdown
---
name: research_digest_style
description: 周报输出风格偏好（每篇 1-2 句、附 SOTA 表、不要长篇综述）
type: feedback
tags: [output, style]
created: 2026-05-07
updated: 2026-05-07
source: user-edited
---

- 每篇论文不超过 2 句话
- 附 SOTA 增量表（如能算出）
- 列出"复现风险"快速评分（GPU 数 / 是否开源 / 代码完整度）
- 不要在 digest 里推导式综述长篇大论
- 不要用 emoji
```

- [ ] **Step 4: Write `memory/me/user/research_focus.md`**

```markdown
---
name: research_focus
description: 用户当前在写关于 lead-time extension 的论文，研究焦点
type: user
tags: [focus]
created: 2026-05-07
updated: 2026-05-07
source: user-edited
---

我在写一篇关于"将 nowcasting lead-time 从 2h 延到 6h"的论文。
关注点：
- 任何延长 lead-time 的方法
- diffusion / autoregressive 对长序列稳定性的对比
- benchmark 评测在长 lead-time 下的退化曲线
```

- [ ] **Step 5: Reindex MEMORY.md**

```bash
ISBE_MEMORY_ROOT=memory/me uv run radar memory reindex
```

Expected: `memory/me/MEMORY.md` 被重写，含 4 行新条目（+ Task 7 之前若有别的也保留）。

- [ ] **Step 6: Commit**

```bash
git add memory/me/topics/ memory/me/feedback/research_digest_style.md memory/me/user/research_focus.md memory/me/MEMORY.md
git commit -m "feat(memory): bootstrap nowcasting topic + theses + style + focus"
```

---

## Task 16: Nowcasting digester flow (TDD with mocked LLM)

**Files:**
- Create: `src/isbe/topics/nowcasting/digester.py`
- Create: `src/isbe/topics/nowcasting/templates/weekly.j2`
- Test: `tests/test_nowcasting_digester.py`

digester 三段流程：
1. 收集 facts（近 7 天 papers + 所有 repos）
2. 加载 memory（`topics/nowcasting*` + `feedback/research_digest_style.md` + `user/research_focus.md`）
3. 构 prompt → 调 LLM → 拿三段文本
4. 解析"## 蒸馏"段，每行 `- DRAFT[<path>]: <body>` 转 PendingMemoryDraft
5. 渲染 final markdown + fingerprint，存 artifact
6. 写 .pending drafts

- [ ] **Step 1: Write the failing test**

`tests/test_nowcasting_digester.py`:

```python
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from isbe.topics.base import DigestResult, PendingMemoryDraft
from isbe.topics.nowcasting.digester import (
    parse_distillation_section,
    weekly_digester,
)


def test_parse_distillation_yields_drafts():
    section = """- DRAFT[topics/nowcasting.theses.md]: 新论点：lead-time > 90min mode collapse
- DRAFT[reading/2026/W19/2604.12345.md]: PaperX 已自动标注

无前缀的行应忽略
"""
    drafts = parse_distillation_section(section)
    assert len(drafts) == 2
    assert drafts[0].target_path == "topics/nowcasting.theses.md"
    assert "lead-time" in drafts[0].body
    assert drafts[1].target_path.startswith("reading/")


def test_weekly_digester_end_to_end_mocked(memory_dir: Path, monkeypatch):
    """Mock papers query + LLM + artifact store; verify produces DigestResult + .pending."""
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))

    # Bootstrap minimum memory files
    (memory_dir / "topics" / "nowcasting.md").write_text(
        """---
name: nowcasting
description: nowcasting topic
type: topic
created: 2026-05-07
updated: 2026-05-07
source: user-edited
---
keywords: a, b
""",
        encoding="utf-8",
    )

    fake_papers = [
        MagicMock(
            arxiv_id="2604.12345",
            title="Test paper",
            abstract="abstract",
            authors=["Alice"],
            primary_category="cs.LG",
            submitted_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            source_url="https://arxiv.org/abs/2604.12345",
        )
    ]

    fake_session = MagicMock()
    fake_session.__enter__ = MagicMock(return_value=fake_session)
    fake_session.__exit__ = MagicMock(return_value=False)
    fake_session.scalars.return_value.all.return_value = fake_papers

    fake_llm_resp = MagicMock(text="""## 事实
当周期 1 篇论文。

## 分析
PaperX 提了新方法 (memory: nowcasting@1)。

## 蒸馏
- DRAFT[topics/nowcasting.theses.md]: 新论点候选
""", message_id="m1", input_tokens=100, output_tokens=50, trace_id="t1")

    with patch("isbe.topics.nowcasting.digester.make_session_factory",
               return_value=lambda: fake_session), \
         patch("isbe.topics.nowcasting.digester.complete", return_value=fake_llm_resp), \
         patch("isbe.topics.nowcasting.digester.save_artifact",
               return_value="00000000-0000-0000-0000-000000000001"):
        result = weekly_digester(period_label="2026-W19", today=date(2026, 5, 7))

    assert isinstance(result, DigestResult)
    assert result.topic_id == "nowcasting"
    assert {s.kind for s in result.sections} == {"facts", "analysis", "distillation"}
    assert len(result.pending_drafts) == 1
    pending_root = memory_dir / ".pending"
    assert any(p.suffix == ".md" for p in pending_root.rglob("*"))
```

- [ ] **Step 2: Run test to verify failure**

```bash
uv run pytest tests/test_nowcasting_digester.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement `src/isbe/topics/nowcasting/templates/weekly.j2`**

```jinja
# 临近降水预报周报 — {{ period_label }}

（facts: {{ n_papers }} papers / {{ n_repos }} repos；
 memory: {{ memory_refs }}；
 trace: {{ trace_id }}）

{{ digest_body }}

---

*generated_at: {{ generated_at }}; artifact_id: {{ artifact_id }}*
```

- [ ] **Step 4: Implement `src/isbe/topics/nowcasting/digester.py`**

```python
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Template
from prefect import flow
from sqlalchemy import select

from isbe.artifacts.store import save_artifact
from isbe.facts.db import make_session_factory
from isbe.llm.client import complete
from isbe.llm.prompts import SYSTEM_PROMPT, build_digest_prompt
from isbe.memory.loader import load_index
from isbe.memory.pending import write_pending
from isbe.topics.base import (
    DigestResult,
    DigestSection,
    PendingMemoryDraft,
)
from isbe.topics.nowcasting.facts import Paper, Repo

TOPIC_ID = "nowcasting"
TOPIC_LABEL = "Nowcasting research subscription"

DRAFT_LINE_RE = re.compile(r"^\s*-\s*DRAFT\[([^\]]+)\]:\s*(.+)$")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


def _build_facts_block(papers: list, repos: list) -> str:
    lines = [f"近 7 天 papers ({len(papers)}):"]
    for p in papers:
        lines.append(f"- [{p.arxiv_id}] {p.title} ({p.primary_category}) — {p.source_url}")
    lines.append(f"\nTracked repos ({len(repos)}):")
    for r in repos:
        lines.append(f"- {r.title} stars={r.stars} last_commit={r.last_commit_at} — {r.github_url}")
    return "\n".join(lines)


def _build_memory_block(memory_root: Path) -> tuple[str, dict]:
    """Returns (text_block, {name: revision} index)."""
    index = {}
    chunks = []
    for entry in load_index(memory_root):
        ftype = entry.frontmatter.type
        # Only include topic-relevant types for digest context
        if ftype.value not in ("topic", "feedback", "user"):
            continue
        index[entry.frontmatter.name] = entry.frontmatter.revision
        chunks.append(
            f"--- {entry.frontmatter.name}@rev{entry.frontmatter.revision} "
            f"(type={ftype.value}) ---\n{entry.body.strip()}"
        )
    return "\n\n".join(chunks), index


def _split_sections(text: str) -> dict[str, str]:
    """Split LLM output by '## 事实' / '## 分析' / '## 蒸馏' headers."""
    sections: dict[str, str] = {}
    current_key = None
    buf: list[str] = []
    name_map = {"事实": "facts", "分析": "analysis", "蒸馏": "distillation"}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_key is not None:
                sections[current_key] = "\n".join(buf).strip()
            header = stripped[3:].strip()
            current_key = name_map.get(header)
            buf = []
        else:
            if current_key is not None:
                buf.append(line)
    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()
    return sections


def parse_distillation_section(text: str) -> list[PendingMemoryDraft]:
    drafts: list[PendingMemoryDraft] = []
    for line in text.splitlines():
        m = DRAFT_LINE_RE.match(line)
        if not m:
            continue
        target_path = m.group(1).strip()
        content = m.group(2).strip()
        target_type = target_path.split("/")[0].rstrip("s")  # "topics"->"topic", "reading"->"reading"
        if target_type == "topic":
            target_type = "topic"
        body = (
            f"---\nname: {Path(target_path).stem}\n"
            f"description: agent draft from digest\n"
            f"type: {target_type}\n"
            f"created: {date.today().isoformat()}\n"
            f"updated: {date.today().isoformat()}\n"
            f"source: agent-inferred\n---\n{content}\n"
        )
        drafts.append(
            PendingMemoryDraft(
                target_type=target_type,
                target_path=target_path,
                body=body,
                rationale="extracted from weekly digest distillation section",
            )
        )
    return drafts


@flow(name="nowcasting-weekly-digester")
def weekly_digester(period_label: str, today: date | None = None) -> DigestResult:
    today = today or date.today()
    cutoff = datetime.combine(today - timedelta(days=7), datetime.min.time(), tzinfo=timezone.utc)

    Session = make_session_factory()
    with Session() as s:
        papers = list(s.scalars(select(Paper).where(Paper.submitted_at >= cutoff)).all())
        repos = list(s.scalars(select(Repo)).all())

    facts_block = _build_facts_block(papers, repos)
    memory_root = _memory_root()
    memory_block, memory_index = _build_memory_block(memory_root)

    user_prompt = build_digest_prompt(
        topic_label=TOPIC_LABEL,
        period_label=period_label,
        facts_block=facts_block,
        memory_block=memory_block,
    )
    resp = complete(system=SYSTEM_PROMPT, user=user_prompt)
    parts = _split_sections(resp.text)
    sections = [
        DigestSection(kind="facts", body=parts.get("facts", "")),
        DigestSection(kind="analysis", body=parts.get("analysis", "")),
        DigestSection(kind="distillation", body=parts.get("distillation", "")),
    ]
    drafts = parse_distillation_section(parts.get("distillation", ""))
    for d in drafts:
        write_pending(memory_root, d)

    fingerprint = {
        "facts": {"papers": [p.arxiv_id for p in papers], "repos": [r.github_url for r in repos]},
        "memory": memory_index,
        "trace_id": resp.trace_id,
        "message_id": resp.message_id,
    }

    template = Template((Path(__file__).parent / "templates" / "weekly.j2").read_text(encoding="utf-8"))
    rendered = template.render(
        period_label=period_label,
        n_papers=len(papers),
        n_repos=len(repos),
        memory_refs=", ".join(f"{k}@rev{v}" for k, v in memory_index.items()),
        trace_id=resp.trace_id or "(none)",
        digest_body=resp.text,
        generated_at=datetime.now(timezone.utc).isoformat(),
        artifact_id="(filled below)",
    )

    artifact_id = save_artifact(
        topic_id=TOPIC_ID,
        kind="weekly_digest",
        period_label=period_label,
        body_markdown=rendered,
        fingerprint=fingerprint,
        generated_at=datetime.now(timezone.utc),
    )

    return DigestResult(
        topic_id=TOPIC_ID,
        period_label=period_label,
        generated_at=datetime.now(timezone.utc),
        sections=sections,
        fingerprint={**fingerprint, "artifact_id": str(artifact_id)},
        pending_drafts=drafts,
    )
```

- [ ] **Step 5: Run test to verify pass**

```bash
uv run pytest tests/test_nowcasting_digester.py -v
```

Expected: 2 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/topics/nowcasting/digester.py src/isbe/topics/nowcasting/templates/ tests/test_nowcasting_digester.py
git commit -m "feat(nowcasting): weekly digester (facts × memory → 3-section + .pending)"
```

---

## Task 17: CLI `radar topics list|run`

**Files:**
- Create: `src/isbe/cli/topics_cmd.py`
- Modify: `src/isbe/cli/main.py`
- Test: `tests/test_cli_topics_cmd.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_topics_cmd.py`:

```python
from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_topics_list_includes_nowcasting():
    runner = CliRunner()
    result = runner.invoke(app, ["topics", "list"])
    assert result.exit_code == 0
    assert "nowcasting" in result.stdout
    assert "weekly" in result.stdout


def test_radar_topics_run_unknown_topic_fails():
    runner = CliRunner()
    result = runner.invoke(app, ["topics", "run", "nonexistent", "--collect"])
    assert result.exit_code != 0
    assert "unknown topic" in result.stdout.lower() or "unknown topic" in result.stderr.lower()
```

- [ ] **Step 2: Run tests to verify failure**

```bash
uv run pytest tests/test_cli_topics_cmd.py -v
```

Expected: FAIL — `No such command 'topics'`。

- [ ] **Step 3: Implement `src/isbe/cli/topics_cmd.py`**

```python
import importlib
from datetime import date

import typer

from isbe.topics.registry import default_topics_root, discover_topics

topics_app = typer.Typer(help="Topic 管理与执行。")


@topics_app.command("list")
def topics_list() -> None:
    for t in discover_topics(default_topics_root()):
        marker = "active" if t.active else "inactive"
        typer.echo(f"{t.id}\t{t.cadence}\t{marker}\t{t.label}")


@topics_app.command("run")
def topics_run(
    topic_id: str,
    collect: bool = typer.Option(False, "--collect", help="Run collectors only"),
    digest: bool = typer.Option(False, "--digest", help="Run digester only"),
    period_label: str = typer.Option(None, help="e.g. 2026-W19; defaults to current ISO week"),
) -> None:
    topics = {t.id: t for t in discover_topics(default_topics_root())}
    if topic_id not in topics:
        typer.echo(f"unknown topic: {topic_id}", err=True)
        raise typer.Exit(code=1)

    if not (collect or digest):
        typer.echo("specify --collect or --digest (or both)", err=True)
        raise typer.Exit(code=1)

    if topic_id == "nowcasting":
        if collect:
            arxiv_mod = importlib.import_module("isbe.topics.nowcasting.collectors.arxiv")
            github_mod = importlib.import_module("isbe.topics.nowcasting.collectors.github")
            n1 = arxiv_mod.arxiv_collector()
            n2 = github_mod.github_collector()
            typer.echo(f"arxiv: {n1} new / github: {n2} new")
        if digest:
            digester_mod = importlib.import_module("isbe.topics.nowcasting.digester")
            today = date.today()
            year, week, _ = today.isocalendar()
            label = period_label or f"{year}-W{week:02d}"
            result = digester_mod.weekly_digester(period_label=label, today=today)
            typer.echo(f"digest done: {len(result.pending_drafts)} drafts pending")
    else:
        typer.echo(f"topic {topic_id} has no run wiring yet", err=True)
        raise typer.Exit(code=2)
```

- [ ] **Step 4: Update `src/isbe/cli/main.py`**

```python
import typer

from isbe.cli.memory_cmd import memory_app
from isbe.cli.review import review_app
from isbe.cli.topics_cmd import topics_app

app = typer.Typer(help="ISBE radar — 自我成长情报雷达 CLI。")
app.add_typer(review_app, name="review")
app.add_typer(memory_app, name="memory")
app.add_typer(topics_app, name="topics")


if __name__ == "__main__":
    app()
```

- [ ] **Step 5: Run tests to verify pass**

```bash
uv run pytest tests/test_cli_topics_cmd.py -v
```

Expected: 2 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/cli/topics_cmd.py src/isbe/cli/main.py tests/test_cli_topics_cmd.py
git commit -m "feat(cli): radar topics list/run"
```

---

## Task 18: End-to-end smoke run (manual; not unit test)

**Files:**
- Modify: `docs/superpowers/PROGRESS.md`（末尾追加 P1 状态段）

> 这是一项**集成验证任务**。前置：`docker compose up -d` 启动整套基础设施；`.env` 中填入真实 `ANTHROPIC_API_KEY`。

- [ ] **Step 1: Confirm compose stack is up**

```bash
docker compose ps
```

Expected: 7 个 service 全 running（postgres / qdrant / minio / langfuse-db / langfuse / prefect-server / uptime-kuma）。

- [ ] **Step 2: Apply migrations against running Postgres**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 001 -> 002, nowcasting facts`（首次）。

- [ ] **Step 3: Run all unit tests**

```bash
uv run pytest -v
```

Expected: 全部 PASS（含集成 marker 的本次也跑，因为 PG 在线）。

- [ ] **Step 4: Run the arxiv + github collectors**

```bash
uv run radar topics run nowcasting --collect
```

Expected: `arxiv: N new / github: M new`，N ≥ 0、M ∈ {0, 1, 2}。Postgres 中 `papers` / `repos` 表填入数据：

```bash
docker exec isbe-postgres psql -U isbe -c "select count(*) from papers; select count(*) from repos;"
```

- [ ] **Step 5: Confirm `.env` has ANTHROPIC_API_KEY**

```bash
grep ANTHROPIC_API_KEY .env
```

Expected: 非空值（用户填入）。**若空，停止本 Step，提示用户填入后再继续。**

- [ ] **Step 6: Run the digester**

```bash
uv run radar topics run nowcasting --digest
```

Expected: 终端打印 `digest done: K drafts pending`（K ≥ 0）。

- [ ] **Step 7: Verify artifact saved to MinIO + PG**

```bash
docker exec isbe-postgres psql -U isbe -c "select id, topic_id, period_label, body_uri from artifacts order by created_at desc limit 1;"
```

Expected: 一行新 artifact。

```bash
docker run --rm --network isbe_default minio/mc alias set isbe http://minio:9000 isbe changeme123 && \
docker run --rm --network isbe_default minio/mc ls isbe/isbe-artifacts/nowcasting/ --recursive | head -5
```

Expected: 至少一个 `.md` 对象。

- [ ] **Step 8: Read the artifact body**

```bash
ARTIFACT_KEY=$(docker exec isbe-postgres psql -U isbe -t -c "select body_uri from artifacts order by created_at desc limit 1;" | sed 's|minio://isbe-artifacts/||' | xargs)
docker run --rm --network isbe_default minio/mc alias set isbe http://minio:9000 isbe changeme123 && \
docker run --rm --network isbe_default minio/mc cat "isbe/isbe-artifacts/${ARTIFACT_KEY}"
```

Expected: 三段 markdown 输出（事实 / 分析 / 蒸馏 + jinja 包裹）。

- [ ] **Step 9: Review pending drafts**

```bash
ISBE_MEMORY_ROOT=memory/me uv run radar review memory
```

Expected: 列出 `.pending/` 中的草稿（如有）+ `K pending`。

- [ ] **Step 10: Accept one draft (manual sample)**

```bash
ls memory/me/.pending/ -R
ISBE_MEMORY_ROOT=memory/me uv run radar review memory --accept <relative-path-printed-above>
```

Expected: `accepted -> <new path>`；该文件已离开 `.pending/`。

- [ ] **Step 11: Reindex memory**

```bash
ISBE_MEMORY_ROOT=memory/me uv run radar memory reindex
cat memory/me/MEMORY.md | head -20
```

Expected: MEMORY.md 含新接受条目。

- [ ] **Step 12: Update `docs/superpowers/PROGRESS.md`**

在 PROGRESS.md 末尾追加：

```markdown

## P1 — Nowcasting MVP（2026-05-XX 完成）

- [x] Topic 抽象 + Digester 三段 + review 流（`src/isbe/topics/`）
- [x] Memory lifecycle（reindex / archive / pending workflow）
- [x] Nowcasting 域：arxiv collector + github collector + weekly digester
- [x] LLM client (anthropic) + Langfuse trace（trace 详细化留 P2）
- [x] Artifact MinIO + PG 双写
- [x] CLI: `radar topics list/run` + `radar memory reindex/archive` + `radar review memory --accept/--reject`
- [x] E2E smoke：collectors → facts → digest → artifact + .pending → review → memory（已在真实 docker 栈上跑通）

下一步候选 specs：
- P1.5：NVDA 金融日报域（复用 Topic 抽象，验证抽象正确性）
- P0.5：hermes-agent 评估补办 + 决策门
```

```bash
git add docs/superpowers/PROGRESS.md
git commit -m "docs(progress): P1 nowcasting MVP complete"
```

---

## Task 19: Tag P1 milestone

- [ ] **Step 1: Run final test suite + ruff**

```bash
uv run pytest -v && uv run ruff check src/ tests/
```

Expected: 全 PASS / 0 ruff issue。

- [ ] **Step 2: Tag**

```bash
git tag p1-nowcasting-mvp
git log --oneline | head -25
```

- [ ] **Step 3: Hand off**

宣布 P1 nowcasting MVP 完成。后续候选：
- 用 P1 抽象做 NVDA 域（Plan #2）—— 这是抽象正确性的真正考验
- 补办 P0 Task 15-19（hermes 评估）—— 单独 spec
- P2: 文件式偏好层强化（PII lint / cross-topic 引用）

---

## Self-Review Checklist

1. **Spec 覆盖**：spec §1 三层模型在 Task 2/13/14（artifacts 表）+ Task 5-7（memory 文件层）+ Task 16（digester 引用 fingerprint）三处分别落地；§2 Topic 抽象在 Task 3-4；§2.2 三段约定在 Task 13（prompt）+ Task 16（解析）；§2.3 review 流 Task 7+9；§2.4 lifecycle Task 5-6；§3 nowcasting 域 Task 10-12+15-17。**未覆盖**：§3.5 SOTA 增量表 (digester prompt 没强制要求；LLM 输出依赖 memory 引导)、Qdrant 向量检索（明示推迟）。

2. **占位符扫描**：无 TBD/TODO；所有 task 步骤含具体代码。

3. **类型一致**：`MemoryFrontmatter` (P0)、`MemoryFile` (P0)、`TopicMetadata`/`DigestResult`/`PendingMemoryDraft` (Task 3) 在 Task 7/9/16/17 间引用一致；`save_artifact()` 签名（Task 14）与 Task 16 调用一致；`complete()` 签名（Task 13）与 Task 16 调用一致；`memory_app` / `topics_app` / `review_app` Typer 实例命名贯穿 main.py 一致。

4. **边界明示**：
   - Task 11 arxiv 查询硬编码；查询语义 (cs.LG + nowcasting/precipitation/radar) 是 MVP 决定，后续可扩展
   - Task 12 GitHub `TRACKED_REPOS` 是代码常量；P3 做配置化（spec §3 L3）
   - Task 13 不在 unit test 里集成 langfuse SDK；`trace_id` 透传保留接口，Task 18 的 smoke run 才真正连 Langfuse（用户决定加 `@observe`）
   - Task 16 SOTA 增量表由 LLM 自由生成，不在 prompt 里强制结构化——这是 spec §1.5 红线 4 "memory 允许模糊" 的延伸应用

5. **风险点**：
   - Task 11 arxiv API 偶发 503——依赖 Prefect 重试；`@flow` 没显式配 retry，但 collector 快慢都不致命，每日跑允许跳过一天
   - Task 13 LLM 输出可能不严格遵守"## 事实/## 分析/## 蒸馏"分隔——`_split_sections` 用宽松匹配；若全部 LLM 不分段，三个 section body 都为空但流程不挂
   - Task 16 `_split_sections` 不验证三段都存在；可在 P2 加 lint

---

*本计划生成时间：2026-05-07；执行起点：用户选择执行模式（subagent-driven / executing-plans）后开始 Task 1。*
