# ISBE 执行进度

> 单一来源：哪些任务做完了、当前 phase 是哪个、卡在哪、下一步是什么。
> 每完成一个 task 更新这里；每开始一个新会话先读这里。

**最后更新**：2026-05-07（Task 2-9 完成）

---

## 总览

| Phase | 周期 | 状态 |
|---|---|---|
| Brainstorming + Spec | 完成 | ✅ |
| Writing implementation plan | 完成 | ✅ |
| **P0' 评估 + 骨架** | **1.5 周** | **⏳ 进行中（9/19 完成；Python 骨架就绪）** |
| P1' 日报 MVP | 1.5-2 周 | ⏸ 等 P0' |
| P2' 文件式偏好层 | 1 周 | ⏸ |
| P3' 对话 + 语义检索 | 0.5-1 周 | ⏸ |
| P4' L3 自扩展 | 1 周 | ⏸ |
| P5' 看板 + 多人就绪 | 1 周 | ⏸ |

---

## P0' Task 进度（19 任务）

执行计划：`docs/superpowers/plans/2026-05-06-p0-evaluation-and-skeleton.md`

| # | 任务 | 状态 | Commit |
|---|---|---|---|
| 1 | Initialize git repo + base layout | ✅ | `c44d596` |
| 2 | Set up Python project with uv | ✅ | `af5c03d` |
| 3 | Memory frontmatter Pydantic models (TDD) | ✅ | `4372c65` |
| 4 | Memory file loader (read-only, TDD) | ✅ | `02adb50` |
| 5 | Memory frontmatter linter (TDD) | ✅ | `f71083d` |
| 6 | Config loader (env + topics.yaml, TDD) | ✅ | `9533dd8` |
| 7 | CLI `radar` + review memory placeholder | ✅ | `0024605` |
| 8 | Memory directory scaffolding | ✅ | `a3637e2` (+ `d9eb548` 修正 .claude ignore) |
| 9 | Prefect 3 hello-world flow (TDD) | ✅ | `9e75e49` |
| 10 | docker-compose Postgres + Qdrant + MinIO | ⏸ | — |
| 11 | docker-compose Langfuse | ⏸ | — |
| 12 | docker-compose Prefect 3 server | ⏸ | — |
| 13 | docker-compose Uptime Kuma | ⏸ | — |
| 14 | Compose stack smoke test + tag | ⏸ | — |
| 15 | **★ hermes-agent 评估**（§2.1 清单 8 项） | ⏸ | — |
| 16 | **★ 决策门**（路径 B vs A） | ⏸ | — |
| 17 | (路径 B) Add hermes to docker-compose | ⏸ 待 #16 | — |
| 18 | (路径 B) Verify hermes RPC ↔ Prefect | ⏸ 待 #16 | — |
| 19 | P0' completion — wrap up + tag | ⏸ | — |

**下一步起点**：Task 10 — docker-compose Postgres + Qdrant + MinIO。前置条件：Docker Desktop 已启动，端口 5432/6333/9000/9001 空闲。

**Task 2-9 摘要**：19 个 pytest 全绿；CLI `radar review memory` 占位可用；Prefect hello-world flow in-memory 跑通；memory loader/linter 覆盖 .pending/.audit 排除规则与 4KB body 限制。

---

## Task 1 review 摘要

完成于 brainstorming 会话末。

- **Implementer (haiku)**：DONE — 创建 `.gitignore` (27 行) / `README.md` (24 行) / `.env.example` (18 行)，commit `c44d596`，3 文件 69 行
- **Spec compliance**：✅ byte-level 匹配
- **Code quality**：✅ ready to merge；唯一 minor 注记是 `.gitignore` 引用了尚未存在的 `memory/` 子目录路径（Task 8 才创建），但 gitignore 对不存在路径无害

---

## 已知问题与注记

### 1. 仓库 preexisting git 状态

执行 Task 1 时发现：`F:\codes\ISBE\` **早已是 git 仓库且配了 origin remote**——非本次会话所为。implementer 正确处理（没 re-init、没 push、没碰 LICENSE）。

**待你决定**：
- `git remote -v` 看 remote 是哪里
- 如果是你自己的私仓 → 沿用，最终需要时 `git push`
- 如果是别处遗留 → 决定是否 `git remote remove origin`

### 2. Windows + Bash 路径

- Bash 工具：`/f/codes/ISBE`
- Edit/Read/Write：`F:\codes\ISBE`
- 两种都接受，看具体调用更顺手

### 3. P0 评估 (Task 15) 的真实未知数

Task 15 是研究性任务——我们写计划时**无法预知** hermes-agent 的实际 API surface（RPC URL 形式、env 变量名、镜像 tag、命令名等）。

Task 17-18 中所有 hermes 接口代码标的"以评估实际为准"，意味着 Task 15 的评估报告 (`docs/superpowers/eval/p0-hermes-evaluation-report.md`) 一旦填好，Task 17-18 的具体实现要按它调整。

**关键 ★ 项**：5 (Python RPC 稳定性) / 6 (Memory SQLite 可禁用) / 7 (Sandbox docker backend in compose)。任一 NO 就触发回退到附录 A 路径。

### 4. 持久记忆（跨会话）

用户级长期记忆在 `C:\Users\Administrator\.claude\projects\F--codes\memory\`（Claude Code 自动加载，不在本仓库）：

- `feedback_file_based_memory.md` — 偏好文件式透明系统
- `feedback_workflow_vs_agent_separation.md` — 固定调度禁用 LLM 决策循环
- `reference_hermes_agent_self_evolution.md` — hermes-agent 与本系统高度对齐
- `user_high_doc_density.md` — 用户的记忆/文档量预期快速过千

未来会话开始时，这些记忆会自动注入 Claude Code 上下文。如果换 AI 工具（Cursor/Cline/Codex），需要把这些"翻译"成对应工具的等价机制。

---

## 执行模式

之前选择：**Subagent-Driven Development**（superpowers:subagent-driven-development），每个 task 派 fresh subagent，task 间用户 review。

如果后续你想直接动手而不走 subagent：完全没问题——计划本身是自包含的，每个 task 的 step 都是可独立执行的命令 + 代码块。

---

## 下次开会话时的开场白模板

```
我在 F:\codes\ISBE 这个项目里继续 P0' phase 的实施。
- 当前进度看 docs/superpowers/PROGRESS.md
- 设计依据看 docs/superpowers/specs/...design.md
- 实施计划看 docs/superpowers/plans/...skeleton.md
- 不可违反的约束看 AGENTS.md

下一步：[Task N — 任务名]
```

---

## 变更记录

- **2026-05-06**：初始化进度文档；Task 1 完成；写入 brainstorming + spec + plan 三件套
- **2026-05-07**：Task 2-9 完成（Python 骨架 + memory + CLI + Prefect hello-world）；19 tests passing；下一步进 docker-compose 段
