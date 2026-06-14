---
status: Ready
date: 2026-06-14
target: 远端服务器 192.168.0.156（isbe-radar-worker, image isbe/radar:latest）
---

# 检索重构上线 runbook（nowcasting + video-generation 激活）

把 `refactor/architecture-cleanup` 的检索子系统 + 两个域的 `retrieval:` 契约部署到服务器。

## 0. 这次部署改了什么 / 风险面

- **新增**：`isbe.triage` 子系统 + nowcasting/video-generation 的 `retrieval:` 契约（source: facts）。
- **行为变化**：这两个域的周报，论文先过 triage 相关性门（out_of_scope 标题 + require_any 正向）再进 digest。其余域（motorcycle/nvda/...）无 `retrieval:` 块 → 直通，**逐字节不变**。
- **不涉及**：无 DB schema 变更（**无需 alembic upgrade**）、无新运行时依赖、无必需新 env（facts 模式读服务器自己 arxiv 采集的 Postgres，不依赖 S2/本地 DB）。
- **可达性**：服务器能直连 arxiv（本机才被 WAF 封），故 facts 模式数据来源不变，只多了 triage 门。

## 1. Pre-flight（dev 已确认）

- ✅ 201 passed（非集成全绿）、ruff clean。
- ✅ `git diff main..HEAD -- alembic/` 空（无迁移）；runtime deps 未变。
- ✅ 激活有安全网：无 `retrieval:` 块的域 contract=None → triage 直通。

## 2. 取代码到服务器

服务器从仓库目录 build（compose build context `.`）。二选一：
- **A（推荐，走 review）**：合 `refactor/architecture-cleanup` → `main`（PR 或本地 merge），服务器 `git pull` main。
- **B（直接部署分支）**：服务器上 `git fetch && git checkout refactor/architecture-cleanup && git pull`。

## 3. 重建镜像 + 重启 worker

```bash
# 服务器仓库根目录（含 docker-compose*.yml）
docker compose -f docker-compose.yml -f docker-compose.server.yml --profile worker \
  up -d --build radar-worker
```
- 重建 `isbe/radar:latest`（新 src + topic.yaml 打进镜像；server overlay 已带清华 PIP 镜像）。
- worker 起来会重新注册 Prefect deployments（`scheduler serve`），新代码生效。
- **无需** `alembic upgrade`（无迁移）。

## 4. Smoke（不等 cron，手动触发一次）

```bash
docker exec isbe-radar-worker radar topics run video-generation --digest
docker exec isbe-radar-worker radar topics run nowcasting --digest
```
看日志里的 triage 计数（`FT triage` / kept 数），确认门生效。

## 5. 验证

- 产物：`/mnt/nas_iscsi/isbe/artifacts/<topic>/<period>/latest.md` —— 论文条目应是**过门后**的（无关条目少了）。
- **重点查召回**：require_any 偏召回但仍可能误杀。扫一眼有没有该留的真域论文被弃（日志 drop 理由 `no in-domain signal`）。若误杀多 → §7 调。
- 对照：跑一个未激活域（如 motorcycle）确认产物不变。
- Phoenix（:6006）：LLM span 正常。

## 6. 回滚（激活有安全网，回滚廉价）

- 快速：删两个 topic.yaml 的 `retrieval:` 块（→ contract=None → 直通）→ 重建 worker。
- 或：`git revert 75d966f` → 重建。
- 其余域全程未受影响，无需动。

## 7. 上线后调参（都改 topic.yaml + 重建，无需改码）

- **误杀真域论文** → 放宽/精简 `require_any`（如去掉过窄词），或临时删 `require_any` 只留 `out_of_scope`。
- **漏进噪音** → 给 `out_of_scope_keywords` 加词（标题匹配）。
- **一词多义最后一公里**（radar=气象 vs LiDAR）纯规则不可解，周表量级靠眼筛；要根治需 stage-2 LLM-judge（用户当前不信任，未启用）。

## 附：暂不在本次范围

- 本地 `papers.db` 源上服务器（DB 在开发机，传输方案另议）——服务器走 facts。
- 周报正文按 核心/次级 **分层渲染**（digester 已能 triage，分层 rendering 是 `weekly.j2` 小后续）。
- image-restoration 等其余科研域的契约（先验证这两个）。
