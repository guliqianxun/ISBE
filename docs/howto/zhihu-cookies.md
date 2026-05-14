# 接入知乎数据源 — 获取 `d_c0` cookie

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
