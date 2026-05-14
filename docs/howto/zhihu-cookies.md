# 接入知乎数据源

知乎有两条路径，按需求挑：

| 路径                       | 覆盖                            | 是否要 cookies | 适用场景                          |
|----------------------------|---------------------------------|----------------|-----------------------------------|
| **morerssplz**（推荐）     | 只 `/zhuanlan/<slug>`（专栏）   | 不要           | 想拉头部 AI / 科技专栏 RSS        |
| **RSSHub + cookies**       | `/topic`/`/people`/`/zhuanlan`  | 要完整 jar     | 想拉话题 / 大 V 回答              |

如果你只想拉**专栏**，跳到下面的 [morerssplz 章节](#备选-morerssplz无 cookies专栏抓取)。
要拉**话题 / 大 V** 才走下面的 RSSHub + 完整 cookie jar 路径。

---

## RSSHub 路径 — 获取 `d_c0` cookie

知乎所有 `/api/v4/topics/*`、`/api/v4/columns/*`、`/api/v4/members/*/answers` 都用 `x-zse-96` 签名头守门。
RSSHub 2026-03-07 以后可以自己算签名 + 自动取 `__zse_ck`，但**前提是 `ZHIHU_COOKIES` 里同时包含 `d_c0` 和 `z_c0`**：

- `d_c0` = 设备 ID（写进签名公式）
- `z_c0` = 登录 token（解锁需登录才能看的回答）

只塞 `z_c0` 不够 —— 调研报告 2026-05-14 验证过：`/zhihu/daily` 200，所有 `/topic/*` 上游 403。

## 步骤（Chrome / Edge / Firefox 通用）

1. 登录 https://www.zhihu.com
2. F12 打开 DevTools → **Application** 标签页（Firefox 是 **Storage**）
3. 左侧树展开 **Cookies → https://www.zhihu.com**
4. 找两行：
   - Name = `d_c0` → 复制 Value（一般是 `AABB.....|123456789` 形）
   - Name = `z_c0` → 复制 Value（更长，`2|1:0|...` 起头）
5. 编辑项目根的 `.env`：

```
ZHIHU_COOKIES=d_c0=<刚刚 d_c0 的值>; z_c0=<刚刚 z_c0 的值>
```

   注意：**两个 cookie 之间用 `; ` 隔开**，不要带换行，整体放一行。

6. 重启 RSSHub 容器拿新环境变量：

```powershell
docker compose up -d --force-recreate rsshub
```

7. 验证：

```powershell
uv run python scripts/probe_zhihu_routes.py
```

   全部 200 + items > 0 即通过。

## 升级路径：粘"完整 cookie 字符串"（建议）

最稳的姿势是把整个 cookie jar 一次贴进去 —— 知乎的风控不只看 `d_c0` 一项，
完整 jar 含 `KLBRSID`、`_xsrf`、`q_c1`、`tst` 等会显著降低 403 概率。

获取步骤：

1. 登录 https://www.zhihu.com
2. F12 → **Network** 标签 → 刷新页面
3. 在请求列表里找一个对 `www.zhihu.com` 自己的请求（通常是文档请求或 `/api/v4/...`）
4. 右键 → **Copy** → **Copy as cURL (bash)**
5. 在粘出来的 curl 命令里找 `-H 'cookie: ...'` 那一行
6. 把单引号内的所有 cookie 内容复制出来，整段贴成 `.env` 的 `ZHIHU_COOKIES=...`

如果你 paranoid 想精简，至少保留：`d_c0 / z_c0 / KLBRSID / _xsrf / q_c1 / tst`
六项。完整 jar 也可以全留，知乎只看自己关心的那几个。

## 失败排查

- **全 503 + 上游 403**：cookies 过期（知乎 web cookie 大概 60 天滚动），重做步骤 4。
- **只有 `/zhihu/daily` 200，其他都 503**：`d_c0` 没填进去，或者写法错了（必须是 `d_c0=XXX; z_c0=YYY` 一行）。
- **某条路由 404**：URL token 错了。`/people/answers/<urlToken>` 中 `urlToken` 是浏览器 URL 里 `/people/` 后面那段，不是数字 UID。
- **全 200 但是 items=0**：上游账号被限流；切换号 / 等 24h。

## 已知不能用的姿势

- 只塞 `z_c0`（昨天 2026-05-13 试过，全部 403）
- Crawl4AI + OS Chrome + cookies（指纹检测会拦，跟签名是两层独立屏障）
- 任何 Python 端的 `feedparser` + cookies 直连知乎 API（没人在 Python 端实现 `x-zse-96`）

## 进度跟踪

- 2026-05-13: 接入 RSSHub 容器；只塞 `z_c0` → topic/zhuanlan/people 全 403
- 2026-05-14: 调研报告（[[oss-survey-first]]）发现需要 `d_c0`；准备脚手架，等用户粘贴
- 2026-05-15: 即便 `d_c0+z_c0` 齐齐塞进去 RSSHub 还是 403（疑似缺 `KLBRSID`/`_xsrf` 等
  风控字段）。**改走 morerssplz 兜底专栏，无 cookies**——见下一节。

---

## 备选：morerssplz（无 cookies，专栏抓取）

[lilydjwg/morerssplz](https://github.com/lilydjwg/morerssplz) 直接爬专栏页面 HTML
输出 RSS 2.0，**绕开签名问题**。代价是只覆盖 `/zhuanlan`，**没有** `/topic`、
`/people/answers`、`/question`。

### 拓扑

- 容器：`isbe-morerssplz`（compose service `morerssplz`，build from upstream git）
- 内部端口 :8000，本机映射 :1201（`docker-compose.override.yml`）
- 路由：`GET /zhihuzhuanlan/<column-slug>` —— 注意 `zhihu` 和 `zhuanlan` **中间无斜杠**
- 内网间访问用 `http://morerssplz:8000`，本机访问用 `http://localhost:1201`

### 操作步骤

1. 启动容器：

   ```powershell
   docker compose up -d morerssplz
   ```

2. 跑 smoke test：

   ```powershell
   uv run python scripts/probe_morerssplz.py
   ```

3. 加新专栏前，先用 `curl` 验证两件事：

   ```bash
   # 是否 200
   curl -sI http://localhost:1201/zhihuzhuanlan/<slug> | head -1
   # 是否近期还在更新（否则会被 lookback_days 过滤掉）
   curl -s http://localhost:1201/zhihuzhuanlan/<slug> | grep -oE '<pubDate>[^<]+' | head -3
   ```

4. 通过后把 `<slug>` 加进 `src/isbe/topics/china_tech/topic.yaml` 的 `rss.feeds` 列表，
   重新跑 `radar topics run china-tech --collect` 入库。

### 已知的"活栏目"种子（2026-05-15 验过）

- `jiqizhixin` —— 机器之心 / 研究综述
- `qbitai` —— 量子位 / AI 应用 + 产品

### 已知**不能用**

- `aiera`、`xinzhiyuan` 等：返回 200 但最新 pubDate 在 2025-10 / 更早，等于停更
- 单 V 用户 (`/zhihu/<id>`)、话题 (`/zhihu_topic/<id>`)、合集 (`/zhihu_collection/<id>`)：
  morerssplz 列了路由但目前 0.4 版本对 web 改版后的 `/api/v4` 反爬不太稳，PoC 之前别上线

### 失败排查

- **HTTP 404**：slug 拼错了，确认浏览器里 `zhuanlan.zhihu.com/<slug>` 能打开
- **HTTP 502 / 504**：上游知乎临时不稳，等几分钟重试
- **200 但 collector 0 入库**：feed 里 pubDate 都早于 `lookback_days`（默认 14 天）
- **容器死循环重启**：看 `docker logs isbe-morerssplz`，常见是 PyPI 拉包超时，重 build 即可
