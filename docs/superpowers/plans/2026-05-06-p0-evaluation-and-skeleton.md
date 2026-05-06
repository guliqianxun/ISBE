# ISBE P0' — Evaluation + Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 1.5 周内交付一个 path-B-ready 的项目骨架（Python 工程 + 文件式 memory 加载器 + CLI 占位 + 完整 docker-compose 基础设施），并对 NousResearch hermes-agent 完成 §2.1 评估清单——若关键项 5/6/7 通过则把 hermes 集成进 compose 并通过 RPC 跑通 hello-world 流程；若不通过则触发回退到附录 A。

**Architecture:** Python 3.11+ uv-managed 单仓库；docker-compose 编排 Postgres / Qdrant / MinIO / Langfuse / Prefect 3 / Uptime Kuma 六件套；评估通过后追加 hermes-agent 容器；本机 Prefect 通过 RPC 调 hermes skill 验证集成路径。所有产物可被审核流（草稿→人工→落盘）后续接入。

**Tech Stack:** Python 3.11+ (uv)、Pydantic v2、Typer、Prefect 3、PyYAML、python-frontmatter、pytest、ruff、Docker Compose、Postgres 16、Qdrant 1.x、MinIO、Langfuse 3、Uptime Kuma、hermes-agent (NousResearch)。

**Spec reference:** `docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`（重点章节：§1.3 模块表、§1.5 红线、§2.1 评估清单、§4.3 memory frontmatter、§5.2 可观测栈）。

---

## File Structure

```
F:/codes/ISBE/
├── .gitignore                               # Python + Docker + memory/.pending/
├── .env.example                             # 环境变量模板（不含真值）
├── README.md                                # 项目简介 + quickstart
├── pyproject.toml                           # uv-managed Python project
├── docker-compose.yml                       # 核心基础设施（含 hermes 占位 service）
├── topics.yaml.example                      # 订阅主题示例
│
├── docs/
│   └── superpowers/
│       ├── specs/2026-05-06-self-growing-info-system-design.md
│       ├── plans/2026-05-06-p0-evaluation-and-skeleton.md  # 本文件
│       └── eval/p0-hermes-evaluation-report.md             # 任务 15 填充
│
├── src/
│   └── isbe/
│       ├── __init__.py                      # 版本号
│       ├── config.py                        # env + topics.yaml 加载
│       ├── memory/
│       │   ├── __init__.py
│       │   ├── models.py                    # Pydantic: MemoryFrontmatter, MemoryFile
│       │   ├── loader.py                    # load_index / load_file (只读)
│       │   └── lint.py                      # frontmatter 校验
│       ├── workflows/
│       │   ├── __init__.py
│       │   └── hello_world.py               # 第一个 Prefect flow
│       └── cli/
│           ├── __init__.py
│           ├── main.py                      # `radar` Typer 入口
│           └── review.py                    # `radar review memory` 占位
│
├── memory/                                  # gitignored；保留目录结构
│   └── me/
│       ├── MEMORY.md                        # 初始为空索引
│       ├── user/.gitkeep
│       ├── feedback/.gitkeep
│       └── topics/.gitkeep
│
└── tests/
    ├── conftest.py                          # tmp 目录夹具
    ├── test_config.py
    ├── test_memory_models.py
    ├── test_memory_loader.py
    ├── test_memory_lint.py
    ├── test_cli_review.py
    └── test_hello_world_flow.py
```

**职责切分**：

| 文件 | 唯一职责 |
|---|---|
| `config.py` | 读 `.env` + `topics.yaml`，**不**读 memory |
| `memory/models.py` | Pydantic 数据形状 + frontmatter 解析，**不**做 IO |
| `memory/loader.py` | 文件 IO（只读），**不**做校验逻辑 |
| `memory/lint.py` | frontmatter 校验规则，**不**做 IO |
| `cli/main.py` | Typer 入口注册子命令，**不**含业务逻辑 |
| `cli/review.py` | review 子命令骨架（P0 仅占位） |
| `workflows/hello_world.py` | Prefect flow 烟雾测试 |

---

## Conventions Used Throughout

- **Tests first** — TDD；每个 task 先写 failing test
- **Commits** — 每个 task 末尾一次 commit；commit message 用 conventional commits (`feat:`/`chore:`/`test:`/`docs:`)
- **Run commands** — 默认从 `F:/codes/ISBE/` 跑；Bash tool 而非 PowerShell（`docker compose` / `pytest` 在 bash 下行为更一致）
- **Python** — `uv run pytest ...` 不是 `pytest ...`，避免漏装依赖
- **不要 `git push`** — 用户没要求；本地 commit 即可

---

## Task 1: Initialize git repo + base layout

**Files:**
- Create: `F:/codes/ISBE/.gitignore`
- Create: `F:/codes/ISBE/README.md`
- Create: `F:/codes/ISBE/.env.example`

- [ ] **Step 1: Initialize git in ISBE root**

```bash
cd /f/codes/ISBE && git init && git branch -m main
```

Expected: `Initialized empty Git repository in F:/codes/ISBE/.git/` 并切到 main 分支。

- [ ] **Step 2: Write `.gitignore`**

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.pytest_cache/
.ruff_cache/

# uv
.python-version

# Editor
.vscode/
.idea/

