# 自我成长 AI 信息搜集处理系统 — 设计文档

- **状态**：Draft (待用户确认后进入实现规划)
- **日期**：2026-05-06
- **作者**：Claude (brainstorming with user)
- **代号**：ISBE — *Information System with Backbone of Evolution*
- **下一步**：通过 spec → 进入 `writing-plans` 制定实施计划

---

## 0. 文档导读

本文档是 brainstorm 全过程的固化产物，**6-8 周交付一个能每天产出"行业情报 + 研究进展"日报、可对话、可被 agent 自我扩展、长期记忆可手编辑的本地 AI 系统**。

阅读路径：先看 §1 架构、§2 phase 切分；再按需读 §3 (L3 自扩展)、§4 (记忆)、§5 (运维)、§6 (内容沉淀与检索)。附录 A 是备选方案 A（自建 Best-of-Breed），P0' 评估失败时启用。

---

## 1. 架构总览

### 1.1 系统定位

| 维度 | 决策 |
|---|---|
| 主场景 | 行业情报雷达 (B) + 研究助理 (C) |
| 自我成长方向 | L3 沙箱代码生成 (E) + 长期记忆与个性化 (D) |
| 部署 | Docker-first，单人起步，可平滑共享 |
| 交互形态 | 推送 + 对话 + 看板（**MVP 优先推送日报**） |
| LLM 策略 | 混合，先云端为主，预留本地切换 |
| 技术路线 | **路径 B**：在 NousResearch hermes-agent 上叠加"情报雷达"领域层 |

### 1.2 顶层架构

```
┌──────── 我们写的"情报雷达"领域层 ────────┐
│                                            │
│  collectors/   ← 注册为 hermes skills      │
│    rss_collector / arxiv / web_full        │
│                                            │
│  workflows/    ← Prefect 3 flows           │
│    daily_digest.py    (调度权在我们)       │
│    weekly_compact.py                       │
│                                            │
│  templates/    Jinja prompt 模板           │
│  memory/       我们的 markdown 偏好层      │
│  topics.yaml   订阅清单                     │
│                                            │
└─────────────┬──────────────────────────────┘
              │ Python RPC + skill registration
              ▼
┌────────── hermes-agent (内核) ─────────────┐
│  agent runtime · skills · tools · sandbox  │
│  llm gateway · multi-channel gateway       │
│  web/ UI · MCP · self-evolution            │
│  SQLite memory（事实型）                    │
│  ~~natural-lang cron~~（不用）              │
└────────────────────────────────────────────┘
```

**核心切分**：调度权 + 偏好记忆 = 我们；执行 + 工具 + UI + LLM = hermes。

### 1.3 模块边界

| 模块 | 职责 | 不做什么 |
|---|---|---|
| **api-gateway** | FastAPI 入口、鉴权、触发 orchestrator | 不直接调 LLM/采集 |
| **workflow-engine** | Prefect 3：加载 `workflows/*.py`、cron 注册、重试、状态持久化、按步骤调 hermes skills | 不写业务逻辑（业务在 skills 与 templates） |
| **agent-runtime** | hermes-agent；服务 chat / research / skill self-creation | 不持有调度权 |
| **collectors** *(skills)* | RSS / arxiv / web 等采集原语，注册为 hermes skills | 不做语义处理 |
| **processors** *(skills)* | PDF→MD（docling/marker）、分块、摘要、标签、向量化 | 不调度 |
| **content-pipeline** | 内容生命周期编排（去重 / 持久化 / 处理 / 行为痕迹 / 老化 / insight 提议）——详见 §6 | 不直接采集（用 collectors）；不直接生成日报（由 daily_digest 调用） |
| **tool-registry** | hermes 自带 `~/.hermes/skills/`（agentskills.io 标准） | 我们不重写 |
| **sandbox-executor** | hermes 的 Docker backend（默认） + E2B 备选 | 我们不写沙箱内核 |
| **memory-fs** | `memory/<uid>/*.md` 文件式偏好/反馈/订阅 | 不替代 hermes 事实型记忆 |
| **memory-sqlite** *(hermes)* | hermes 自带 FTS5 + Honcho；存会话回忆与全文检索 | 不存意图/偏好 |
| **vector-store** | Qdrant：知识库语义检索 + 自 P3 起 memory_index | 不存原文 |
| **blob-store** | MinIO 或本地 FS（PDF/HTML 原文） | 不索引 |
| **notifier** | 复用 hermes multi-channel gateway（TG/邮件/Discord/…） | 不决定内容 |
| **web-ui** | hermes `web/`（MVP）+ 自建 Next.js 看板（P5） | 仅消费 API |
| **template-store** | `templates/*.j2`，被 workflow 与 agent 共用 | — |
| **review-cli** | `radar review tools / memory / workflows`，统一草稿审核 | 不直接写文件 |
| **observability** | Langfuse（LLM trace） + Prefect UI（flow） + Uptime Kuma（健康） + hermes 自带日志 | — |

