# 本机 SearXNG

使用官方镜像，固定已验证的 digest。服务仅监听 `127.0.0.1:48785`，供本机 ISBE 使用；配置和缓存放在已忽略的 `data/searxng/`。Docker 启动后，容器按 `unless-stopped` 策略恢复，不另建系统启动任务。

首次安装，在仓库根目录执行。Linux 或 macOS：

```bash
mkdir -p data/searxng/config data/searxng/cache
[ -e data/searxng/config/settings.yml ] && { echo '已有配置，请保留并按需修改'; exit 1; }
sed "s|REPLACE_WITH_RANDOM_SECRET|$(openssl rand -base64 32)|" \
  deploy/searxng/settings.example.yml > data/searxng/config/settings.yml
docker compose -f deploy/searxng/compose.yaml up -d
```

Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force data/searxng/config, data/searxng/cache | Out-Null
$configPath = Join-Path (Get-Location) 'data/searxng/config/settings.yml'
if (Test-Path -LiteralPath $configPath) { throw '已有配置，请保留并按需修改' }
$secretBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($secretBytes)
$template = Get-Content -Raw deploy/searxng/settings.example.yml
[IO.File]::WriteAllText($configPath, $template.Replace('REPLACE_WITH_RANDOM_SECRET', [Convert]::ToBase64String($secretBytes)), (New-Object Text.UTF8Encoding($false)))
docker compose -f deploy/searxng/compose.yaml up -d
```

已安装后只需最后一条启动命令。查看状态和日志：

```bash
docker compose -f deploy/searxng/compose.yaml ps
docker compose -f deploy/searxng/compose.yaml logs --tail 30
```

在 ISBE「服务设置 → 来源与联网检索」把 SearXNG 地址保存为 `http://127.0.0.1:48785`，再到「来源与材料 → 联网检索」保存并执行方案。配置已启用 JSON；不需要任何搜索 API 密钥。

当前保留搜狗、360、Yahoo、Bing 网页与 Bing 新闻。新闻引擎同时归入默认查询类别，让普通主题词也能查询新闻。测试时 Google 返回拒绝访问，百度和 DuckDuckGo 返回验证码，Brave 返回限流，因此未启用这些上游；结果随网络环境变化，可按需修改 `settings.yml`。各上游仍可能返回空结果、限流或拒绝访问，产品会保留部分失败状态。SearXNG 的日期提示不能替代原文发布日期核对。

如果所在网络把公网域名解析为 `198.18.x.x` 等虚拟地址，ISBE 会按非公网地址拒绝读取原文。可在服务设置选择「原文地址解析 → Cloudflare 加密 DNS」（配置名 `ISBE_ARTICLE_DNS=cloudflare`）。默认仍为 `system`；这一选择用于原文日期和正文读取，不修改系统 DNS。它把公开文章域名发给固定 Cloudflare HTTPS 端点，再检查完整地址集合并连接核对过的公网地址；私网、虚拟地址和不安全跳转仍被拒绝。当前实现只查询 IPv4 A 记录；解析失败保留读取失败状态，不回退到虚拟地址。

仅供本机使用，不能直接把端口绑定改成公网地址；需要多人访问时另行配置认证与反向代理。升级镜像前记录当前 digest，验证接口和实际搜索后再替换；不要覆盖随机密钥。

参考：[官方容器部署](https://docs.searxng.org/admin/installation-docker.html)、[配置继承与引擎选择](https://docs.searxng.org/admin/settings/settings.html)、[搜索接口](https://docs.searxng.org/dev/search_api.html)。
