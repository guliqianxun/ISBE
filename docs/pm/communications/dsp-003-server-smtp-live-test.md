---
pm_id: dsp-003
pm_type: dispatch
target: user
action: verify
feature: ft-001
priority: P1
status: pending
created_at: 2026-05-23
deadline: 2026-05-26
---

# 任务: 服务器配置 SMTP + 触发 nvda digest 实测落箱

## 背景

[dsp-002](dsp-002-email-digest-mvp.md) 的代码部分已完成（见 [rpt-002](rpt-002-email-mvp-report.md)）：
notify 邮件正文已升级到承载 digest 全文，`.env.example` 已注释清楚 7 项 `ISBE_SMTP_*`。

ft-001 的最后一步：**在服务器 `.env` 填你自己的 SMTP 凭据 → 触发一次 nvda digest →
确认收件箱真实收到一封含全文 digest 的邮件**。

这一步从一开始就归用户（凭据只进 server `.env`，不进仓库、不进对话）。

## 前置条件

- [dsp-001](dsp-001-deploy-latest-code.md) ✅ 已完成（worker 跑新代码）—— **注意**：本任务还需要先把 `feat/email-digest-delivery` 合到 main 并在 server 上 `git pull` 再次部署，因为新的 notify 代码还在分支上。

## 要求

### 1. 合并分支到 main（本机）

确认 [rpt-002](rpt-002-email-mvp-report.md) 描述的改动符合预期，然后：

```bash
# 清理 worktree（释放分支）
git worktree remove .claude/worktrees/agent-a17179ded6f9ae6a7

# 合并 + 推
git checkout main
git merge --no-ff feat/email-digest-delivery   # 或 ff-only / rebase，按你的偏好
git push origin main
```

### 2. server 上更新代码 + `.env` 加 SMTP 凭据

```bash
ssh lzhserver        # 或你的 ssh 别名
cd /mnt/nas_iscsi/isbe
git pull --ff-only origin main

# 编辑 .env，新增 7 项（参照 .env.example）：
#   ISBE_SMTP_HOST=...        # 例如 smtp.qq.com / smtp.163.com / smtp.gmail.com
#   ISBE_SMTP_PORT=587        # STARTTLS；465 走 SSL
#   ISBE_SMTP_FROM=...        # 发件人地址（通常 = USER）
#   ISBE_SMTP_TO=...          # 收件人（你自己的邮箱）
#   ISBE_SMTP_USER=...        # 登录账号
#   ISBE_SMTP_PASS=...        # 授权码（不是邮箱登录密码）
#   # ISBE_SMTP_ALLOW_PLAINTEXT=1   # 只有走本地受信中继才开
nano .env
```

> `src/` 是 bind-mount 进容器的，notify 的新代码会被 worker 立刻看见。
> 但 worker 进程已经载入了旧的 `os.environ` —— 改 `.env` 后需要重启 worker 让它读新环境变量。

```bash
docker compose -f docker-compose.yml -f docker-compose.server.yml \
  --profile worker up -d radar-worker
```

### 3. 手动触发一次 nvda digest

不等今天的 daily cron。从 Prefect UI 或 CLI 立刻跑一次：

```bash
# 进容器跑 CLI（避免本机依赖问题）
docker compose -f docker-compose.yml -f docker-compose.server.yml \
  --profile worker exec radar-worker radar topics run nvda --digest
```

或在 Prefect UI（`http://192.168.0.156:4200`）里找 `nvda-daily-digester/nvda-digester`
deployment 点 Run。

### 4. 验收落箱

- 收件箱在几分钟内应收到一封邮件
- subject：`[ISBE] NVDA 金融日报 — <period>`
- body：开头是 `Topic / Period / Artifact` 三行 + 分隔 + **当期 digest 全文**（你应该能直接在邮件里读完整内容）

## 验收标准

- [ ] 服务器 `.env` 包含 7 项 `ISBE_SMTP_*`（凭据不外泄）
- [ ] 触发 nvda digest 后，收件箱在 5 分钟内收到一封邮件
- [ ] 邮件正文不再只是路径 + excerpt，而是 digest 全文
- [ ] worker 日志无 `[notify] WARN` 类报错（在 `docker logs isbe-radar-worker | grep notify`）

## 失败回报

如果邮件没到 / 或日志报 `STARTTLS rejected` / 或登录失败 ——
把 `docker logs isbe-radar-worker --tail 30` 贴回来，PM 据此评估是否要开 follow-up
（可能需要切端口 465、或确认是授权码不是密码、或临时开 `ALLOW_PLAINTEXT=1`）。

## 完成后

发邮件到 PM：把收件箱里那封邮件的 subject + 第一段内容贴一下作为证据。PM 据此关闭
[ft-001](../features/ft-001-email-digest-delivery.md)（→ ✅ Completed），更新 ROADMAP + CHANGELOG。

## 参考

- Feature: [ft-001](../features/ft-001-email-digest-delivery.md)
- 前序: [dsp-002](dsp-002-email-digest-mvp.md) + [rpt-002](rpt-002-email-mvp-report.md)
- notify 实现（已合并后）：`src/isbe/notify/__init__.py`