### 1.4 横切

- **LLM 调用**：全部走 hermes 的 LLM gateway（`hermes model` 切换）；MVP 默认云端 Claude / GPT，预留本地 Ollama
- **可观测**：Langfuse 接 hermes & workflow 的所有 LLM 调用；Prefect UI 看 flow run；Uptime Kuma 看容器健康
- **配置**：`.env` + `topics.yaml`（主题/源/调度）+ `memory/` + `~/.hermes/skills/`；改文件即生效，不改代码
- **草稿审核**：所有 agent 写入（skills、memory、workflows）统一走 `.pending/` → `radar review` → 落盘

### 1.5 关键设计原则（红线，不可违反）

1. **单向数据流**：采集 → 处理 → 存储 → 检索 → 生成；禁止反向调用
2. **agent 学到的一切都是文件、都是数据**：tools / memory / workflows 都通过"草稿 → 审核 → 落盘 → 索引"统一流程，**永远不热替换运行中代码**
3. **状态可恢复**：Prefect run 状态持久化，长任务（日报可能 10 分钟+）可断点续跑
4. **多人就绪但单人优先**：所有 API 带 `user_id`，单人模式默认 `"me"`，不强制做权限系统
5. **双执行范式分离**：
   - **Workflow（确定式）**：所有 cron 任务，YAML/Python 写死，LLM 仅作为带固定模板的步骤
   - **Agent Loop（探索式）**：仅服务 chat / research / skill self-creation
   - 共用同一 tool-registry 与 LLM gateway，**Agent 可触发 workflow 但不能决定 workflow 内部步骤**
6. **人有绝对优先权**：memory 冲突场景下，LLM 不动手 merge，仅作只读建议；用户改动赢
7. **可复现性是硬指标**：每次 LLM 调用 trace 全留；每次 workflow run 留中间产物可重放

---

## 2. MVP 阶段切分（路径 B）

| 阶段 | 周期 | 内容 | 完成标志 |
|---|---|---|---|
| **P0' 评估 + 骨架** | 1.5 周 | 跑通 hermes demo、回答 §2.1 评估清单、docker-compose 接 Prefect/Langfuse/Qdrant/MinIO/Uptime Kuma | 评估清单中关键项（5/6/7）通过；hello-world flow 跑成功；通过决策点 |
| **P1' 日报 MVP** | 1.5-2 周 | 3 个 collector skills (RSS / arxiv / web) + Prefect daily_digest flow + hermes gateway 推送 + Langfuse trace | 连跑 7 天每天早上自动收到日报，引用源齐全 |
| **P2' 文件式偏好层** | 1 周 | memory/ 加载器、`feedback/digest_style.md` 接入 digest flow、weekly_compact flow | 在 `feedback/digest_style.md` 写一句"别再放融资新闻"，下周日报里融资新闻消失 |
| **P3' 对话 + 语义检索** | 0.5-1 周 | 把我们的 markdown memory 暴露给 hermes runtime；启用 Qdrant `memory_index` collection；hermes web/ 接通 | "最近一周 RAG 领域有什么新方法" 能给出基于本地知识库的可信回答（带引用），且会自动检索相关 memory 文件 |
| **P4' L3 自扩展** | 1 周 | radar.skill_proposer wrapper（拦截 hermes skill self-creation）→ `.pending` 草稿 → `radar review tools` CLI → 静态扫描 + 网络 ACL | 三个验收用例全跑通：simonwillison 全文 / epub parser / ntfy notifier |
| **P5' 看板 + 多人就绪** | 1 周 | Next.js 自建看板（订阅/主题视图/卡片流）；API 全量带 user_id；备份导出脚本 | 朋友拿 docker-compose + 一份打包，30 分钟内能跑起来自己的实例 |

**总周期**：~6-8 周；**关键路径 P0'+P1' ≈ 3-4 周** = 真正能用上日报。

### 2.1 P0' 评估检查清单（必须答完才能进 P1）

