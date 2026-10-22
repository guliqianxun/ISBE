# 配置与使用参考

本页是 [README](../README.md) 之外的完整参考：服务配置、邮件、全文抽取、密钥与共享部署、主题、来源与检索、固定材料专题、命令、Docker、行业周报、数据备份与开发。快速上手请先读 README。

- [网页生成任务与排错](#网页生成任务与排错)
- [服务配置](#服务配置)：[手动邮件投递](#手动邮件投递)、[全文抽取](#全文抽取)、[密钥与访问权限](#密钥与访问权限)
- [主题配置](#主题配置)：[外部调度](#外部调度)
- [新闻来源与材料收件箱](#新闻来源与材料收件箱)
- [固定材料专题研究](#固定材料专题研究)
- [命令](#命令)
- [Docker](#docker)：[升级](#升级)
- [媒体材料与行业周报](#媒体材料与行业周报)
- [数据与备份](#数据与备份)
- [开发与贡献](#开发与贡献)

## 网页生成任务与排错

网页任务先保存到 SQLite 队列，再由一个后台执行器按提交顺序运行。任务中心显示排队位置、阶段、结果和历史；每个账户最多保留 5 个排队或运行任务，可以撤回尚未执行的任务。重复提交同一个请求不会重复执行；同一账户同一主题已有活动任务时返回该任务。关闭对话框或刷新不会重复提交。重复生成同一周期会更新该周期的报告。

服务重启后继续处理排队任务，将此前执行中的任务标记为“已中断”，不自动重跑状态不明的模型调用。主题配置在提交时固定；若非敏感服务配置在排队期间改变，任务会明确失败并要求重新提交。模型密钥在执行时读取，不写入任务记录。CLI 与网页任务使用同一执行锁：已有任务运行时，CLI 会报告忙；CLI 执行期间网页任务继续排队。

CLI 仍可用于手动或外部调度：先执行 `collect`，再执行 `digest`。`digest` 依次执行筛选、全文补充和报告生成，**不会执行采集**。

如果提示“当前窗口无可用论文”，先在筛选页查看逐篇排除理由。`Gate dropped` 表示标题规则排除，`Judge dropped` 表示模型按检索意图和质量标准排除；标题没有命中“必须包含的关键词”时，即使摘要相关也不会送给模型。采集日志的 `new=0` 只表示没有新入库记录；筛选日志的 `to_judge=0` 只表示没有需要重新判定的候选。筛选日志同时显示 `window_candidates`、`cached` 和 `cached_kept` / `cached_gate_dropped` / `cached_judge_dropped`（非零项），用于区分无候选与复用既有判定。修改并保存筛选规则后会自动重新判定，无需删除历史数据；重复生成且不改规则会复用缓存。若希望覆盖标题未提及风等目标变量的方法论文，可清空标题必含词，并在质量标准中区分直接验证与方法参考，仍由模型排除无关内容。

仅采集不需要 LLM Key。`triage`、`enrich`、`digest` 和 `research` 需要模型配置；模型请求可能产生服务费用。

## 服务配置

设置页保存到所选数据目录的 `settings.json`。后续网页生成任务与使用同一个 `--data` 目录的 CLI 命令都会读取最新保存的配置；已经开始的任务不会中途切换配置。可以先保存不完整的模型配置，或单独保存来源服务；页面会明确显示模型配置尚不完整。设置页、总览和 CLI 使用相同的模型配置校验。“配置完整”不代表连接已验证；保存不会发送模型请求，真实任务成功后才能确认所用服务和模型能够响应。

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
| `ISBE_LLM_TIMEOUT_S` | 模型请求超时，默认 900 秒 |
| `ISBE_LLM_EXTRA_BODY` | 合并到模型请求的 JSON 对象，默认 `{}`；仅按服务协议设置 |
| `ISBE_FULLTEXT_EXTRACTOR` | `llm`、`metrail`、`off`；未设置时沿用未禁用的 metrail 配置，否则使用 `llm` |
| `ESSAIES_URL` | 可选的 essaies 基地址；未设置时从公开 arXiv API 采集论文 |
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

`ISBE_DATA`（默认 `data`）和 `ISBE_BIND`（默认 `127.0.0.1:8080`）决定数据目录和监听地址，通过启动参数或环境变量设置，不在设置页修改。RSS 地址中的 `${VAR}`、`${VAR:-默认值}`、`${VAR:?错误信息}` 优先读取进程环境，也可引用已保存的服务配置。

环境变量示例见 [.env.example](../.env.example)。**程序不会自动加载 `.env` 文件**；请由 shell、服务管理器或 Docker `--env-file` 注入。

### 手动邮件投递

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

### 全文抽取

在设置页选择全文方式，或设置 `ISBE_FULLTEXT_EXTRACTOR`：

- `llm`：下载 arXiv HTML，按固定规则提取正文、标题、表格行和链接，保存适合的原文配图，再交给现有 Fast 模型生成文字事实卡片。引文以及事实中的数字、网址必须能在全文中找到，未通过校验的字段会被丢弃。配图不增加视觉模型调用；此模式不对 PDF 做 OCR 或识别图片内容，也不重建表格的合并单元格。
- `metrail`：从 essaies 或 arXiv 获取 PDF，交给 metrail-web 解析正文、表格与配图，再用同一套 LLM 事实抽取。需要配置 `METRAIL_API_URL` 或主题的 `metrail.base_url`。
- `off`：不再获取新全文或生成新事实卡片；数据库中已有的全文与卡片仍可用于报告。

未显式选择时，已配置 `METRAIL_API_URL` 或主题 `metrail.base_url` 且未禁用 metrail 的部署继续使用 metrail；其他部署使用 `llm`。主题的 `metrail.enabled: false` 只禁用 metrail，不会禁用 LLM 抽取；要停用新增全文与卡片，请选择 `off`。成功取得的全文和事实卡片会缓存，避免重复下载与模型调用；抽取提示词或全文变化后会重新生成卡片。

HTML 配图从有图注的原文图片中选择，优先方法、架构或流程图，其次概览图，再次为首个有图注的图；组图保留多个面板，存入本地 `figures/` 供报告展示。支持 PNG、JPEG、WebP；每个面板最多 2 MiB，整组最多 5 MiB、12 个面板，不截断组图。超限或任一面板下载失败时会记录原因，不采用不完整的组图，但保留正文并继续生成报告。原文没有合适配图时也仍生成文字报告。

超过 12 个面板、不安全 URL 等确定不支持的组图会记录拒绝原因，不会在每次运行时重复尝试。暂时性网络或下载失败则保留待补图状态，下次 `enrich` 可重试；已有 HTML 全文缓存尚未处理配图时也可补图。补图不替换已缓存的正文，也无需因此重新生成文字事实卡片。

arXiv 并非每篇论文都有可用的 HTML，转换结果也可能不完整，见 [arXiv HTML 说明](https://info.arxiv.org/about/accessible_HTML.html)。HTML 不可用时会记录摘要回退原因，不会因此单独判定 `enrich` 失败；数据库写入或事实卡片生成失败仍会报告错误。选择 `llm` 不会自动改用 metrail。两种全文路径都复用现有模型配置。

### 密钥与访问权限

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

## 主题配置

在设置页进入 [主题管理](http://127.0.0.1:8080/topics)，可用表单创建、编辑、启用、停用或删除主题。主题 ID 创建后不可修改；名称等其他配置可以更新。保存后，后续网页生成任务和使用同一数据目录的 CLI 命令会读取新配置，不会改变已经开始的任务。

停用对应 `active: false`，仅将主题排除在不指定名称的批量任务之外；显式执行 `isbe collect <ID>`、`isbe digest <ID>` 等命令仍可运行它。启停与保存配置不会触发采集、生成报告或定时调度。

删除只移除该主题的配置文件，保留 `topics/` 目录及数据库记录、报告、图片与 memory。历史报告仍可在报告书架打开。之后若重新创建相同 ID，会接续原有历史；需要独立主题时请使用新 ID。

删除后，书架和阅读页使用报告原有一级标题作为名称；没有标题时才使用主题 ID。标题中已有当前期号时不再额外显示封面期号。报告文件和正文不会被改写。

表单修改会保留原配置中未显示的字段；保存时重新序列化 YAML，**不保留原注释和排版**。RSS URL 中的 `${VAR}` 等占位符会原样保存在文件中，运行时再展开。

也可以直接维护 `data/topics/<ID>.yaml` 或 `.yml`，文件名必须与 `id` 一致。同一 ID 不应同时存在两种扩展名的文件；管理接口会拒绝这种冲突。示例：

- [nowcasting.yaml](../examples/topics/nowcasting.yaml)：中文论文周报，包含研究范围、排除规则、仓库跟踪和全文解析配置。
- [rust-news.yaml](../examples/topics/rust-news.yaml)：英文 RSS 主题；目前支持采集与筛选，尚不能生成文章类报告。

手动修改后运行 `isbe topics` 检查格式。`lang` 选择 `zh` 或 `en`，`cadence` 选择 `daily` 或 `weekly`，`window_days` 决定回溯窗口。

`arxiv` 配置论文分类与关键词，`retrieval` 定义筛选范围，`digest` 控制逐篇评述数量与输入预算。完整字段以 [isbe/topics/model.py](../isbe/topics/model.py) 为准。

启用 arXiv 时，分类和关键词至少各填一项，每行一项。分类代码示例：`cs.LG`（机器学习）、`cs.AI`（人工智能）、`cs.RO`（机器人）；范围说明见 [arXiv 官方分类表](https://arxiv.org/category_taxonomy)。查询按 `(分类 A 或 B) 且 (标题/摘要命中短语 X 或 Y)` 组合，例如分类 `cs.RO`、`cs.AI` 配合关键词 `robot learning`、`planning`。这不是要求所有关键词同时出现；后续 `retrieval` 筛选规则还会进一步筛选结果。

新主题保存后会打开第一份报告的分步对话框，空报告页也提供入口。确认模型配置与主题来源后，显式点击「生成报告」才会开始任务。没有可用论文时，页面会提示检查来源、关键词和筛选条件；模型或生成失败会显示安全的操作提示。报告成功完成后可以直接打开，失败时不会把已落盘的部分报告误报为成功。网页生成指引和 `digest` 当前只支持论文主题；RSS 文章主题可采集、筛选，尚不能自动生成文章报告。固定材料专题使用下述 `research` 命令。

### 外部调度

`weekly` / `daily` 只决定报告周期；主题“配置有效”、保存或启用均不表示任务已运行，也不会创建定时任务。运行记录显示实际执行的任务结果，模型配置状态不等于连接验证结果。需要自动运行时，由系统任务调度器、cron 或服务管理器依次执行同一数据目录的 `isbe --data <目录> collect <主题>` 和 `isbe --data <目录> digest <主题>`，检查采集成功后再运行生成；避免同一主题的任务重叠。当前 `schedules` 字段只校验格式，不会被服务执行。

## 新闻来源与材料收件箱

控制台「来源」页支持手动配置 RSS／Atom 和网页栏目、JSON 导入导出、指定网站内发现与试读。默认自动启用试读成功的新来源，已停用来源保持停用。收件箱保留来源、原文链接、独立时间字段与内容版本；此阶段不调用模型。命令行可用 `isbe sources import <配置文件>` 和 `isbe sources collect`。详见 [来源接口使用与边界](source-interface.md)。

「联网检索」可保存主题、检索词与日期窗口，通过已配置的 SearXNG 发现媒体报道，核对原文日期并进入同一材料箱。未知日期、范围外和冲突记录均可查看；同方案相同网址合并，原始命中保留。当前手动执行、每词首批有界结果，不调用模型或自动生成报告。见[检索方案示例](../examples/search/shanghai-esg-green-finance.json)和[使用说明](source-interface.md#主题联网检索)。示例的“近 30 天”固定为 2026-09-20 至 2026-10-19，复用时须修改日期窗口及含年月的检索词。

来源管理台按网站、专题、状态搜索和分页，每页最多 50 个来源，支持当前页所选来源的批量启用／停用。配置详情按需打开，材料与采集记录分别查看；全库统计和筛选结果计数分开显示。

[上海 ESG 与绿色金融真实来源样例](../examples/sources/shanghai-esg-green-finance.json) 包含交易所、银行、研究机构、媒体与行业协会的 10 个已实采入口，可直接导入；范围及当前限制见[示例说明](source-interface.md#示例来源配置)。

## 固定材料专题研究

`research` 从 JSON 清单读取研究问题、来源元数据和摘录，调用已配置的 Smart 模型提取主体、对象、时间线索、行动要求及数据指标原文，并保存原件、材料文本、引文位置及内容梳理报告。模型不生成结论、推断、评分、证据缺口或建议，不决定纳入、排序、停止与验收。程序只接受所属摘录中的原样连续片段，并按输入顺序完整展示每份来源的全部摘录；空字段表示未标注，不代表原文没有。行动及指标可包含计划和目标值，不自动成为实际进展；原文与抽取线索同时展示，角色、时间及目标/实绩关系仍需人工核对。

此命令不自动检索、下载或解析来源；PDF/HTML 原件和相应 UTF-8 文本须事先准备。程序校验引文唯一命中、材料 hash、来源完整性和字段原文匹配；这些检查不等于材料真实性或语义角色正确性。旧论文 `triage` / `digest` 路径仍使用模型筛选和复核，尚未按本专题的职责边界改造，不是本次研究入口。

已取得的模型回复中，不符合连续原文的片段会被弃用；单个栏位超过 8 个片段时整栏弃用，不选择前几条，也不影响其他有效栏位。结构、字段内容或引用不合法时，整次模型标注弃用，来源及完整摘录仍照常展示。弃用数量或错误原因进入运行记录；不代表内容质量分数，也不自动触发模型重试。模型服务请求失败、输入材料校验失败等运行错误仍会中止本次命令并保留尝试记录。

可先使用 [合成输入清单](../examples/research/fixed-source.json) 与 [合成材料](../examples/research/fixed-source.txt) 演示。**示例不对应真实企业，不能用于研究结论；执行仍会真实调用模型。** 先按上文配置同一数据目录的模型服务，再从仓库根目录运行：

```bash
uv run isbe --data data init
uv run isbe --data data research examples/research/fixed-source.json
uv run isbe --data data serve
```

打开 [报告书架](http://127.0.0.1:8080/reports)，选择“合成演示”报告。阅读页可下载 Markdown；点击提取片段或“查看这段原文”，可核对引文、保存的材料文本、使用范围和限制，并下载本期冻结原件。专题目前由 CLI 提交，网页提供报告和证据的只读查看；现有“生成报告”指引仍只处理论文，不支持在网页输入专题材料。

真实使用时复制并修改清单：`scope` 记录问题、地域、语言及日期范围；`sources` 记录来源、覆盖期、读取深度、限制和摘录。`original_file` 与 `text_file` 相对清单所在目录，不能越界或经符号链接；`sha256` 必须是原件当前字节的 SHA-256，修改原件后须更新。原件支持 PDF、UTF-8 HTML 或文本，每份不超过 10 MiB；对应文本不超过 200 KiB，最多 16 份材料、64 条证据。每条 `quote` 必须在对应文本中恰好出现一次。

`topic` 与 `period` 确定报告版本。专题不会覆盖已有版本；开始模型请求前会保留冻结目录，之后即使生成失败也保留该次尝试。确认失败或不明确结果后，显式换一个新 `period` 重试，不删除旧目录来重复同一次请求。原件和证据保存在 `research/<topic>/<period>/`，报告位于 `reports/<topic>/<period>.md`，备份时一并保留。

来源可填写 `publisher`（发布机构），报告按输入顺序展示每份来源，机构与完整标题链接直达发布页。未填写的旧清单仍可使用，显示“发布机构未注明”。来源元数据由程序展示，不由模型推断发布者就是执行者；核验记录在来源页默认收起，旧版报告继续保留。

## 命令

在仓库目录中，以下 `isbe` 写作 `uv run isbe`；Docker 镜像的入口就是 `isbe`，见下节。

| 命令 | 作用 |
| --- | --- |
| `isbe init` | 创建数据目录与数据库；可重复运行 |
| `isbe topics` | 校验并列出主题 |
| `isbe collect [主题…]` | 采集论文、RSS 与 GitHub 仓库动态 |
| `isbe triage [主题…]` | 对窗口内的条目进行筛选 |
| `isbe enrich [主题…]` | 补充待评论文的全文、配图与事实卡片 |
| `isbe digest [主题…]` | 筛选、补充全文、生成论文报告 |
| `isbe research <清单.json>` | 从固定材料摘录生成中文专题，冻结原件与引用；同版本不覆盖 |
| `isbe sources …` | 配置、发现并采集 RSS／网页来源，管理联网检索方案；不调用模型，见[来源接口](source-interface.md) |
| `isbe media-weekly <方案 ID> --topic <主题> --week-end <日期>` | 把已保存的检索候选整理成不可变的媒体周报草稿；不调用模型 |
| `isbe media-briefing <编辑稿.json> --topic <主题> --source-period <期号>` | 发布编辑稿行业周报，见下文「媒体材料与行业周报」 |
| `isbe serve [--bind 地址] [--shared] [--secure-cookies]` | 启动单机控制台或共享登录服务 |
| `isbe account-add <用户名> --password-file <文件> [--admin]` | 部署者创建账户；首次共享启动需管理员 |
| `isbe import-reports <目录>` | 导入 `<主题>/<周期>/latest.md` 布局的旧版报告 |

所有命令支持 `--data <目录>`。省略主题名时处理所有启用的主题；具体参数见 `isbe --help` 或 `isbe <命令> --help`。

## Docker

先准备宿主机 `data/`，确保容器内 UID/GID `1000:1000` 可读写。Linux 可用 `sudo install -d -m 700 -o 1000 -g 1000 data` 创建目录；复制主题时也需要相应权限。

```bash
docker build -t isbe .
docker run --rm -v "$PWD/data:/data" isbe init
cp examples/topics/nowcasting.yaml data/topics/
# ISBE_ADMIN_PASSWORD_FILE 指向仓库外、仅含初始密码的受保护文件。
docker run --rm -v "$PWD/data:/data" -v "$ISBE_ADMIN_PASSWORD_FILE:/run/admin-password:ro" \
  isbe account-add admin --password-file /run/admin-password --admin

docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD/data:/data" \
  isbe serve --shared --bind 0.0.0.0:8080
```

在浏览器中保存设置后，从另一个终端运行：

```bash
docker run --rm -v "$PWD/data:/data" isbe collect nowcasting
docker run --rm -v "$PWD/data:/data" isbe digest nowcasting
```

每次运行共享同一个数据挂载。若采用环境变量配置，可在镜像名 `isbe` 前加入 `--env-file .env`；它会覆盖设置页保存的对应值。容器内监听 `0.0.0.0`，上面的端口映射仍只开放给宿主机本地访问。

构建镜像时 Docker Hub 与 PyPI 镜像源的可选参数见 [Dockerfile](../Dockerfile)。

### 升级

升级前停止服务，并备份整个数据目录（见下文「数据与备份」）：新版程序会按需迁移数据库，旧版不能打开已升级的数据库。不要让两个实例同时使用同一个数据目录。

```bash
git pull --ff-only
docker build -t isbe .
docker run --rm isbe --version
docker run --rm --entrypoint python isbe -c "import isbe.web.server; print('ok')"
```

确认 `--version` 显示预期版本、第二条命令输出 `ok` 后，再用新镜像重启服务。从早期 Rust 版升级见 [docs/upgrading-from-rust.md](upgrading-from-rust.md)。

## 媒体材料与行业周报

来源页的“生成材料汇编”按日期保存检索候选及搜索摘要，供编写周报使用。报告中的“展开原文”读取媒体正文，成功后保存在本地数据库；已保存的正文可重复阅读。尚未适配的页面与网站暂不可达分别提示，不会用搜索摘要冒充正文。

行业周报采用“本周速览 → 按议题整理的进展与背景 → 已公布的后续安排”，每条保留来源、标题及链接。同一事件可以合并引用多个来源。当前为编辑稿发布流程，尚未接入自动撰稿或网页编辑器：

```bash
isbe --data data media-briefing draft.json --topic shanghai-esg-green-finance --source-period 2026-W42-media
```

编辑稿字段为 `title`、`intro`、`sections`（`title`、`items`）、可选 `schedule` 和 `scope_note`。每个条目包含 `headline`、`body` 段落数组、`source_ids` 和 `evidence`；每条证据用 `source_id`、从零开始的 `paragraph`、原文中的连续 `quote` 定位。可选 `data_rows` 包含 `label`、`value`、`scope` 及单条 `evidence`；日程包含 `when`、`arrangement` 和同样的证据定位字段。

来源须来自指定材料汇编并已成功读取原文。程序核对引文是否存在、冻结引用的正文，另存为 `YYYY-Www-briefing`；再次发布创建新版本，原报告与材料库保留。引文校验不验证改写的语义，正文、数据口径与事件归组仍需编辑核对。此命令不调用模型，也不联网。

## 数据与备份

```text
data/
├── isbe.db             # 本机/首位管理员数据、账户、会话与任务队列
├── workspaces/<用户ID>/ # 其他账户的数据库、主题、报告、图片与 memory
├── settings.json       # 设置页保存的服务配置，可能含密钥
├── topics/             # 网页或人工维护的主题 YAML
├── reports/<主题>/     # 各周期 Markdown 报告
├── research/<主题>/<版本>/ # 专题冻结原件、材料文本和引用清单
├── figures/            # 论文与导入报告的配图
└── memory/
    ├── topics/         # 人工维护的主题记忆
    └── .pending/       # 模型提出的待审阅草稿
```

正式 memory 是带 YAML frontmatter 的 Markdown。报告会读取该主题的 `topic` 笔记以及 `feedback`、`user` 笔记；`.pending/` 不参与读取。当前需人工审阅并维护正式笔记，尚无 `review` 命令。

升级数据库前先保留整个数据目录备份；旧版程序不能打开升级后的新数据库。备份前停止写入数据的命令与服务，再备份整个数据目录；仅备份数据库无法保留报告、图片与记忆。LLM 调用记录包含提示词和回复，备份应按研究数据保护。

## 开发与贡献

```bash
scripts/py-test                  # Python 测试，在固定的 python:3.14-slim 容器中运行
node --test web/*.test.cjs tests/*.test.cjs
```

前端为 `web/` 中的原生 HTML/CSS/JavaScript，随 Python 包打入镜像，无 npm 构建步骤；服务设置与主题管理的交互测试使用 Node.js，无额外依赖。在仓库中运行时直接读取 `web/` 与 `prompts/`，修改后重启服务即可；Docker 镜像需重新构建。代码结构与约定见[架构说明](architecture.md)，各阶段的设计见 [DESIGN.md](DESIGN.md)。

`examples/research/handoff.json` 是合成的多源专题研究交接示例，可运行 `node --test tests/research-handoff.test.cjs` 检查；示例通过不代表对应的生产能力已实现。

欢迎提交 Issue 和 Pull Request。问题报告请附复现步骤、版本与脱敏日志；行为变更请附相应测试。不要提交密钥、个人数据目录或带凭据的服务地址。
