# ISBE v1 Acceptance Smoke Test

> **唯一通过条件**：新朋友拿到 `docker-compose` + `topics.yaml`，30 分钟内，明早 7:00 收到包含其新增 topic 的日报。

依据：`docs/superpowers/specs/2026-05-12-v1-scope-correction.md`。

---

## 前置假设（新机器）

- 装好 Docker Desktop + uv + git
- 克隆 ISBE 仓库
- 一份 `.env`（参考 `.env.example`）包含至少一个 LLM API key（DeepSeek / Anthropic 任一）
- 一个通知通道凭证（TG bot token / 邮件 SMTP）

## 步骤（手动 checklist）

1. `docker compose up -d` —— 7 个基础设施容器全 healthy
2. `uv sync --all-extras`
3. `uv run alembic upgrade head`
4. **编辑 `topics.yaml`**：添加一个新 topic（示例 `robotics`），声明 arxiv categories + 一条 cron schedule（如 `daily 06:00`）+ 通知目标
5. **不写任何 Python 代码**（这是关键约束）
6. `uv run radar scheduler serve` 或 `docker compose --profile worker up -d`
7. 等到次日 cron 触发，或手动跑：
   ```
   uv run radar topics run robotics --collect
   uv run radar topics run robotics --digest
   ```

## 通过判据（必须全部为 yes）

- [ ] `artifacts/robotics-YYYY-Wxx-digest.md` 文件存在
- [ ] 日报内容有完整三段（list / analysis / distillation 或金融域三段同构）
- [ ] 至少 5 条引用源链接
- [ ] 通知通道（TG / 邮件）实际收到摘要
- [ ] Phoenix UI 能看到这次 LLM 调用的 trace（含 prompt + output + tokens）
- [ ] 整条流程从步骤 1 到收到日报，**人工干预总时长 ≤ 30 分钟**

## Fail criteria（任一即失败）

- 需要修改任何 collector / digester Python 代码才能添加新 topic
- 步骤 1-7 任一步骤需要超过 5 分钟人工干预
- 日报里没有引用源 或 引用链接全部失效
- LLM 调用没有 trace 进 Phoenix
- 连续 3 天 cron 不触发（worker 死了无告警）

## 不在测试范围（v2 才验）

- chat / 聊天接口
- L3a 自扩展（agent 自己写 collector）
- agent-written memory 的 review 流
- 多用户隔离
- 看板 UI
- Qdrant 语义检索
- weekly_compact / weekly_insight

这些是 v2 候选。v1 不验。