```
[ ] 1. 项目活跃度真伪——commit 频率、release 节奏、issue 响应时间
[ ] 2. License 真为 MIT，无 dual-licensing 陷阱
[ ] 3. AGENTS.md 与核心约定——是否要求所有 skill 走它的格式
[ ] 4. Skills 文件结构——能写一个 collector skill 在 30 分钟内跑通
[ ] 5. Python RPC API 稳定性——能否从外部 Prefect flow 干净调用 ★
[ ] 6. Memory SQLite 是否能"只读模式"或"局部禁用"——并存我们的 markdown 层 ★
[ ] 7. Sandbox docker backend 在 docker-compose 里能否正常嵌套（无 docker.sock 噩梦）★
[ ] 8. Self-evolution 子项目的成熟度——是 demo 还是生产就绪
[ ] 9. (延后) 多用户路径
[ ] 10. (延后) fork 维护成本
```

打 ★ 是硬性项。≥3 项硬性 NO → 退回附录 A 的方案 A（自建 Best-of-Breed）。

### 2.2 阶段间取舍（已锁定）

- **(a) Phase 1 不上向量库**：MVP 阶段日报只查"昨日新增"，主题硬规则匹配够用；向量库 P3 介入
- **(b) Phase 3 在 Phase 4 之前**：先有聊天，让用户能给反馈、积累 memory，再跑 L3 才有"它懂我"的感觉
- **(c) Open WebUI 不再使用**：直接用 hermes 的 web/；P5 自建 Next.js 看板
- **(d) Sandbox 抽象层 + Docker 默认 + E2B 备选**：参考 OpenHands 与 hermes 的多 backend 模式
- **(e) P1 与 P2 之间插冷静期**：日报跑 1-2 周积累真实反馈再决定 P2 路线，避免冲到 L3 才发现根基不牢

---

## 3. L3 自扩展机制详细设计

### 3.1 三种自扩展层级

| 层级 | 描述 | MVP 是否启用 |
|---|---|---|
| **L3a — Skill 自创建** | 用户/对话触发缺工具时，hermes 在 sandbox 写 Python | ✅ Phase 4 启用 |
| **L3b — Skill 自改进** | hermes-agent-self-evolution（DSPy+GEPA）持续优化 prompt/code | ❌ Phase 5+ 评估 |
| **L3c — Workflow 自演化** | LLM 提议改 Prefect flow | ❌ Phase 5+ 评估 |

### 3.2 L3a 端到端流程

```
用户/agent 发现缺工具
       │
       ▼
┌──── hermes skill self-creation ────┐
│ 1. 在 sandbox（docker default）写  │
│ 2. 调研接口 / 试爬 / 跑 pytest     │
│ 3. 产出 manifest + code + 测试报告 │
└──────────────┬─────────────────────┘
               │
               ▼  ★ radar.skill_proposer 拦截
┌──── ~/.hermes/skills/.pending/<id>/ ────┐
│  manifest.yaml  · tool.py · test_tool.py│
│  sandbox_run.log · proposed_by.json     │
└──────────────┬───────────────────────────┘
               │
               ▼
┌──── 用户审核 (radar review tools) ────┐
│  diff · tests · ACL · risk            │
│  [v]iew  [t]est  [a]pprove  [r]eject  │
│  [e]dit-then-approve  [p]artial       │
└──────────────┬─────────────────────────┘
               │ approve
               ▼
~/.hermes/skills/<name>/  +  hermes reload
   + memory/.audit/skills/<ts>-<name>.md
   + 同步提议 memory/.pending/topics/<x>.md
```

### 3.3 安全约束（4 道额外锁，可扩展）

1. **静态扫描红线**——审核前自动跑 ruff + bandit + 自定义 linter，命中以下立即标红：
   - `eval` / `exec` / `__import__` 动态调用
   - 任何 `subprocess` / `os.system`
   - `open()` 写非工作目录
   - `requests` 域名不在 manifest 声明 ACL 内
2. **网络 ACL 强制声明**——manifest 必须列 `allowed_hosts`，未声明 → sandbox 测试时网络阻断
3. **资源配额**——审核记录 sandbox 跑测试的内存/时长峰值；运行时同配额
4. **dry-run 强制**——approve 前必看一次 dry-run 输出（防盲签）

### 3.4 Skill manifest 格式

```yaml
name: simonwillison_full_collector
version: 0.1.0
type: collector
description: |
  抓取 simonwillison.net 的全文（无 RSS 全文版），返回标准 Item 列表
inputs:
  schema: { since: datetime }
outputs:
  schema: { items: list[Item] }
network:
  allowed_hosts: [simonwillison.net]
runtime:
  timeout_s: 30
  memory_mb: 256
provenance:
  proposed_by: hermes-skill-self-creation
  proposed_at: 2026-05-12T14:32:00Z
  proposed_for: "用户在 chat 里说想订阅 simonwillison 全文"
  approved_by: me
  approved_at: 2026-05-12T14:45:00Z
  sandbox_test:
    runs: 3
    last_pass: 2026-05-12T14:33:11Z
```

