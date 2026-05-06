# AGENTS.md — ISBE 项目工作指南

> 给未来在这个目录工作的 AI 助手 / 协作者：先读这里，再读 spec。
> 这是**不可绕过的约束清单**，不是可选 onboarding。

---

## 一句话项目定位

**ISBE** = Information System with Backbone of Evolution。
单人优先、Docker-first、可平滑共享的"自我成长 AI 信息搜集处理系统"，目标是每天产出"行业情报 + 研究进展"日报，可对话、可被 agent 自我扩展（L3 沙箱代码生成）、长期记忆为可手编辑文件。

---

## 必读文档（按顺序）

1. **本文件** — 不可改动的约束
2. `docs/superpowers/specs/2026-05-06-self-growing-info-system-design.md` — 完整设计（500 行；§1.5 红线、§2 phase 切分、§6 内容沉淀必读）
3. `docs/superpowers/plans/2026-05-06-p0-evaluation-and-skeleton.md` — 当前阶段实施计划（19 任务）
4. `docs/superpowers/PROGRESS.md` — 当前执行到哪一步

---

## 七条不可违反的红线（spec §1.5）

1. **单向数据流**：采集 → 处理 → 存储 → 检索 → 生成；禁止反向调用
2. **agent 学到的一切都是文件、都是数据**：tools / memory / workflows 都通过"草稿 → 审核 → 落盘 → 索引"统一流程，**永远不热替换运行中代码**
3. **状态可恢复**：Prefect run 状态持久化，长任务可断点续跑
4. **多人就绪但单人优先**：所有 API 带 `user_id`，单人模式默认 `"me"`，不强制做权限系统
5. **双执行范式分离**：
   - **Workflow（确定式）** = 所有 cron 任务，Python flow 写死，LLM 只是带固定模板的步骤
   - **Agent Loop（探索式）** = 仅服务 chat / research / skill self-creation
   - **绝对禁止用 LLM 决策每步的 ReAct 循环跑日报这种重复任务**——这是用户被现有工具坑过的痛点
6. **人有绝对优先权**：memory 冲突时 LLM 不动手 merge，仅作只读建议；用户改动赢
7. **可复现性是硬指标**：每次 LLM 调用 trace 全留；改 `templates/*.j2` 必须更新 golden output

---

## 已锁定的关键技术决策

| 维度 | 决策 | 理由 |
|---|---|---|
| 技术路线 | **路径 B**：在 NousResearch hermes-agent 上叠加领域层 | 自我成长 + sandbox + skills 系统现成，与需求骨头层面对齐 |
| 工作流引擎 | **Prefect 3 Python flows** | 确定式调度握在我们手里，**绝不**用 hermes 的 natural-language cron |
| 记忆系统 | **文件式 markdown + frontmatter**（`memory/<uid>/*.md`），不是 Mem0/数据库 | 用户要透明、可 vim、可 git 版本化 |
| 沉淀分层 | **C1 原始资料** (MinIO blob = source of truth) / **C2 行为痕迹** (reading log) / **C3 提炼洞察** (topics)；详见 spec §6 | |
| LLM 网关 | hermes 自带，**不用 LiteLLM**（路径 B 下） | |
| 通知 | hermes 多平台 gateway，**不用 Apprise** | |
| Sandbox | **Docker 默认 + E2B 备选**，遵循 OpenHands / hermes 的 backend 抽象模式 | |
| Web UI | hermes 自带 `web/`（MVP）→ 自建 Next.js（P5） | |
| 备份 | **默认本地**，上云 opt-in（用户显式开启） | |

---

## 仓库结构惯例

```
F:\codes\ISBE\
├── AGENTS.md                     # 本文件
├── README.md                     # quickstart
├── pyproject.toml                # uv-managed
├── docker-compose.yml            # 基础设施 + hermes
├── topics.yaml                   # 订阅清单（用户编辑）
├── .env / .env.example
│
├── src/isbe/                     # Python 源码
│   ├── config.py
│   ├── memory/                   # frontmatter 解析、loader、lint
│   ├── workflows/                # Prefect flows（生产代码，不是 cron 配置）
│   └── cli/                      # `radar` Typer 入口
│
├── memory/<uid>/                 # 用户数据（gitignored 部分）
│   ├── MEMORY.md                 # 索引（≤200 行，由脚本派生，不是 source of truth）
│   ├── user/feedback/topics/reading/reference/   # 5 类记忆
│   ├── .pending/                 # agent 提议的草稿
│   └── .audit/                   # 审计日志
│
├── workflows/                    # P1 起的实际 flow 文件（区别于 src/isbe/workflows）
├── templates/                    # Jinja prompt 模板（独立版本化）
├── tests/
│
└── docs/superpowers/
    ├── specs/                    # 设计文档
    ├── plans/                    # 实施计划
    ├── eval/                     # 评估报告
    └── PROGRESS.md               # 进度追踪
```

**模块边界铁律**（spec §1.3）：
- `config.py` 不读 memory；`memory/*` 不读 env；`cli/main.py` 不含业务逻辑；`workflows/*.py` 调用工具但不写工具实现；`models.py` 不做 IO

---

## 草稿审核流（统一所有 agent 写入）

不论是 skill / memory / workflow，agent 写入路径**永远是**：

```
agent 提议 → 落 .pending/<id>/ → radar review CLI 显示 diff + 上下文
   → 用户 [a]pprove / [r]eject / [e]dit-then-approve / [p]artial
   → approve → 落正式目录 + 索引重建 + 写 audit
   → reject → 进 .audit/rejected/ + 自动生成"用户不喜欢这种推断"的 meta-feedback 草稿
              （30 天 / 3 次同主题截止，防递归）
```

**绝对不要写"agent 直接覆盖正式目录"的代码路径。**

---

## 开发命令

```bash
# 装环境
uv sync --all-extras

# 起基础设施（P0 完成后可用）
docker compose up -d

# 跑测试
uv run pytest -v

# CLI
uv run radar --help

# Lint
uv run ruff check src/ tests/
uv run bandit -r src/
```

---

## 已知 environmental quirks

- **仓库已被 git init 且配了 origin remote**（preexisting，不是本次会话所为）。`git remote -v` 看一眼，确认是你自己的私仓再决定 push 策略。
- **Windows 平台**：Bash 工具可用 `/f/codes/ISBE`，Edit/Read/Write 用 `F:\codes\ISBE`。
- **Python 3.11+ via uv**。

---

## 当 agent / 你陷入选择困难时

1. 翻 spec 红线 → 任何冲突红线 → 这条选项错
2. 翻"已锁定决策" → 已经定的不要重新讨论，除非有新证据
3. 翻 PROGRESS.md → 当前 phase 不该做的事就别做（YAGNI）
4. 仍不清楚 → 问用户，不要猜

**特别警惕**：`L1/L2/L3/L4`（自扩展光谱）、`T1/T2/T3`（记忆加载层）、`C1/C2/C3`（内容沉淀层）是三个不同维度的术语，不要串台。详见 spec 附录 B。