# Project state
memory/*/.pending/
memory/*/.audit/
memory/*/reading/raw-*.md
.env

# Docker volumes (本地)
data/

# OS
.DS_Store
Thumbs.db
```

- [ ] **Step 3: Write `README.md` skeleton**

```markdown
# ISBE — Information System with Backbone of Evolution

自我成长的 AI 信息搜集处理系统。

详见 `docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md`。

## Status
P0' — Evaluation + Skeleton（进行中）

## Quickstart (P0')
```bash
uv sync
docker compose up -d
uv run pytest
uv run radar --help
```

## Layout
- `src/isbe/`  Python 源码
- `docs/superpowers/specs/`  设计文档
- `docs/superpowers/plans/`  实施计划
- `memory/<uid>/`  文件式记忆（用户可手编辑）
- `workflows/`  Prefect 3 flows（P1 起）
- `templates/`  Jinja prompt 模板（P1 起）
```

- [ ] **Step 4: Write `.env.example`**

```
# LLM provider (P0 阶段 hermes 评估时验证)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# Postgres
POSTGRES_USER=isbe
POSTGRES_PASSWORD=changeme
POSTGRES_DB=isbe

# Prefect
PREFECT_API_URL=http://localhost:4200/api

# Langfuse (P0 评估期可空)
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=

# Single-user default
ISBE_UID=me
```

- [ ] **Step 5: First commit**

```bash
git add .gitignore README.md .env.example && \
git commit -m "chore: initialize ISBE repo with baseline layout"
```

Expected: 一个 commit，3 个文件。

---

## Task 2: Set up Python project with uv

**Files:**
- Create: `F:/codes/ISBE/pyproject.toml`

- [ ] **Step 1: Verify uv is installed**

```bash
uv --version
```

Expected: `uv 0.4.x` 或更新；若没装跑 `pip install uv` 或参考 https://docs.astral.sh/uv/。

- [ ] **Step 2: Create `pyproject.toml`**

```toml
[project]
name = "isbe"
version = "0.0.1"
description = "Information System with Backbone of Evolution"
requires-python = ">=3.11"
dependencies = [
    "pydantic>=2.7",
    "typer>=0.12",
    "pyyaml>=6.0",
    "python-frontmatter>=1.1",
    "prefect>=3.0",
    "httpx>=0.27",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.5",
    "bandit>=1.7",
]

[project.scripts]
radar = "isbe.cli.main:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/isbe"]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]
```

- [ ] **Step 3: Create src layout skeleton**

```bash
mkdir -p src/isbe tests && \
touch src/isbe/__init__.py tests/__init__.py
```

- [ ] **Step 4: Write `src/isbe/__init__.py`**

```python
__version__ = "0.0.1"
```

- [ ] **Step 5: Run uv sync to install deps**

```bash
uv sync --all-extras
```

Expected: 创建 `.venv/`，所有依赖装好；末尾打印 "Installed N packages"。

- [ ] **Step 6: Run a smoke test**

```bash
uv run python -c "import isbe; print(isbe.__version__)"
```

Expected: `0.0.1`

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/isbe/__init__.py tests/__init__.py && \
git commit -m "chore: bootstrap Python project with uv"
```

---

## Task 3: Memory frontmatter Pydantic models (TDD)

**Files:**
- Test: `F:/codes/ISBE/tests/test_memory_models.py`
- Create: `F:/codes/ISBE/src/isbe/memory/__init__.py`
- Create: `F:/codes/ISBE/src/isbe/memory/models.py`

Spec reference: §4.3 文件格式（lint 强制）

- [ ] **Step 1: Write the failing test**

```python
# tests/test_memory_models.py
from datetime import date

import pytest
from pydantic import ValidationError

from isbe.memory.models import MemoryFrontmatter, MemoryType, MemorySource


def test_minimal_valid_frontmatter():
    fm = MemoryFrontmatter(
        name="digest_style",
        description="用户偏好的日报风格",
        type=MemoryType.feedback,
        created=date(2026, 5, 6),
        updated=date(2026, 5, 6),
        source=MemorySource.user_edited,
    )
    assert fm.revision == 1
    assert fm.tags == []
    assert fm.supersedes == []


def test_description_max_length():
    with pytest.raises(ValidationError):
        MemoryFrontmatter(
            name="x",
            description="a" * 151,
            type=MemoryType.feedback,
            created=date(2026, 5, 6),
            updated=date(2026, 5, 6),
            source=MemorySource.user_edited,
        )


def test_type_must_be_enum():
    with pytest.raises(ValidationError):
        MemoryFrontmatter(
            name="x",
            description="d",
            type="invented_type",  # not a valid MemoryType
            created=date(2026, 5, 6),
            updated=date(2026, 5, 6),
            source=MemorySource.user_edited,
        )


def test_revision_increments_via_validator():
    fm = MemoryFrontmatter(
        name="x",
        description="d",
        type=MemoryType.user,
        created=date(2026, 5, 6),
        updated=date(2026, 5, 6),
        source=MemorySource.agent_inferred,
        revision=5,
    )
    assert fm.revision == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory_models.py -v
```

Expected: 4 tests FAIL，错误为 `ModuleNotFoundError: isbe.memory.models`。

- [ ] **Step 3: Create `src/isbe/memory/__init__.py`**

```python
```

(空文件即可)

- [ ] **Step 4: Implement `models.py`**

```python
# src/isbe/memory/models.py
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    user = "user"
    feedback = "feedback"
    topic = "topic"
    reading = "reading"
    reference = "reference"


class MemorySource(str, Enum):
    user_edited = "user-edited"
    agent_inferred = "agent-inferred"
    agent_summarized = "agent-summarized"


class MemoryFrontmatter(BaseModel):
    name: str
    description: str = Field(max_length=150)
    type: MemoryType
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date
    source: MemorySource
    revision: int = 1
    supersedes: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_models.py -v
```

Expected: 4 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add tests/test_memory_models.py src/isbe/memory/__init__.py src/isbe/memory/models.py && \
git commit -m "feat(memory): Pydantic models for memory frontmatter"
```

---

## Task 4: Memory file loader (read-only, TDD)

**Files:**
- Create: `F:/codes/ISBE/tests/conftest.py`
- Test: `F:/codes/ISBE/tests/test_memory_loader.py`
- Create: `F:/codes/ISBE/src/isbe/memory/loader.py`

Spec reference: §4.2 加载与注入策略；MEMORY.md 是派生物。

- [ ] **Step 1: Write `tests/conftest.py`**

```python
# tests/conftest.py
from pathlib import Path

import pytest


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Empty memory/<uid>/ scaffold."""
    root = tmp_path / "memory" / "me"
    for sub in ("user", "feedback", "topics", "reading", "reference"):
        (root / sub).mkdir(parents=True)
    (root / "MEMORY.md").write_text("", encoding="utf-8")
    return root


@pytest.fixture
def sample_feedback_file(memory_dir: Path) -> Path:
    p = memory_dir / "feedback" / "digest_style.md"
    p.write_text(
        """---
name: digest_style
description: 用户偏好的日报风格
type: feedback
tags: [output]
created: 2026-05-06
updated: 2026-05-06
source: user-edited
revision: 2
---

不要在日报里放融资新闻。
""",
        encoding="utf-8",
    )
    return p
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_memory_loader.py
from pathlib import Path

from isbe.memory.loader import MemoryFile, load_file, load_index


def test_load_file_parses_frontmatter_and_body(sample_feedback_file: Path):
    mf: MemoryFile = load_file(sample_feedback_file)
    assert mf.frontmatter.name == "digest_style"
    assert mf.frontmatter.revision == 2
    assert "不要在日报里放融资新闻" in mf.body
    assert mf.path == sample_feedback_file


def test_load_index_empty_when_no_files(memory_dir: Path):
    entries = load_index(memory_dir)
    assert entries == []


def test_load_index_lists_all_memory_files(memory_dir: Path, sample_feedback_file: Path):
    entries = load_index(memory_dir)
    assert len(entries) == 1
    assert entries[0].frontmatter.name == "digest_style"


def test_load_index_skips_pending_and_audit(memory_dir: Path, sample_feedback_file: Path):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "x.md").write_text(
        """---
name: x
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    entries = load_index(memory_dir)
    assert len(entries) == 1  # pending is excluded
    assert entries[0].frontmatter.name == "digest_style"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory_loader.py -v
```

Expected: 4 tests FAIL — `ModuleNotFoundError: isbe.memory.loader`。

- [ ] **Step 4: Implement `loader.py`**

```python
# src/isbe/memory/loader.py
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from isbe.memory.models import MemoryFrontmatter

EXCLUDED_DIRS = {".pending", ".audit"}


@dataclass(frozen=True)
class MemoryFile:
    path: Path
    frontmatter: MemoryFrontmatter
    body: str


def load_file(path: Path) -> MemoryFile:
    post = frontmatter.load(path)
    fm = MemoryFrontmatter.model_validate(post.metadata)
    return MemoryFile(path=path, frontmatter=fm, body=post.content)


def load_index(memory_root: Path) -> list[MemoryFile]:
    """Load all memory files under memory_root, skipping .pending and .audit."""
    out: list[MemoryFile] = []
    for md in memory_root.rglob("*.md"):
        if md.name == "MEMORY.md":
            continue
        if any(part in EXCLUDED_DIRS for part in md.parts):
            continue
        out.append(load_file(md))
    return out
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_loader.py -v
```

Expected: 4 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_memory_loader.py src/isbe/memory/loader.py && \
git commit -m "feat(memory): read-only file loader with .pending/.audit exclusion"
```

---

## Task 5: Memory frontmatter linter (TDD)

**Files:**
- Test: `F:/codes/ISBE/tests/test_memory_lint.py`
- Create: `F:/codes/ISBE/src/isbe/memory/lint.py`

Spec reference: §4.3 — 正文 ≤ 4KB；description ≤ 150；name 必须是 file stem。

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_memory_lint.py
from pathlib import Path

import pytest

from isbe.memory.lint import LintError, lint_file


def test_valid_file_passes(sample_feedback_file: Path):
    errors = lint_file(sample_feedback_file)
    assert errors == []


def test_name_must_match_stem(memory_dir: Path):
    p = memory_dir / "feedback" / "actual_stem.md"
    p.write_text(
        """---
name: wrong_name
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
body""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("stem" in e.message for e in errors)


def test_body_size_limit(memory_dir: Path):
    p = memory_dir / "feedback" / "huge.md"
    huge_body = "x" * 5000  # >4KB
    p.write_text(
        f"""---
name: huge
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
{huge_body}""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("body size" in e.message for e in errors)


def test_invalid_frontmatter_collected(memory_dir: Path):
    p = memory_dir / "feedback" / "bad.md"
    p.write_text(
        """---
name: bad
description: d
type: not_a_type
created: 2026-05-06
updated: 2026-05-06
source: user-edited
---
body""",
        encoding="utf-8",
    )
    errors = lint_file(p)
    assert any("frontmatter" in e.message for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_memory_lint.py -v
```

Expected: 4 tests FAIL — `ModuleNotFoundError: isbe.memory.lint`。

- [ ] **Step 3: Implement `lint.py`**

```python
# src/isbe/memory/lint.py
from dataclasses import dataclass
from pathlib import Path

import frontmatter
from pydantic import ValidationError

from isbe.memory.models import MemoryFrontmatter

BODY_SIZE_LIMIT_BYTES = 4096


@dataclass(frozen=True)
class LintError:
    path: Path
    message: str


def lint_file(path: Path) -> list[LintError]:
    errors: list[LintError] = []
    post = frontmatter.load(path)

    try:
        fm = MemoryFrontmatter.model_validate(post.metadata)
    except ValidationError as e:
        errors.append(LintError(path, f"frontmatter invalid: {e.errors()[0]['msg']}"))
        return errors  # 不能继续校验其它

    if fm.name != path.stem:
        errors.append(LintError(path, f"name '{fm.name}' must match file stem '{path.stem}'"))

    body_bytes = post.content.encode("utf-8")
    if len(body_bytes) > BODY_SIZE_LIMIT_BYTES:
        errors.append(
            LintError(path, f"body size {len(body_bytes)} bytes exceeds {BODY_SIZE_LIMIT_BYTES}")
        )

    return errors
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_memory_lint.py -v
```

Expected: 4 tests PASS。

- [ ] **Step 5: Commit**

```bash
git add tests/test_memory_lint.py src/isbe/memory/lint.py && \
git commit -m "feat(memory): frontmatter linter (stem match + body size + schema)"
```

---

## Task 6: Config loader (env + topics.yaml, TDD)

**Files:**
- Create: `F:/codes/ISBE/topics.yaml.example`
- Test: `F:/codes/ISBE/tests/test_config.py`
- Create: `F:/codes/ISBE/src/isbe/config.py`

- [ ] **Step 1: Write `topics.yaml.example`**

```yaml
# 订阅主题清单（P0 阶段仅是示例，P1 起被 collectors 消费）
topics:
  - id: ai-agents
    label: AI Agents
    sources:
      - type: rss
        url: https://example.com/feed.xml
      - type: arxiv
        category: cs.AI

  - id: llm-inference
    label: LLM Inference
    sources:
      - type: rss
        url: https://example.com/inference-feed.xml
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path

import pytest

from isbe.config import Config, load_config


def test_load_config_reads_uid_from_env(tmp_path: Path, monkeypatch):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text("topics: []\n", encoding="utf-8")
    monkeypatch.setenv("ISBE_UID", "alice")
    cfg: Config = load_config(topics_path=topics_file)
    assert cfg.uid == "alice"
    assert cfg.topics == []


def test_load_config_default_uid_is_me(tmp_path: Path, monkeypatch):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text("topics: []\n", encoding="utf-8")
    monkeypatch.delenv("ISBE_UID", raising=False)
    cfg: Config = load_config(topics_path=topics_file)
    assert cfg.uid == "me"


def test_load_config_parses_topics(tmp_path: Path):
    topics_file = tmp_path / "topics.yaml"
    topics_file.write_text(
        """topics:
  - id: x
    label: X
    sources: []
""",
        encoding="utf-8",
    )
    cfg = load_config(topics_path=topics_file)
    assert len(cfg.topics) == 1
    assert cfg.topics[0].id == "x"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 3 tests FAIL — `ModuleNotFoundError: isbe.config`。

- [ ] **Step 4: Implement `config.py`**

```python
# src/isbe/config.py
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel


class TopicSource(BaseModel):
    type: str
    url: str | None = None
    category: str | None = None


class Topic(BaseModel):
    id: str
    label: str
    sources: list[TopicSource]


@dataclass(frozen=True)
class Config:
    uid: str
    topics: list[Topic]


def load_config(topics_path: Path) -> Config:
    uid = os.getenv("ISBE_UID", "me")
    raw = yaml.safe_load(topics_path.read_text(encoding="utf-8")) or {}
    topics = [Topic.model_validate(t) for t in raw.get("topics", [])]
    return Config(uid=uid, topics=topics)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 3 tests PASS。

- [ ] **Step 6: Commit**

```bash
git add topics.yaml.example tests/test_config.py src/isbe/config.py && \
git commit -m "feat(config): env + topics.yaml loader"
```

---

## Task 7: CLI entrypoint `radar` with `review memory` placeholder (TDD)

**Files:**
- Create: `F:/codes/ISBE/src/isbe/cli/__init__.py`
- Create: `F:/codes/ISBE/src/isbe/cli/main.py`
- Create: `F:/codes/ISBE/src/isbe/cli/review.py`
- Test: `F:/codes/ISBE/tests/test_cli_review.py`

Spec reference: §3.2 / §4.4 review CLI 设计；P0 仅占位列出 .pending/，**不**做实际审核。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_review.py
from pathlib import Path

from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_root_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "review" in result.stdout


def test_review_memory_lists_pending_files(memory_dir: Path, monkeypatch):
    pending = memory_dir / ".pending" / "feedback"
    pending.mkdir(parents=True)
    (pending / "test.md").write_text(
        """---
name: test
description: d
type: feedback
created: 2026-05-06
updated: 2026-05-06
source: agent-inferred
---
body""",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))

    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "test.md" in result.stdout
    assert "1 pending" in result.stdout


def test_review_memory_empty_when_nothing_pending(memory_dir: Path, monkeypatch):
    monkeypatch.setenv("ISBE_MEMORY_ROOT", str(memory_dir))
    runner = CliRunner()
    result = runner.invoke(app, ["review", "memory"])
    assert result.exit_code == 0
    assert "0 pending" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_cli_review.py -v
```

Expected: 3 tests FAIL — `ModuleNotFoundError: isbe.cli.main`。

- [ ] **Step 3: Implement `cli/__init__.py`**

```python
```

(空文件)

- [ ] **Step 4: Implement `cli/review.py`**

```python
# src/isbe/cli/review.py
import os
from pathlib import Path

import typer

review_app = typer.Typer(help="审核 agent 提议的草稿（memory / tools / workflows）。")


def _memory_root() -> Path:
    raw = os.getenv("ISBE_MEMORY_ROOT")
    if raw:
        return Path(raw)
    uid = os.getenv("ISBE_UID", "me")
    return Path("memory") / uid


@review_app.command("memory")
def review_memory() -> None:
    """List pending memory drafts (P0 placeholder — no actual review yet)."""
    root = _memory_root()
    pending_root = root / ".pending"
    if not pending_root.exists():
        typer.echo("0 pending")
        return

    files = sorted(p for p in pending_root.rglob("*.md"))
    for p in files:
        typer.echo(str(p.relative_to(root)))
    typer.echo(f"{len(files)} pending")


@review_app.command("tools")
def review_tools() -> None:
    """List pending skill drafts (P0 placeholder)."""
    typer.echo("(not implemented in P0; will be wired up after hermes integration)")
```

- [ ] **Step 5: Implement `cli/main.py`**

```python
# src/isbe/cli/main.py
import typer

from isbe.cli.review import review_app

app = typer.Typer(help="ISBE radar — 自我成长情报雷达 CLI。")
app.add_typer(review_app, name="review")


if __name__ == "__main__":
    app()
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli_review.py -v
```

Expected: 3 tests PASS。

- [ ] **Step 7: Verify the script is callable**

```bash
uv run radar --help
```

Expected: 帮助文本显示 `review` 子命令。

- [ ] **Step 8: Commit**

```bash
git add src/isbe/cli/__init__.py src/isbe/cli/main.py src/isbe/cli/review.py tests/test_cli_review.py && \
git commit -m "feat(cli): radar entrypoint with review memory placeholder"
```

---

## Task 8: Memory directory scaffolding

**Files:**
- Create: `F:/codes/ISBE/memory/me/MEMORY.md`
- Create: `F:/codes/ISBE/memory/me/user/.gitkeep`
- Create: `F:/codes/ISBE/memory/me/feedback/.gitkeep`
- Create: `F:/codes/ISBE/memory/me/topics/.gitkeep`
- Create: `F:/codes/ISBE/memory/me/reading/.gitkeep`
- Create: `F:/codes/ISBE/memory/me/reference/.gitkeep`
- Modify: `F:/codes/ISBE/.gitignore`（确保 .gitkeep 不被忽略）

- [ ] **Step 1: Create memory directory tree**

```bash
mkdir -p memory/me/{user,feedback,topics,reading,reference}
```

- [ ] **Step 2: Add .gitkeep files**

```bash
touch memory/me/user/.gitkeep \
      memory/me/feedback/.gitkeep \
      memory/me/topics/.gitkeep \
      memory/me/reading/.gitkeep \
      memory/me/reference/.gitkeep
```

- [ ] **Step 3: Write empty `MEMORY.md`**

```bash
printf "%s\n" "" > memory/me/MEMORY.md
```

(有意留空。Memory 写入时由 reindex 脚本——后续阶段——填充。)

- [ ] **Step 4: Update `.gitignore`** to ensure `.gitkeep` survives the `memory/*/.pending/` rule

`F:/codes/ISBE/.gitignore` 末尾追加：

```
# Allow .gitkeep to preserve empty memory/ subdirs
!memory/*/*/.gitkeep
!memory/*/MEMORY.md
```

- [ ] **Step 5: Verify with git status**

```bash
git add -A && git status
```

Expected: 看到 `memory/me/MEMORY.md` + 5 个 `.gitkeep`，**没有** `memory/me/.pending/...`（因为还没创建）。

- [ ] **Step 6: Commit**

```bash
git commit -m "chore: scaffold memory/me/ directory layout"
```

---

## Task 9: Prefect 3 hello-world flow (TDD)

**Files:**
- Create: `F:/codes/ISBE/src/isbe/workflows/__init__.py`
- Create: `F:/codes/ISBE/src/isbe/workflows/hello_world.py`
- Test: `F:/codes/ISBE/tests/test_hello_world_flow.py`

Spec reference: §1.5 红线 5（workflow 是 Python flow）；MVP 完成标志是 hello-world flow 跑通。

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hello_world_flow.py
from isbe.workflows.hello_world import hello_world_flow


def test_hello_world_returns_expected_payload():
    """In-memory flow execution; no Prefect server needed."""
    result = hello_world_flow(name="ISBE")
    assert result["greeting"] == "hello, ISBE"
    assert "timestamp" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/test_hello_world_flow.py -v
```

Expected: FAIL — `ModuleNotFoundError`。

- [ ] **Step 3: Implement `workflows/__init__.py`**

```python
```

(空文件)

- [ ] **Step 4: Implement `hello_world.py`**

```python
# src/isbe/workflows/hello_world.py
from datetime import datetime, timezone

from prefect import flow, task


@task
def build_greeting(name: str) -> str:
    return f"hello, {name}"


@flow(name="hello-world")
def hello_world_flow(name: str = "world") -> dict:
    greeting = build_greeting(name)
    return {"greeting": greeting, "timestamp": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    print(hello_world_flow("ISBE"))
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/test_hello_world_flow.py -v
```

Expected: PASS（注意：Prefect 在 in-memory 模式下能跑；不连 server）。

- [ ] **Step 6: Smoke run**

```bash
uv run python -m isbe.workflows.hello_world
```

Expected: 打印 `{'greeting': 'hello, ISBE', 'timestamp': '2026-05-...'}`。

- [ ] **Step 7: Commit**

```bash
git add src/isbe/workflows/__init__.py src/isbe/workflows/hello_world.py tests/test_hello_world_flow.py && \
git commit -m "feat(workflows): hello-world Prefect 3 flow"
```

---

## Task 10: docker-compose — Postgres / Qdrant / MinIO

**Files:**
- Create: `F:/codes/ISBE/docker-compose.yml`

Spec reference: §1.3 模块表；§5.4 备份默认本地（用命名 volume）。

- [ ] **Step 1: Write `docker-compose.yml` (first 3 services)**

```yaml
# docker-compose.yml
name: isbe

services:
  postgres:
    image: postgres:16-alpine
    container_name: isbe-postgres
    environment:
      POSTGRES_USER: ${POSTGRES_USER:-isbe}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
      POSTGRES_DB: ${POSTGRES_DB:-isbe}
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-isbe}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  qdrant:
    image: qdrant/qdrant:v1.11.0
    container_name: isbe-qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrantdata:/qdrant/storage
    restart: unless-stopped

  minio:
    image: minio/minio:RELEASE.2024-09-13T20-26-02Z
    container_name: isbe-minio
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER:-isbe}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD:-changeme123}
    ports:
      - "9000:9000"
      - "9001:9001"
    volumes:
      - miniodata:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes:
  pgdata:
  qdrantdata:
  miniodata:
```

- [ ] **Step 2: Add MinIO env vars to `.env.example`**

`.env.example` 追加：

```
MINIO_ROOT_USER=isbe
MINIO_ROOT_PASSWORD=changeme123
```

- [ ] **Step 3: Bring up the 3 services**

```bash
cp .env.example .env && docker compose up -d postgres qdrant minio
```

Expected: 3 个容器跑起来；`docker compose ps` 显示 status=running，postgres healthcheck=healthy。

- [ ] **Step 4: Smoke test each service**

```bash
docker exec isbe-postgres pg_isready -U isbe && \
curl -sf http://localhost:6333/healthz && \
curl -sf http://localhost:9000/minio/health/live
```

Expected: 三条都返回 OK / 200。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example && \
git commit -m "feat(infra): docker-compose with Postgres + Qdrant + MinIO"
```

---

## Task 11: docker-compose — Langfuse

**Files:**
- Modify: `F:/codes/ISBE/docker-compose.yml`
- Modify: `F:/codes/ISBE/.env.example`

Spec reference: §5.2 LLM trace 走 Langfuse 自托管。

- [ ] **Step 1: Add Langfuse services to `docker-compose.yml`**

在 `services:` 块尾部追加（在 `volumes:` 顶层 key 之前）：

```yaml
  langfuse-db:
    image: postgres:16-alpine
    container_name: isbe-langfuse-db
    environment:
      POSTGRES_USER: langfuse
      POSTGRES_PASSWORD: langfuse
      POSTGRES_DB: langfuse
    volumes:
      - langfusedata:/var/lib/postgresql/data
    restart: unless-stopped

  langfuse:
    image: langfuse/langfuse:3
    container_name: isbe-langfuse
    depends_on:
      - langfuse-db
    environment:
      DATABASE_URL: postgresql://langfuse:langfuse@langfuse-db:5432/langfuse
      NEXTAUTH_SECRET: ${LANGFUSE_NEXTAUTH_SECRET:-changeme-32chars-changeme-32chars}
      SALT: ${LANGFUSE_SALT:-changeme-salt-changeme-salt}
      NEXTAUTH_URL: http://localhost:3000
      TELEMETRY_ENABLED: "false"
    ports:
      - "3000:3000"
    restart: unless-stopped
```

`volumes:` 块追加：

```yaml
  langfusedata:
```

- [ ] **Step 2: Update `.env.example`**

```
LANGFUSE_NEXTAUTH_SECRET=changeme-32chars-changeme-32chars
LANGFUSE_SALT=changeme-salt-changeme-salt
```

- [ ] **Step 3: Bring up Langfuse**

```bash
docker compose up -d langfuse-db langfuse
```

- [ ] **Step 4: Wait for Langfuse to be ready and smoke test**

```bash
sleep 30 && curl -sf http://localhost:3000/api/public/health
```

Expected: `{"status":"OK", ...}`。

> 若 30 秒不够，再等 30 秒重试。Langfuse 首次启动会跑 db migration。

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example && \
git commit -m "feat(infra): Langfuse self-hosted (LLM tracing)"
```

---

## Task 12: docker-compose — Prefect 3 server

**Files:**
- Modify: `F:/codes/ISBE/docker-compose.yml`

- [ ] **Step 1: Add Prefect server to `docker-compose.yml`**

在 `services:` 块追加：

```yaml
  prefect-server:
    image: prefecthq/prefect:3-latest
    container_name: isbe-prefect-server
    command: prefect server start --host 0.0.0.0
    environment:
      PREFECT_API_DATABASE_CONNECTION_URL: postgresql+asyncpg://${POSTGRES_USER:-isbe}:${POSTGRES_PASSWORD:-changeme}@postgres:5432/prefect
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "4200:4200"
    restart: unless-stopped
```

- [ ] **Step 2: Create `prefect` database in Postgres**

```bash
docker exec isbe-postgres createdb -U isbe prefect
```

(若已存在会 noop 报错，忽略)

- [ ] **Step 3: Bring up Prefect**

```bash
docker compose up -d prefect-server
```

- [ ] **Step 4: Wait for Prefect to be ready**

```bash
sleep 20 && curl -sf http://localhost:4200/api/health
```

Expected: HTTP 200。

- [ ] **Step 5: Verify UI**

打开 http://localhost:4200/ 浏览器看看 Prefect dashboard 跑起来。

- [ ] **Step 6: Run hello-world flow against the server**

```bash
PREFECT_API_URL=http://localhost:4200/api uv run python -m isbe.workflows.hello_world
```

Expected: flow 跑成功；Prefect UI 的 Flow Runs 页面看到一条新 run。

- [ ] **Step 7: Commit**

```bash
git add docker-compose.yml && \
git commit -m "feat(infra): Prefect 3 server (postgres-backed)"
```

---

## Task 13: docker-compose — Uptime Kuma

**Files:**
- Modify: `F:/codes/ISBE/docker-compose.yml`

Spec reference: §5.2 健康指标走 Uptime Kuma。

- [ ] **Step 1: Add Uptime Kuma to `docker-compose.yml`**

在 `services:` 追加：

```yaml
  uptime-kuma:
    image: louislam/uptime-kuma:1
    container_name: isbe-uptime-kuma
    ports:
      - "3001:3001"
    volumes:
      - uptimekumadata:/app/data
    restart: unless-stopped
```

`volumes:` 追加：

```yaml
  uptimekumadata:
```

- [ ] **Step 2: Bring up Uptime Kuma**

```bash
docker compose up -d uptime-kuma
```

- [ ] **Step 3: Verify**

打开 http://localhost:3001/ 应该看到首次设置向导。

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml && \
git commit -m "feat(infra): Uptime Kuma (health monitoring)"
```

---

## Task 14: Compose stack smoke test

**Files:**
- 无新文件，仅校验

- [ ] **Step 1: Stop all containers, start fresh**

```bash
docker compose down && docker compose up -d
```

- [ ] **Step 2: Wait for all services to be healthy**

```bash
sleep 45 && docker compose ps
```

Expected: 6 个 service 全部 status=running；postgres 与 minio healthcheck=healthy。

- [ ] **Step 3: Run smoke check on every endpoint**

```bash
echo "Postgres:" && docker exec isbe-postgres pg_isready -U isbe && \
echo "Qdrant:" && curl -sf http://localhost:6333/healthz && \
echo "MinIO:" && curl -sf http://localhost:9000/minio/health/live && \
echo "Langfuse:" && curl -sf http://localhost:3000/api/public/health && \
echo "Prefect:" && curl -sf http://localhost:4200/api/health && \
echo "Uptime Kuma:" && curl -sf -o /dev/null -w "%{http_code}\n" http://localhost:3001/
```

Expected: 6 个全部 OK / 200；记录任何一个出问题的 service 进 issue。

- [ ] **Step 4: Run all unit tests one more time**

```bash
uv run pytest -v
```

Expected: 全部 PASS（应 ≥18 个 test）。

- [ ] **Step 5: Tag this stable point**

```bash
git tag p0-infra-skeleton && git log --oneline -10
```

> 此 tag 是评估失败时的回退基线。

---

## Task 15: hermes-agent evaluation against §2.1 checklist

**Files:**
- Create: `F:/codes/ISBE/docs/superpowers/eval/p0-hermes-evaluation-report.md`

Spec reference: §2.1 评估清单（10 项，9/10 延后；★ 标 5/6/7 是硬性）。

> 这是一项**研究性任务**而非编码任务。每个 sub-step 是"读 + 试 + 记录结论"的循环。所有结论写入评估报告文件——这个文件本身是评估的交付物。
>
> **不评估 natural-language cron**：spec §1.5 红线 5 已锁定调度权握在 Prefect 手里、不用 hermes 内置 cron。无需为它单独写评估项。

- [ ] **Step 1: Clone hermes-agent into a workspace dir (在仓库外)**

```bash
mkdir -p /f/codes/_workspace && \
cd /f/codes/_workspace && \
git clone https://github.com/NousResearch/hermes-agent.git && \
cd hermes-agent && git log -5 --oneline
```

> 不要 clone 进 ISBE/ 仓库——会污染 git 历史。

- [ ] **Step 2: Create the evaluation report skeleton**

```bash
mkdir -p /f/codes/ISBE/docs/superpowers/eval
```

写 `F:/codes/ISBE/docs/superpowers/eval/p0-hermes-evaluation-report.md`：

```markdown
# P0' — hermes-agent 评估报告

- **日期**：2026-05-XX
- **评估对象**：NousResearch/hermes-agent
- **评估上游 commit**：（填）
- **评估者**：（填）
- **依据**：spec §2.1

## 通过条件
- 关键项 5 / 6 / 7 必须全部 PASS
- 9 / 10 延后；不参与本次决策

## 评估项

### 1. 项目活跃度 — PASS / FAIL
- commit 频率（近 30 天 / 近 90 天）：
- release 节奏（近 6 个月几次发版）：
- 近 30 天 issue 关闭率：
- 结论：

### 2. License — PASS / FAIL
- 实际 LICENSE 文件内容摘要：
- 是否纯 MIT（无 dual-licensing 陷阱）：
- 结论：

### 3. AGENTS.md 与核心约定 — PASS / FAIL
- 关键约定列表（项目对 skill 写法的要求）：
- 我们的 collectors 与之兼容性评估：
- 结论：

### 4. Skills 文件结构 — PASS / FAIL
- 跑通示范 skill 用时：
- 自己写的 hello-skill 跑通用时（目标 <30 分钟）：
- 文件 layout / manifest 实例：
- 结论：

### 5. ★ Python RPC API 稳定性 — PASS / FAIL
- 从外部 Python 进程调 hermes skill 是否能跑：
- API 稳定性信号（版本号 / API doc 详尽度 / changelog）：
- 调用样例（贴 code）：
- 结论：

### 6. ★ Memory SQLite 只读/局部禁用 — PASS / FAIL
- hermes 是否提供禁用/只读 memory 的开关：
- 我们的 markdown 偏好层能否并存：
- 结论：

### 7. ★ Sandbox docker backend in compose — PASS / FAIL
- docker.sock 暴露方式（host mount / DinD / sibling）：
- 在我们 docker-compose 里能跑通的最小配置：
- 结论：

### 8. Self-evolution 子项目成熟度 — PASS / FAIL（参考性）
- hermes-agent-self-evolution 是 demo 还是生产就绪：
- 与主 agent 的耦合方式：
- 结论：

## 决策
- 5/6/7 = PASS / PASS / PASS → **走路径 B，进 Task 16-18**
- 5/6/7 任一 NO → **触发回退附录 A，跳到 Task 19**
```

- [ ] **Step 3: Evaluate item 1 — 项目活跃度**

```bash
cd /f/codes/_workspace/hermes-agent && \
echo "近 30 天 commit:" && git log --since="30 days ago" --oneline | wc -l && \
echo "近 90 天 commit:" && git log --since="90 days ago" --oneline | wc -l && \
git tag --sort=-creatordate | head -10 && \
gh repo view NousResearch/hermes-agent --json stargazerCount,issues,pullRequests 2>/dev/null || echo "(gh CLI 不可用，手动到 github 看)"
```

把数字填进报告 §1。**通过门槛**：近 30 天 ≥10 commits（活跃）OR 近 90 天 ≥1 release。

- [ ] **Step 4: Evaluate item 2 — License**

```bash
cat LICENSE | head -5
grep -i "license" pyproject.toml package.json 2>/dev/null | head -5
```

填报告 §2。**通过门槛**：LICENSE 第一行包含 "MIT" 且无附加条款。

- [ ] **Step 5: Evaluate item 3 — AGENTS.md 约定**

```bash
cat AGENTS.md 2>/dev/null | head -100
ls skills/ optional-skills/ 2>/dev/null
```

读 AGENTS.md，记录关键约定（≤10 条）填报告 §3。**通过门槛**：约定不要求"必须把整个项目改造成 hermes 风格"，可以把我们的 collector 作为独立 skill 注册。

- [ ] **Step 6: Evaluate item 4 — 写一个 hello-skill**

```bash
# 按照 hermes 文档/示例创建一个最小 skill
# 路径与格式以 hermes 实际要求为准；这里给一个常见骨架
mkdir -p ~/.hermes/skills/hello_isbe
```

写 `~/.hermes/skills/hello_isbe/manifest.yaml`（**严格按 hermes README 与 AGENTS.md 要求调整**）：

```yaml
name: hello_isbe
version: 0.1.0
description: ISBE 评估用最小 skill — 返回 hello
inputs:
  schema: {}
outputs:
  schema:
    message: string
```

写 `~/.hermes/skills/hello_isbe/skill.py`：

```python
def run() -> dict:
    return {"message": "hello from isbe evaluation skill"}
```

按 hermes 文档启动 hermes（命令名以 README 为准，常见为 `hermes` / `hermes-agent`），让它发现 skill：

```bash
# 命令以 hermes README 为准，下面两条是常见形式
hermes skills list 2>/dev/null || hermes-agent skills list 2>/dev/null
```

记录"从 0 到 skill 出现在 list 用了多久"填报告 §4。**通过门槛**：≤30 分钟。

- [ ] **Step 7: Evaluate item 5 ★ — Python RPC API**

写一个最小 RPC 调用脚本 `/tmp/test_hermes_rpc.py`：

```python
# 实际 RPC 入口以 hermes Python SDK doc 为准
# 下面是占位 — 评估时按 hermes 实际 API 替换
import sys
try:
    from hermes_agent import skills  # 或类似入口
    out = skills.invoke("hello_isbe", inputs={})
    print(out)
    sys.exit(0)
except Exception as e:
    print(f"FAIL: {e}", file=sys.stderr)
    sys.exit(1)
```

```bash
uv run --with hermes-agent python /tmp/test_hermes_rpc.py
```

把可行的最小 RPC 调用代码片段贴入报告 §5。**通过门槛**：能用 stable Python API（不是 subprocess shell-out）跑通至少 1 次 invoke，且 hermes 文档/changelog 显示 API 在最近 2 个 minor release 内未破坏性变化。

- [ ] **Step 8: Evaluate item 6 ★ — Memory SQLite 禁用/只读**

```bash
grep -ri "memory" hermes-agent/{cli-config.yaml.example,AGENTS.md,docs/} 2>/dev/null | head -30
grep -ri "honcho\|fts5\|sqlite" hermes-agent/agent/ 2>/dev/null | head -20
```

读 hermes config 看是否有 `memory.enabled: false` 或类似开关；若没有，看代码层能否补 patch / 通过环境变量。把发现填入报告 §6。**通过门槛**：能通过配置/启动参数让 hermes 不写或不读它内置 memory（哪怕只是空目录指向一个废弃路径）。

- [ ] **Step 9: Evaluate item 7 ★ — Sandbox docker backend in compose**

写一份临时 `compose-hermes-test.yml` 测试在 docker compose 里能否让 hermes 用 docker backend：

```yaml
# /tmp/compose-hermes-test.yml
name: hermes-test
services:
  hermes:
    image: nousresearch/hermes-agent:latest  # 镜像名以 hermes README 为准
    environment:
      HERMES_BACKEND: docker
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock  # 仅评估期使用，警惕安全
    command: hermes skills run hello_isbe   # 命令以 hermes 为准
```

```bash
docker compose -f /tmp/compose-hermes-test.yml up --abort-on-container-exit
```

观察：
- 是否拉得到镜像？
- backend=docker 时，hermes 能否拉一个 sibling 容器跑 skill？
- 是否要求 `--privileged` 或 DinD？

填入报告 §7。**通过门槛**：能在 sibling 模式下（host docker.sock 挂载）跑通；不要求 DinD 也不要求 privileged。

> 安全提示：暴露 host docker.sock 给容器在生产是危险的。评估期可接受；正式部署时需用 sysbox / rootless docker 或专用 sandbox 服务方式收紧——这是 P5 之前要重新审视的事情。

- [ ] **Step 10: Evaluate item 8 — self-evolution 成熟度**

```bash
cd /f/codes/_workspace && \
git clone https://github.com/NousResearch/hermes-agent-self-evolution.git && \
cd hermes-agent-self-evolution && \
ls && cat README.md | head -100 && \
git log --oneline | head -20
```

判断它是否有 release / 测试 / 文档，还是仅 demo。填报告 §8。**通过门槛**：参考性即可，不影响主决策。

- [ ] **Step 11: Fill in the 决策 section + commit**

填好报告底部"决策"段，明确：

- 5 = PASS / FAIL
- 6 = PASS / FAIL
- 7 = PASS / FAIL
- 总决策：路径 B / 路径 A

```bash
cd /f/codes/ISBE && \
git add docs/superpowers/eval/p0-hermes-evaluation-report.md && \
git commit -m "docs(eval): P0 hermes-agent evaluation report"
```

---

## Task 16: Decision gate

> 这是个**决策点**，不是编码任务。

- [ ] **Step 1: Read the decision section of the eval report**

```bash
tail -20 docs/superpowers/eval/p0-hermes-evaluation-report.md
```

- [ ] **Step 2: 分支决策**

- **路径 B（5/6/7 全 PASS）**：进入 Task 17。
- **路径 A（5/6/7 任一 FAIL）**：跳到 Task 19（回退动作）；不再做 Task 17-18。

将决策记录到 commit message：

```bash
git commit --allow-empty -m "decision(p0): proceed with path B (hermes integration)" 
# 或
git commit --allow-empty -m "decision(p0): fall back to path A (best-of-breed self-build)"
```

---

## Task 17: (path B only) Add hermes-agent to docker-compose

> **若 Task 16 决策 = 路径 A，跳过本任务直接到 Task 19**

**Files:**
- Modify: `F:/codes/ISBE/docker-compose.yml`
- Modify: `F:/codes/ISBE/.env.example`

- [ ] **Step 1: Append hermes service to compose**

在 `services:` 追加（参数以 §15 评估期实际跑通的配置为准；下面是占位骨架，**评估通过后按实际值替换**）：

```yaml
  hermes:
    image: nousresearch/hermes-agent:latest
    container_name: isbe-hermes
    environment:
      HERMES_BACKEND: docker
      HERMES_LLM_PROVIDER: ${HERMES_LLM_PROVIDER:-anthropic}
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      OPENAI_API_KEY: ${OPENAI_API_KEY}
      # memory 禁用/只读开关（以评估 §6 找到的实际变量名为准）
      HERMES_MEMORY_ENABLED: "false"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - hermesdata:/root/.hermes
    ports:
      - "8080:8080"   # hermes web/，端口以实际为准
    restart: unless-stopped
```

`volumes:` 追加 `hermesdata:`。

- [ ] **Step 2: Append hermes env to `.env.example`**

```
HERMES_LLM_PROVIDER=anthropic
```

- [ ] **Step 3: Bring it up**

```bash
docker compose up -d hermes
sleep 20 && docker compose logs --tail=50 hermes
```

Expected: 启动成功；日志无 fatal error。

- [ ] **Step 4: Smoke check hermes health endpoint**

(端点路径以 hermes 实际为准；常见 `/health` 或 `/api/health`)

```bash
curl -sf http://localhost:8080/health || curl -sf http://localhost:8080/api/health
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example && \
git commit -m "feat(infra): add hermes-agent service (path B integration)"
```

---

## Task 18: (path B only) Verify hermes RPC ↔ Prefect integration

**Files:**
- Modify: `F:/codes/ISBE/src/isbe/workflows/hello_world.py`
- Test: `F:/codes/ISBE/tests/test_hello_world_flow.py`

> **若 Task 16 决策 = 路径 A，跳过本任务**

- [ ] **Step 1: Replace the contents of `tests/test_hello_world_flow.py`**

整体替换文件内容（不是追加，要把 Task 9 写的版本换掉）：

```python
# tests/test_hello_world_flow.py
import os

import pytest

from isbe.workflows.hello_world import hello_world_flow


def test_hello_world_returns_expected_payload():
    """In-memory flow execution; no Prefect server needed."""
    result = hello_world_flow(name="ISBE")
    assert result["greeting"] == "hello, ISBE"
    assert "timestamp" in result


@pytest.mark.skipif(
    os.getenv("HERMES_AVAILABLE") != "1",
    reason="hermes 未运行；CI 默认跳过，本地起 docker compose up -d hermes 后置 HERMES_AVAILABLE=1 跑",
)
def test_hello_world_invokes_hermes_skill():
    """flow 调 hermes hello_isbe skill，结果合并进 payload。"""
    result = hello_world_flow(name="ISBE", use_hermes=True)
    assert result["greeting"] == "hello, ISBE"
    assert "hermes_message" in result
    assert "hello" in result["hermes_message"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
HERMES_AVAILABLE=1 uv run pytest tests/test_hello_world_flow.py -v
```

Expected: 新加的 test FAIL（旧 test PASS）；错误为 `TypeError: unexpected kwarg use_hermes`。

- [ ] **Step 3: Update `hello_world.py`**

```python
# src/isbe/workflows/hello_world.py
from datetime import datetime, timezone

import httpx
from prefect import flow, task


@task
def build_greeting(name: str) -> str:
    return f"hello, {name}"


@task
def call_hermes_hello_skill() -> str:
    """调 hermes RPC 跑 hello_isbe skill。
    
    注意：hermes 的 RPC URL 与 payload 形态以评估期实际跑通的配置为准。
    """
    resp = httpx.post(
        "http://localhost:8080/api/skills/invoke",
        json={"skill": "hello_isbe", "inputs": {}},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()["message"]


@flow(name="hello-world")
def hello_world_flow(name: str = "world", use_hermes: bool = False) -> dict:
    out = {
        "greeting": build_greeting(name),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if use_hermes:
        out["hermes_message"] = call_hermes_hello_skill()
    return out


if __name__ == "__main__":
    print(hello_world_flow("ISBE", use_hermes=False))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
HERMES_AVAILABLE=1 uv run pytest tests/test_hello_world_flow.py -v
```

Expected: 两个 test 都 PASS。

> 若 hermes RPC URL 形态与代码假设不同（评估期发现），按实际改 `call_hermes_hello_skill`。这里是 P0 整合证明，不追求 production-grade 接口封装。

- [ ] **Step 5: Run the flow against Prefect server**

```bash
PREFECT_API_URL=http://localhost:4200/api uv run python -c "from isbe.workflows.hello_world import hello_world_flow; print(hello_world_flow('ISBE', use_hermes=True))"
```

Expected: dict 包含 `hermes_message`；Prefect UI 看到一条新 run。

- [ ] **Step 6: Commit**

```bash
git add src/isbe/workflows/hello_world.py tests/test_hello_world_flow.py && \
git commit -m "feat(workflows): hello-world flow can invoke hermes skill via RPC"
```

---

## Task 19: P0' completion — wrap up + tag

**Files:**
- Modify: `F:/codes/ISBE/README.md`

- [ ] **Step 1: Update README with current status**

`F:/codes/ISBE/README.md` 把 `## Status` 段改为：

```markdown
## Status
**P0' 完成。**

- 决策：[路径 B / 路径 A] — 见 `docs/superpowers/eval/p0-hermes-evaluation-report.md`
- 基础设施：Postgres / Qdrant / MinIO / Langfuse / Prefect 3 / Uptime Kuma 全部跑起来
- Python skeleton：memory loader + frontmatter lint + radar CLI 占位 + hello-world Prefect flow
- (路径 B) hermes-agent 集成验证：hello-world flow 通过 RPC 调 hermes skill 跑通

下一步：进 P1' 日报 MVP——开始另一份 brainstorming 与 plan。
```

- [ ] **Step 2: Run the full test suite**

```bash
uv run pytest -v
```

Expected: 全部 PASS（≥18 个 test）。

- [ ] **Step 3: Check ruff and bandit**

```bash
uv run ruff check src/ tests/ && uv run bandit -r src/
```

Expected: 0 ruff issue；bandit 零高危。

- [ ] **Step 4: Verify docker compose is clean**

```bash
docker compose down && docker compose up -d && sleep 30 && docker compose ps
```

Expected: 全部 service running；healthchecks 绿。

- [ ] **Step 5: Final commit + tag**

```bash
git add README.md && \
git commit -m "docs(readme): mark P0 complete; ready for P1 planning" && \
git tag p0-complete && \
git log --oneline | head -25
```

- [ ] **Step 6: Hand off**

宣布 P0' 完成。下一步——进 P1' brainstorming + writing-plans 循环。本计划终止。

---

## Self-Review Checklist (执行者无需运行；本计划写完后已自审一次)

1. **Spec 覆盖**：本计划覆盖 spec §2 P0' 所有完成标志（评估清单 + 决策门 + hello-world flow）；§4.3 frontmatter 模型已实现；§5.2 可观测栈完整搭起；§1.5 红线 1-7 在 P0 阶段尚无机会违反（无业务代码触及它们）；P0 不实现 §3（L3 自扩展）/ §4.4 审核流写入 / §6 内容沉淀（这些属于 P1+）。
2. **占位扫描**：评估报告模板里"（填）"是有意为之的填空位，非计划占位；其余无 TODO/TBD。
3. **类型一致**：`MemoryFrontmatter`、`MemoryFile`、`Config`、`Topic` 在 Task 3-7 间引用一致；CLI `radar review memory` 与测试预期 `0 pending` / `1 pending` 输出一致；`hello_world_flow(name, use_hermes)` 在 Task 9 与 Task 18 间签名兼容（Task 18 通过追加默认参数，向后兼容 Task 9 的调用）。
4. **风险点**：Task 15-18 中 hermes 命令名/RPC 端点/env 变量按"以 README 实际为准"标注——这是评估的真实输出，不能在写计划时硬编码。

---

*本计划生成时间：2026-05-06；执行起点：用户选择执行模式（subagent-driven / inline）后开始 Task 1。*