### 3.5 与文件式记忆的联动

L3a 触发时**自动同时提议一份 memory patch**（topic / reference 类型），双草稿在同一次 review 里允许**部分批准**——可独立勾选哪些入库（`[p] partial approve`）。

### 3.6 Phase 4 验收用例

1. **新数据源 collector**：simonwillison.net 全文订阅 → 10 分钟内 skill + topic memory 双双入库，次日日报里出现该来源
2. **新格式 parser**：丢一个 `.epub` 给 chat，"以后 epub 都按这个解析" → epub_parser skill 入库，processor 流水线自动选用
3. **新通知渠道适配器**：(假设 hermes gateway 不支持 ntfy) "我想往 ntfy 推" → ntfy_notifier skill 入库

每例：通过审核流（含静态扫描红线）；三周后回访仍在用。

### 3.7 不做的事（明确 scope）

- ❌ 自动 approve（即使"低风险"也要 y/n）
- ❌ DSPy / GEPA prompt 自我进化（L3b 后续阶段）
- ❌ Skill 之间的依赖图自动管理（hermes 自管）
- ❌ Skill marketplace / 社区上传

---

## 4. 文件式记忆与个性化

### 4.1 记忆分类与边界

| 类型 | 路径 | 谁写 | 谁改 | 用在哪 |
|---|---|---|---|---|
| **会话回忆 / 全文检索**（事实型） | hermes SQLite (FTS5 + Honcho) | hermes 自动 | 不动 | hermes 拼 prompt |
| **用户身份与偏好** | `memory/<uid>/user/*.md` | agent 提议 | 用户 vim | digest flow + chat agent |
| **行为反馈** | `memory/<uid>/feedback/*.md` | agent 提议 | 用户 vim | 所有 LLM 调用按相关性注入 |
| **订阅意图** | `memory/<uid>/topics/*.md` | L3a 一并写 / 用户加 | 用户 vim | digest flow 拉清单 |
| **阅读历史压缩** | `memory/<uid>/reading/YYYY-Wxx.md` | weekly_compact flow | 通常不改 | chat 背景 / digest "上周提过" |
| **外部指针** | `memory/<uid>/reference/*.md` | agent 提议 / 用户加 | 用户 vim | 工具调用时拼 prompt |
| **审计** | `memory/<uid>/.audit/...` | 系统自动 | 不动 | 排查 + 信任溯源 |
| **草稿** | `memory/<uid>/.pending/...` | agent 写 | review 流程 | 不参与 prompt |

**判断口诀**：
- "事实型"（这事发生过）→ hermes SQLite
- "意图/偏好型"（我希望它怎样对我）→ 我们的 markdown
- "长期身份型"（我是谁、做什么）→ 我们的 markdown

### 4.2 加载与注入策略

| 场景 | T1 索引 | T2 文件 | T3 语义检索 |
|---|---|---|---|
| **Workflow 步骤** | 总载 `MEMORY.md` | flow 显式 `inject: [feedback/digest_style.md, topics/*.md]` | ❌ |
| **Chat agent** | 总载 `MEMORY.md` | agent 按 description 选 | ✅ P3 起启用 |
| **Skill self-creation** | 总载 `MEMORY.md` | `feedback/` 全部 + `topics/` 相关 | ❌ |

**关键原则**：workflow 注入是**声明式**（保复现性），agent 注入是**选择式**（保灵活性）。

> 命名说明：T = Tier（加载层级）。注意与 §3 的 L3（自扩展级别）、附录 B 的 L1-L4（自扩展光谱）不同维度——前者是"记忆从哪里取"的存储层，后者是"agent 自我成长到什么深度"的能力光谱。

### 4.3 文件格式（lint 强制）

```yaml
---
name: digest_style                          # 必填，文件 stem
description: 用户偏好的日报风格            # 必填，<150 字符
type: feedback                              # 必填，枚举
tags: [output, formatting]                  # 可选
created: 2026-05-06                         # 必填
updated: 2026-05-06                         # 写入时自动更新
source: user-edited                         # user-edited / agent-inferred / agent-summarized
revision: 3                                 # 每次 approve 自增
supersedes: []                              # 被合并掉的旧 memory id
---

正文 markdown — 推荐结构：
- 一句话核心规则
- **Why:** 动机/起因
- **How to apply:** agent 怎么用这条
- **Examples:** 正反例

正文目标 ≤ 4KB（约 1000 token），超出触发"split or compact"提示
```

**MEMORY.md** 是派生物（由脚本从所有文件 frontmatter 重建），不是 source of truth；每行 `- [name](path) — 一句钩子`，全文件 ≤ 200 行。

