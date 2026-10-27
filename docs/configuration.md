# 服务配置

设置页保存到所选数据目录的 `settings.json`。后续网页生成任务与使用同一个 `--data` 目录的 CLI 命令都会读取最新保存的配置；已经开始的任务不会中途切换配置。可以先保存不完整的模型配置，或单独保存来源服务；页面会明确显示模型配置尚不完整。设置页、总览和 CLI 使用相同的模型配置校验。“配置完整”不代表连接已验证；保存不会发送模型请求，真实任务成功后才能确认所用服务和模型能够响应。

新环境不必逐项填写：在设置页用「导入 JSON…」一次填入，或执行 `isbe --data DIR settings import 文件.json`；「导出 JSON」/ `isbe settings export` 可把现有配置带到别的数据目录。格式与密钥规则见 [settings.md](settings.md)，示例见 [examples/settings.example.json](../examples/settings.example.json)。

配置优先级为：**显式命令行参数（如提供） → 非空环境变量 → 设置文件 → 默认值**。设置页会标明被环境变量覆盖的字段；需要由页面管理时，应移除相应环境变量。单个主题的 `metrail.base_url` 优先于全局 `METRAIL_API_URL`。

| 设置 / 环境变量 | 用途与默认值 |
| --- | --- |
| `ISBE_LLM_PROVIDER` | `anthropic`（默认）、`deepseek`、`openai` |
| `ISBE_LLM_NO_AUTH` | 默认 `false`；仅 `openai` 可显式设为 `true`，用于本地免鉴权服务 |
| `ANTHROPIC_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` | 所选模型服务的密钥 |
| `ISBE_LLM_BASE_URL` | 模型 API 基地址；默认值见下表 |
| `ISBE_LLM_MODEL_FAST` | 筛选与全文事实抽取使用的模型 |
| `ISBE_LLM_MODEL_SMART` | 报告生成等任务使用的模型 |
| `ISBE_LLM_MAX_TOKENS` | 每次请求的输出上限；未设置时使用各任务自己的预算（报告为 24000）。模型服务以“超出上下文”拒绝且给出提示词与上下文长度时，会自动按剩余空间降低上限重试一次；服务不给出长度时，才需要手动设置 |
| `ISBE_LLM_TIMEOUT_S` | 模型请求超时，默认 900 秒；问题运行的每次调用最多 300 秒 |
| `ISBE_LLM_EXTRA_BODY` | 合并到模型请求的 JSON 对象，默认 `{}`；仅按服务协议设置 |
| `ISBE_FULLTEXT_EXTRACTOR` | `llm`、`metrail`、`off`；未设置时沿用未禁用的 metrail 配置，否则使用 `llm` |
| `ISBE_PAPER_SOURCE` | 问题研究（`/question`、首页提问）的论文来源：`openalex`（直接访问 OpenAlex API，无需自建服务）或 `essaies`。未设置时，已设 `ESSAIES_URL` 用 `essaies`（已有部署不变），否则用 `openalex` |
| `OPENALEX_API_URL` | OpenAlex API 基地址，默认 `https://api.openalex.org` |
| `OPENALEX_API_KEY` | 可选的 OpenAlex API Key（[免费申请](https://openalex.org/settings/api)），随每个请求发送；留空即关闭。不设时与同一 IP（含共用的 VPN 出口）的所有人共用每天 1000 credits（约 $0.10）。一次问题运行约用：每页检索 10 credits × 检索式数（常见 15–40 条）+ 候选批量查引文每 50 篇 1 credit + 每篇完整引文约 3 credits；有 Key 时完整引文最多 300 篇（约 900 credits），无 Key 时只查被引最多的 50 篇，给共享额度留余量。额度用完时提问报 `openalex_quota` 并给出重置时间，主页健康提示显示 `HTTP 429 (quota)` |
| `OPENALEX_MAILTO` | 可选的联系邮箱，作为 `mailto` 随请求发送给 OpenAlex；留空即关闭 |
| `ESSAIES_URL` | essaies 基地址。论文来源为 `essaies` 时必需（未设置时提问立即失败，`essaies_not_set`）；arXiv 搜索引擎只在 essaies 下可用。经典话题的采集可不设，此时从公开 arXiv API 采集 |
| `METRAIL_API_URL` | 可选的 metrail-web 基地址，用于 PDF 全文解析 |
| `GITHUB_TOKEN` | 可选，GitHub API 访问令牌 |
| `ARXIV_API_URL` | 默认 `https://export.arxiv.org/api/query` |
| `GITHUB_API_URL` | 默认 `https://api.github.com` |
| `SEARXNG_URL` | 主题联网检索的 SearXNG 基地址，需启用 JSON 输出；无默认服务 |
| `ISBE_EMAIL_DEFAULT_RECIPIENTS` | 可选默认收件人；设置页可保存、清空，发送时可临时修改 |

代码中的模型默认值：

| 服务 | API 基地址 | Fast / Smart |
| --- | --- | --- |
| Anthropic | `https://api.anthropic.com` | `claude-haiku-5-5` / `claude-sonnet-5-5` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` / `deepseek-chat` |
| OpenAI 兼容 | `https://api.openai.com/v1` | `gpt-5-mini` / `gpt-5` |

OpenAI 兼容服务使用 Chat Completions 接口；基地址需包含服务要求的路径（例如 `/v1`）。兼容服务对模型名称、输出预算字段和额外参数的支持可能不同。

本地服务确实不要求密钥时，选择 OpenAI compatible，在“本地免鉴权”中选择“不发送鉴权信息”（或设置 `ISBE_LLM_NO_AUTH=true`），填写地址和服务实际提供的 Fast / Smart 模型。此时 Key 可以为空，也不会发送已有旧 Key 的 `Authorization` 头。免鉴权只接受显式填写的 `localhost`、回环 IP、RFC 1918 私有 IPv4 或 IPv6 ULA 地址，例如 `http://127.0.0.1:11434/v1`、`http://192.168.1.10:8080/v1`；不接受公网地址或其他 DNS 名称。地址在私网不代表服务没有鉴权，需要密钥的本地服务仍应使用正常模式。Anthropic、DeepSeek 和普通 OpenAI 模式仍需对应 Key。

切换 Provider 会更新来源为“默认值”、且未手动编辑的地址及模型；已经保存在文件中、来自环境变量或本次显式输入的值会保留，即使恰好等于旧默认值。普通字段清空并保存后恢复当前 Provider 默认值；自动展示的新默认值不会被另存为固定覆盖值，因此保存、重载、切回服务后仍会跟随 Provider。

`ISBE_LOG_LEVEL`（`DEBUG`、`INFO`、`WARNING`、`ERROR`，默认 `INFO`）决定写到 stderr 的日志级别，只从环境变量读取；`GET /api/health` 报告 worker、论文来源（OpenAlex API 或 essaies）和模型是否可达（见 [docs/question/api.md](question/api.md#health)）。

`ISBE_DATA`（默认 `data`）和 `ISBE_BIND`（默认 `127.0.0.1:8080`）决定数据目录和监听地址，通过启动参数或环境变量设置，不在设置页修改。RSS 地址中的 `${VAR}`、`${VAR:-默认值}`、`${VAR:?错误信息}` 优先读取进程环境，也可引用已保存的服务配置。

环境变量示例见 [.env.example](../.env.example)。**程序不会自动加载 `.env` 文件**；请由 shell、服务管理器或 Docker `--env-file` 注入。

## 手动邮件投递

邮件服务支持通用 SMTP 和 ZeptoMail HTTPS API。管理员在「服务设置 → 报告邮件」选择发信方式，填写相应配置、发件邮箱与显示名称，保存后无需重启。SMTP 支持 TLS（默认端口 465）或强制 STARTTLS（默认端口 587），校验证书，不降级为明文；账号通常使用邮箱服务商提供的授权码。无需认证的中继可同时留空用户名和密码。配置保存在数据目录的私有 `settings.json`，凭据不会回传到页面。共享部署使用管理员的全局发信配置，普通用户可填写本次收件人，但不能修改发件服务。也支持以下环境变量；非空环境变量优先，并在页面中锁定对应字段：

| 环境变量 | 用途 |
| --- | --- |
| `EMAIL_TRANSPORT` | `smtp` 或 `zeptomail_api`。新配置默认 SMTP；旧配置已有 ZeptoMail 地址或 token 时继续使用 API |
| `SMTP_HOST` | SMTP 服务器域名或 IP，不含协议前缀和端口 |
| `SMTP_PORT` | 可选，1–65535；留空按加密方式使用 465 / 587 |
| `SMTP_SECURITY` | `tls`（默认）或 `starttls` |
| `SMTP_USER` / `SMTP_PASSWORD` | SMTP 用户名与密码/授权码；可同时留空使用无需认证的中继 |
| `ZEPTOMAIL_API_URL` | API 通道必填，账户已经验证的完整 HTTPS 发信地址，不提供默认值 |
| `ZEPTOMAIL_TOKEN` | API 通道必填，Send Mail Token；也接受已带 `Zoho-enczapikey ` 前缀的值 |
| `SMTP_FROM` | 必填，邮件服务商允许使用的发件邮箱 |
| `SMTP_FROM_NAME` | 可选的发件人显示名称 |

使用 ZeptoMail 时，沿用现有部署的账户与数据中心地址。当前 [Zoho 官方邮件 API 文档](https://www.zoho.com/cpaas/help/api/email-sending.html) 展示 CPaaS 地址，不能据此替换已有且验证过的 ZeptoMail 服务地址。接口使用 `Authorization: Zoho-enczapikey <token>`，发送 HTML 与纯文本正文，配图使用 `inline_images` 和 CID；不调用模型或重新生成报告。

在设置页保存可选默认收件人，或将其清空。打开某份报告后点击「发送邮件」，核对主题 ID、期号、标题与本次收件人，再确认发送。临时编辑只影响本次投递，默认设置保持不变；无默认值时也可直接填写。邮箱使用普通 ASCII 地址（国际域名可使用 Punycode），最多 20 个，以逗号、分号或空格分隔，后端会校验并去重。该列表作为邮件的 To 字段，收件人可见彼此地址。

先使用设置页的「发送测试邮件」并填写测试收件人，确认中文、链接、表格和内联配图显示正常，再投递实际报告。仅点击打开表单或保存设置不会发送邮件。配置状态表示字段有效，不代表远程凭据已经验证。

邮件入口必须显式指定主题与期号；报告不存在、文件缺失或路径不安全时直接拒绝，不回退到最新一期。只嵌入当前报告安全渲染后引用的本地 PNG、JPEG、WebP 配图；外部图片、不安全路径、缺图或格式错误会中止发送。单图上限 2 MiB，图片合计 5 MiB，完整邮件（API JSON 或 SMTP MIME）上限 10 MiB；超限不会悄悄删图后发送。WebP 显示效果取决于收件客户端。

界面区分「服务商已接受」「失败」「结果待确认」。接受请求不等于已经到达收件箱。API 请求最长 20 秒，SMTP 每次网络读写最长等待 20 秒；API 不跟随重定向，两种通道均不自动重试；超时、连接中断或不明确的服务商回复需先检查服务商日志与收件箱，避免重复投递。日志仅包含结果状态与经筛选的请求编号，不包含密钥或服务商原始报错。第一版不含订阅、定时投递、Webhook 或 PDF/Word 附件。

开发验证先运行 `scripts/py-test tests/py/mail` 的本地模拟服务和 `node --test web/*.test.cjs`；这不会发送真实邮件。真实投递与收件检查仍需部署者使用指定测试邮箱完成。

## 全文抽取

在设置页选择全文方式，或设置 `ISBE_FULLTEXT_EXTRACTOR`：

- `llm`：下载 arXiv HTML，按固定规则提取正文、标题、表格行和链接，保存适合的原文配图，再交给现有 Fast 模型生成文字事实卡片。引文以及事实中的数字、网址必须能在全文中找到，未通过校验的字段会被丢弃。配图不增加视觉模型调用；此模式不对 PDF 做 OCR 或识别图片内容，也不重建表格的合并单元格。
- `metrail`：从 essaies 或 arXiv 获取 PDF，交给 metrail-web 解析正文、表格与配图，再用同一套 LLM 事实抽取。需要配置 `METRAIL_API_URL` 或主题的 `metrail.base_url`。
- `off`：不再获取新全文或生成新事实卡片；数据库中已有的全文与卡片仍可用于报告。

未显式选择时，已配置 `METRAIL_API_URL` 或主题 `metrail.base_url` 且未禁用 metrail 的部署继续使用 metrail；其他部署使用 `llm`。主题的 `metrail.enabled: false` 只禁用 metrail，不会禁用 LLM 抽取；要停用新增全文与卡片，请选择 `off`。成功取得的全文和事实卡片会缓存，避免重复下载与模型调用；抽取提示词或全文变化后会重新生成卡片。

HTML 配图从有图注的原文图片中选择，优先方法、架构或流程图，其次概览图，再次为首个有图注的图；组图保留多个面板，存入本地 `figures/` 供报告展示。支持 PNG、JPEG、WebP；每个面板最多 2 MiB，整组最多 5 MiB、12 个面板，不截断组图。超限或任一面板下载失败时会记录原因，不采用不完整的组图，但保留正文并继续生成报告。原文没有合适配图时也仍生成文字报告。

超过 12 个面板、不安全 URL 等确定不支持的组图会记录拒绝原因，不会在每次运行时重复尝试。暂时性网络或下载失败则保留待补图状态，下次 `enrich` 可重试；已有 HTML 全文缓存尚未处理配图时也可补图。补图不替换已缓存的正文，也无需因此重新生成文字事实卡片。

arXiv 并非每篇论文都有可用的 HTML，转换结果也可能不完整，见 [arXiv HTML 说明](https://info.arxiv.org/about/accessible_HTML.html)。HTML 不可用时会记录摘要回退原因，不会因此单独判定 `enrich` 失败；数据库写入或事实卡片生成失败仍会报告错误。选择 `llm` 不会自动改用 metrail。两种全文路径都复用现有模型配置。

## 密钥与访问权限

设置页不会回传已保存的密钥；留空会保留原值，需要删除时使用明确的移除操作。密钥以明文保存在本地 `settings.json`，请限制数据目录的文件权限，并保护备份。

默认单机模式无需登录，只允许监听回环地址。共享服务器使用 `--shared`：用户登录后直接进入任务队列，不设逐项审批。普通用户只能管理自己的论文主题、任务和报告；管理员可以配置服务、创建/停用账户、重设密码和查看全局任务。密码保存为加盐 PBKDF2 哈希，会话令牌只保存哈希，24 小时到期；停用或重设密码会使原会话失效。

首次启动共享模式前，由部署者从受保护的临时文件创建管理员（文件中仅放密码，至少 12 个字符）：

```bash
isbe --data data account-add admin --password-file /secure/isbe-admin-password --admin
isbe --data data serve --shared --bind 127.0.0.1:8080
```

首次管理员接续现有本机主题和报告。其他账户的数据保存在各自工作区；管理员界面创建的账户为普通用户。密码文件使用后由部署者清理，不要放入仓库或命令行参数。共享部署应通过 HTTPS 反向代理提供访问，并为服务加入 `--secure-cookies`；该参数只设置 cookie 属性，不提供 TLS。代理需要传递 Cookie，并使用下述上游 Host 设置。

普通共享用户的来源地址由管理员配置，首版支持 arXiv 论文主题；不能覆盖来源服务地址或使用环境变量展开。每个主题最多采集 200 篇、评述 50 篇、回看 365 天，全文解析轮询最多 300 秒。

设置接口接受 `localhost` 或 IP 地址访问。使用域名反向代理时，请将上游 `Host` 设置为实际监听的 IP 地址与端口（如 `127.0.0.1:8080`）；仅监听本机时，端口也必须匹配。

`data/`、本地环境文件和 `settings.json` 已从 Git 与 Docker 构建上下文忽略。使用自定义数据目录时也应将其放在仓库外，避免提交报告、研究记忆和密钥。
