---
pm_id: rpt-002
pm_type: report
workstream: notify
feature: ft-001
dispatch_ref: dsp-002
status: completed
created_at: 2026-05-23
---

# 报告: 邮件 digest 投递 MVP — 代码部分完成

## 摘要

[dsp-002](dsp-002-email-digest-mvp.md) 的代码工作（step 1 `.env.example` 补全 +
step 2 notify 正文承载全文）已由 executor agent 在 worktree 中实现并通过 TDD 验证。
step 3（服务器 SMTP 配置 + 实测发信）按原计划归用户在 server 执行，已分派为
[dsp-003](dsp-003-server-smtp-live-test.md)。

## 交付

- 分支：`feat/email-digest-delivery`（未 push，未 merge）
- worktree：`.claude/worktrees/agent-a17179ded6f9ae6a7/`
- 两个 conventional commit：

| SHA | Title |
|-----|-------|
| `b931b2c` | docs(env): document ISBE_SMTP_* in .env.example |
| `2870ef2` | feat(notify): embed full digest body in email when artifact available |

`git diff --stat main..HEAD`：

```
 .env.example                | 16 +++++++++++
 src/isbe/notify/__init__.py | 38 +++++++++++++++++++------
 tests/test_notify_email.py  | 68 +++++++++++++++++++++++++++++++++++++++++++++
 3 files changed, 113 insertions(+), 9 deletions(-)
```

## 验收

| 验收项（来自 dsp-002）| 结果 |
|-----|------|
| `.env.example` 含 7 项 `ISBE_SMTP_*` 变量及注释 | ✅ |
| 邮件正文承载当期 digest 全文 | ✅（被 `tests/test_notify_email.py` 断言） |
| env 未配时 digest 流程仍正常完成（no-op 回归不破） | ✅（新增 no-op 测试覆盖） |
| `notify` 相关单测通过 | ✅ 11/11 |

## 测试结果

- `tests/test_notify_email.py`: **11 passed in 5.39s**
- 整套 `uv run pytest`: **157 passed, 3 failed in 831.83s**
- 3 个失败均为 PG 连接超时（dev 机本地无 PostgreSQL）：
  - `tests/test_facts_db.py::test_artifacts_and_topic_runs_tables_exist`
  - `tests/test_nowcasting_facts.py::test_tables_created`
  - `tests/test_nowcasting_facts.py::test_paper_roundtrip`
- 与本次变更无关（pre-existing 环境问题，未引入新失败）

## 派遣过程小记（PM 留档）

- 首个 executor agent（`a17179ded6f9ae6a7`，9 分钟 28 tool uses）写完了所有代码和测试、跑通了 notify 测试，**但只提交了第一个 commit 就退出**，留下未提交的 notify + test 改动。
- PM 验证 worktree → 确认实现已完成且测试通过 → 派第二个 agent（`ae30314f84844f9a2`，sonnet）完成第二个 commit + 全套测试。
- 经验：未来派遣 executor 时应在派遣单中**显式要求 agent 在结束前打印分支 + 全部 commit SHA**，否则容易掉中间步骤。

## 状态变更

- `dsp-002`: pending → **completed**（代码部分）
- `ft-001`: 仍 🚧 WIP（等 dsp-003 真实落箱后才视为完整闭环）
- 新建 `dsp-003` 用户向（server 上 `.env` + 触发 nvda digest + 验收落箱）

## 用户如何审阅这个分支

worktree 占着分支不能在 main 切过去，两个办法二选一：

```bash
# 办法 A：直接看 diff（无需切分支）
git diff main..feat/email-digest-delivery

# 办法 B：清理 worktree 后再切分支
git worktree remove .claude/worktrees/agent-a17179ded6f9ae6a7
git checkout feat/email-digest-delivery
```