### 4.4 写入流程（.pending → review → 落盘）

```
agent 决定写一条 memory
     ▼
写入 memory/.pending/<type>/<name>.md
+ sidecar memory/.pending/<type>/<name>.proposal.json
  {proposed_by, proposed_at, reason, conversation_ref,
   action: create|update|delete, base_revision}
     ▼
事件入队 → radar review queue
     ▼
$ radar review memory     ← CLI 入口（也可 web review）
  显示 diff（vs base_revision） · 显示 proposal 上下文
  [a] approve  [r] reject + reason  [e] edit-then-approve
  [p] partial approve（仅多条时）
     ▼
approve  → memory/<type>/<name>.md，revision++
         + audit: memory/.audit/<ts>-<action>-<name>.json
         + 触发 MEMORY.md 索引重建
reject  → .pending → .audit/rejected/，附 reject 理由
        + reject 理由作为新 feedback 草稿（"用户不喜欢这种推断"）→ 再走一次审核 → 闭环纠偏
        ※ 防递归：若同一 (type, name) 的 reject 在 30 天内 ≥3 次，停止生成 meta-feedback 草稿，
           只写 audit 不再 propose——避免"拒绝拒绝拒绝"的元循环
```

每条 memory 独立文件，失败回滚只影响一条。

### 4.5 周度压缩（reading/ 不爆炸）

```python
# workflows/weekly_memory_compact.py
@flow(schedule=CronSchedule("0 3 * * 1"))   # 每周一凌晨 3 点
def weekly_memory_compact():
    week = previous_week_iso()
    raw = read_all(f"memory/{UID}/reading/raw-{week}-*.md")
    compacted = llm_complete(
        template="templates/weekly_compact.j2",
        vars={"raw": raw, "topics": load_memory("topics/*.md")},
        model="claude-sonnet-4-6",
    )
    propose_memory(
        path=f"reading/{week}.md",
        content=compacted,
        action="create",
        delete_after_approve=[f"reading/raw-{week}-*.md"],
    )
```

压缩**也走 .pending 审核**——agent 自己产出的"周度总结"用户可否决，approve 才删 raw。

### 4.6 用户手改 vs agent 提议的并发处理（人有绝对优先权）

冲突检测：proposal 的 `base_revision` ≠ 当前文件 revision → 冲突状态：

```
$ radar review memory
[CONFLICT] feedback/digest_style.md
  agent 基于 rev 3 提议改动
  你已手改到 rev 4

  默认动作：保留你的改动；agent 的 proposal 自动变形为新草稿（base_revision = rev 4），
  标记 "need human revision"，不进入索引。

  辅助：[s] show LLM-suggested merge —— 仅只读建议，不可一键 apply。
        用户必须手动写出最终版本，才能再次提交 review。
  [k] keep mine（丢弃 agent 提议）
  [d] show diff
```

**LLM 不动手 merge**——这是红线 6 的具体落地。

### 4.7 性能 & 何时升级

| 文件数 | 策略 | 性能预算 |
|---|---|---|
| < 50 | 总载 MEMORY.md，按需读文件 | < 100ms 加载，< 500 token MEMORY.md |
| 50–200 | MEMORY.md 分类分段 | < 300ms |
| **200+ (P3 起强制启用)** | **Qdrant `memory_index` collection**：file frontmatter description + 内容前 200 字嵌入；agent 检索 top-k → 再读文件 | < 500ms |
| > 1000 | 触发"建议归档"：12 个月未访问转 archive/ 不进索引 | — |

**P3 阶段必须启用语义检索**（基于"用户预期文档量将快速过千"的假设）。

### 4.8 隐私与备份

- **本地默认不出**：`memory/` 纯本地
- **agent 调云端 LLM 时只注入 §4.2 筛选片段**，不发整个目录
- **备份脚本**：`radar export` 一键打包 `memory/ + topics.yaml + workflows/ + ~/.hermes/skills/` → 加密 zip
- **多设备同步**（**opt-in，不默认**）：用户主动配置后才把 memory/ 推私有 git 仓库

---

## 5. 错误处理 · 可观测 · 测试

### 5.1 错误分级与处理策略

| 类别 | 例子 | 策略 |
|---|---|---|
| **瞬态可重试** | 网络抖动、LLM 限流、采集源 5xx | Prefect `retries=3, retry_delay=exp_backoff`；3 次仍失败 → 降级 |
| **降级可继续** | 单源采集失败、LLM 超时致摘要降级、单条 item 处理崩 | 步骤捕获 + 跳过；run 仍 success；最终日报附"今日采集警告"列表 |
| **运行时错误，需人介入** | sandbox 跑 skill 测试反复失败、L3 写出来的 skill 静态扫描红线 | proposal 自动 reject + 写 audit；不阻塞主 flow |
| **系统级错误** | DB 连不上、磁盘满、Qdrant 崩 | Prefect run fail → hermes gateway 推紧急通知（TG/邮件） + 暂停 cron 直到人工解决 |

**红线**：日报这种"用户能感知"的输出，**单源失败不让整篇日报失踪**——宁可缺胳膊少腿也要按时推。

### 5.2 可观测栈

| 关注什么 | 工具 | 数据保留 |
|---|---|---|
| **LLM 调用** trace（prompt / model / tokens / 延迟 / 成本） | **Langfuse**（自托管 docker） | 全保留 |
| **Workflow 执行**（flow run、steps、状态、重试） | **Prefect 3 UI** | 30 天，老的归档 S3 兼容 blob |
| **Hermes runtime 内部** | hermes 自带 logging | 30 天 |
| **Sandbox 执行**（L3 测试 stdout/stderr） | `memory/.audit/skills/<ts>/` | 永久 |
| **Memory write trail** | `memory/.audit/<ts>-*.json` | 永久 |
| **每日 cost 报告** | Langfuse 聚合 + Prefect 周度推送 flow | — |
| **健康指标** | **Uptime Kuma**（一容器） | 30 天 |

**预算上限**：hermes LLM gateway 配 `daily_token_cap`，超额自动切便宜模型 + 推警告。

### 5.3 测试策略

| 层 | 单元 | 集成 | 端到端 |
|---|---|---|---|
| collectors / processors / notifiers | pytest，mock 网络 | live（`@pytest.mark.live`，CI 偶尔跑） | — |
| Workflow Python flows | 步骤函数纯函数化、可单测 | Prefect `@flow.test()` mock LLM | — |
| Skill 自创建（L3a） | 静态扫描器有单测 | sandbox 自动跑生成代码带的 pytest | Phase 4 三个验收用例 |
| Memory 系统 | frontmatter parser、索引重建 | propose → review → approve 三态 | 冲突场景剧本：手改 + agent 提议 |
| 整体日报链路 | — | — | **每日生产即测试**：连续 3 天指纹无变化触发告警 |

**Prompt 模板回归**：改 `templates/*.j2` **必须更新对应 golden output**（字符级 diff），CI 强制；这是"可复现"承诺的真正落地。

### 5.4 备份与灾恢

| 数据 | 频率 | 默认位置 | Opt-in 上云 |
|---|---|---|---|
| `memory/` 目录 | 每次 approve 后增量 | 本地 + 本地 git | 用户开启 → 私有 GitHub/Gitea |
| `~/.hermes/skills/` | 每次写入 | 本地 git | 同上 |
| `workflows/`、`templates/`、`topics.yaml` | 每次写入 | 本地 git | 同上 |
| Postgres（Prefect state） | 每日 pg_dump | 本地 | 用户开启 → S3 兼容 |
| Qdrant | 每周快照 | 本地 | 同上 |
| Hermes SQLite | 每日 cp + 滚动 7 份 | 本地 | 同上 |
| MinIO blob（采集原文） | 每周 mc mirror | 本地 | 同上 |

**默认所有备份本地**；上云需显式开启，不默认上传。

> Git 范围说明：`F:\codes\ISBE\` 仓库整体即一个 git 仓库，包含 `memory/`、`workflows/`、`templates/`、`topics.yaml`、`docs/` 等。**`~/.hermes/skills/` 因在用户主目录之下，单独 init 一个 git 仓库**——审核通过的 skill 落盘后由 audit hook 自动 commit。两者互不依赖，避免 ISBE 仓库被 hermes 内部状态污染。

**灾恢演练**：Phase 1 完成后做一次"删全部容器、从备份重建" drill，目标 < 2 小时跑通日报；演练记录写进 `memory/.audit/disaster-recovery/`。

### 5.5 不做的事

- ❌ 多区高可用（单节点 + 备份足够）
- ❌ APM（Langfuse + Prefect + Uptime Kuma 覆盖 90%）
- ❌ 完整的 e2e UI 自动化（hermes web/ 用手工冒烟）

---

## 6. 内容沉淀与检索

### 6.1 三层产物（C1 / C2 / C3）

"沉淀"实际包含 3 个不同抽象层的产物，存储介质 / 老化策略 / 检索路径都不同：

| 层 | 是什么 | 存储介质 | 保留 | 谁能写 |
|---|---|---|---|---|
| **C1 原始资料** | 抓到的文档本身 | MinIO blob *(source of truth)* + Postgres `documents` 元数据 + Qdrant `documents_chunks`（**可重建**） | 永久（季度老化标 archive 但不删） | collector skills |
| **C2 行为痕迹** | "我读过 / 处理过"的事实流 | `memory/<uid>/reading/raw-*.md` → 周度压缩为 `<YYYY-Wxx>.md` | raw 周度合并后删除；周度文件永久 | digest flow / chat agent |
| **C3 提炼洞察** | 多次接触后抽象出的模式 | `memory/<uid>/topics/*.md`（走 .pending 审核） | 永久（用户可手改） | weekly_insight + chat-triggered |

### 6.2 端到端生命周期

```
[1] collector skill 采集
    ↓ {url, title, content, metadata, fetched_at}

[2] 去重 (URL hash + content hash)
    ↓ 命中 → skip
    ↓ 内容变更 → 新版本，标记 supersedes，旧版仍保留

[3] 持久化（永不删，三处同步，写入 C1）：
    ├─ blob:    MinIO  raw/<source>/<yyyy-mm-dd>/<hash>.{html,pdf,...}   ← source of truth
    ├─ meta:    Postgres documents (id, url, hash, blob_path, source_id,
    │                              fetched_at, processed, read, archived,
    │                              supersedes: id?)
    └─ event:   "new document" 触发 [4]

[4] 处理（Prefect task chain，每步可独立重跑；blob 不变 → 永远可重建）：
    ├─ docling/marker → markdown
    ├─ semantic chunk
    ├─ LLM 单篇摘要（按 content_hash 缓存，相同 hash 不重算）
    ├─ topic 标签（topics.yaml 硬规则 + LLM 建议）
    └─ embed → Qdrant `documents_chunks`
    完成后：documents.processed = true

[5] 行为痕迹（写入 C2，被某个 flow / agent 实际"读"了）：
    memory/<uid>/reading/raw-<week>-<doc_id>.md   (5 行：title/url/summary/tags/read_at)
    完成后：documents.read = true

[6] 周度处理（P3+，每周一凌晨）：
    ├─ weekly_compact:  raw-*.md → memory/<uid>/reading/<YYYY-Wxx>.md
    │                   审核通过后删原始 raw md（C1 chunks/blobs 永不删）
    └─ weekly_insight:  扫上周 reading + 对应 chunks，按 topic 标签分组
                        每组 ≥3 文档 → LLM 提炼"本周新发现/共识/分歧"
                        → memory/.pending/topics/<x>.md  (写入 C3，走审核流)

[7] 季度老化（P5+）：
    documents.archived = true (12 月未访问)
    - chunks 仍在 Qdrant 但不进默认召回（include_archive=true 才命中）
    - blob 移到 cold/ 子目录
    - 用户访问任何 archived 文档 → 自动 unarchive

[8] Chat-triggered insight（任意时刻）：
    用户聊到某主题时，agent 检查：
      memory/topics/<x>.md 不存在 OR 距上次更新 > 7 天
      AND 当前 chunks 中该 topic 文档 ≥ N (默认 5)
    → 在回答末尾追加："要不要我整理一下这个主题的沉淀？"
    → 用户同意后走 [6] weekly_insight 同算法路径
```

### 6.3 documents 状态机

```
new ──processed──► processed ──read──► read ──(12月静默)──► archived
                                                              │
                                                              └─(用户访问)──► back to read
```

`processed` 与 `read` 是独立标志：

- **日报选材**：`processed=true AND read=false AND fetched_at > -24h`
- **研究 agent 主动探索**：`read=true AND archived=false`，内容仍鲜活
- **调试复现**：blob + chunks + documents 三元组按 `doc_id` 定位

### 6.4 检索接口矩阵

| 场景 | 引擎 | 命中啥 |
|---|---|---|
| 日报"昨日新增" | Postgres SQL | `documents WHERE fetched_at > -24h AND read=false` |
| Chat："最近 RAG 有什么新方法" | Qdrant `documents_chunks` | 语义检索 chunks → 找 source doc → 拼引用 |
| Chat："上个月谁提过 X" | Qdrant `memory_index`（reading 嵌入，P3+） | 命中 reading 周记录 → 跳 chunks |
| Chat："simonwillison 那篇关于 Y 的" | Postgres 全文 + Qdrant 联合 | 二阶段：源过滤 + 语义 |
| Chat："那篇我读过的 XXX" | hermes SQLite FTS5 | 会话上下文 |
| 调试："复现 3 周前某次输出" | blob + chunks + Postgres 三元组 | 走 doc_id |

### 6.5 Insight 双触发模式（P3+ 启用）

(a) **周度后台扫描** —— `weekly_insight` flow（被动主动推送）  
(b) **Chat-triggered** —— 用户聊到 + 积累阈值满足时即时提议

两者**都启用、都强制走 .pending 审核**。差异：(a) 是主动推送防遗忘，(b) 是即时性优先。  
用户拒绝 → 触发 §4.4 防递归条款（30 天 / 3 次截止）。

### 6.6 隐私与对用户完全开放

- **本地默认不出**：blob / chunks / `documents` 表 / reading / topics 全部本地
- **agent 调云端 LLM 时**只发命中的 chunks，不发全库；每次调用 trace 可查（Langfuse）
- **用户对所有 C1/C2/C3 产物有 100% 访问与删除权**：
  - `radar export raw <doc_id>` —— 取原始 blob
  - `radar inspect chunks <doc_id>` —— 看每个 chunk + 嵌入元数据
  - `radar forget <doc_id>` —— 级联删 blob + chunks + 引用，写 audit
  - reading/topics —— 直接 `vim` 文件
- **没有黑盒**：任何 agent 看到的内容，用户都能用同样接口看到

### 6.7 Hermes SQLite 边界（澄清）

hermes 自带的 FTS5 + Honcho 仅服务**会话级事实**（你跟它说过什么、聊过什么），**不沉淀采集到的文档**。文档沉淀完全走我们的四件套（Postgres + Qdrant + MinIO + `memory/reading`）。

> 简记：**hermes SQLite = "对话记忆"**；**我们的四件套 = "知识库"**。两者不重合，不互替。

---

## 附录 A — 备选方案 A：自建 Best-of-Breed 栈

仅当 §2.1 P0' 评估清单中关键硬性项（5/6/7）≥3 个 NO 时启用。

| 层 | 候选 |
|---|---|
| Agent 编排 | LangGraph（图式状态机） |
| 沙箱代码生成 | smolagents `CodeAgent` + E2B（云）/ Docker（自托管） |
| 长期记忆 | 我们的 `memory/*.md` 文件式（与路径 B 一致）+ 不带 hermes SQLite |
| LLM 网关 | LiteLLM |
| 向量库 | Qdrant |
| 采集插件库 | crawl4ai · firecrawl · feedparser · arxiv.py · docling/marker |
| 调度 | Prefect 3（与路径 B 一致） |
| 通知 | Apprise |
| Web UI | Open WebUI（MVP）→ Next.js（P5） |
| 可观测 | Langfuse + Prefect UI + Uptime Kuma（与路径 B 一致） |

切换成本：~3-5 周（主要在 P3-P5 增量）。

---

## 附录 B — 关键术语

- **L1 / L2 / L3 / L4** = 自扩展能力光谱：工具注册式 / 配置编排式 / **沙箱代码生成式（已选 = L3）** / 自修改式
- **L3a / L3b / L3c** = Skill 自创建 / Skill 自改进 / Workflow 自演化（仅 L3a 在 MVP 启用）
- **T1 / T2 / T3** = 记忆**加载**层级：索引 / 文件 / 语义检索（§4.2 / §4.7）
- **C1 / C2 / C3** = 内容**沉淀**层级：原始资料 / 行为痕迹 / 提炼洞察（§6.1）
- **B+C** = 行业情报雷达 (B) + 研究助理 (C)
- **D+E** = 长期记忆与个性化 (D) + Agent 自扩展能力 (E)
- **路径 A** = 自建 Best-of-Breed（备选方案，附录 A）
- **路径 B** = 在 hermes-agent 上叠加情报雷达层（**已选**）
- **草稿审核流** = 所有 agent 写入走 `.pending/` → `radar review` CLI → 落盘的统一流程

> 三套层级各管各的维度，不要混淆：L 系列描述"agent 能力"，T 系列描述"记忆怎么加载"，C 系列描述"内容怎么沉淀"。

---

## 附录 C — 参考资料

- OpenHands V1 Runtime Architecture: https://docs.openhands.dev/openhands/usage/architecture/runtime
- OpenHands Software Agent SDK: https://arxiv.org/html/2511.03690v1
- NousResearch hermes-agent: https://github.com/NousResearch/hermes-agent
- NousResearch hermes-agent-self-evolution: https://github.com/NousResearch/hermes-agent-self-evolution
- Hermes Agent docs: https://hermes-agent.nousresearch.com/docs/
- agentskills.io 标准（hermes skill 兼容）

---

*本 spec 由 brainstorming 全过程逐节确认产出；改动需走"提议 + 用户 approve"流程，与系统自身记忆审核流程一致。*
