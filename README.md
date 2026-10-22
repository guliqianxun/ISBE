# ISBE — 你的私人研究助理

> Information System with Backbone of Evolution

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)

**中文** | [English](README.en.md)

写下你关心的研究方向，ISBE 替你把这一期的新论文读一遍：
- **采集**：按分类和关键词从 arXiv（或你自己的 essaies 论文库）拉论文，顺带跟踪 GitHub 仓库和 RSS
- **筛选**：标题规则加 LLM 逐篇判断，入选和剔除都写明理由，随时可以回看
- **读原文**：取 arXiv HTML 全文和原文配图，抽出事实卡片；引文以及其中的数字、网址都必须能在原文里找到
- **成稿**：生成中文或英文的日报 / 周报，在网页书架里阅读、下载 Markdown、打印 PDF，或手动发邮件
- **沉淀**：报告末尾提出对你研究记忆（memory）的修改建议，写成待审阅草稿，收不收由你决定

报告会读取你已经确认的记忆。**你维护的记忆越完整，下一期的分析就越贴近你自己的判断。**

---

## 它在解什么问题

你大概是这种人：

- 同时关注好几个方向，但**没时间每天刷 arXiv 和订阅源**
- 订阅源已经够多，结果**读不过来**，最后堆成"未读 999+"
- 直接问聊天机器人"最近有什么进展"——回答泛、**来源难核对**，也不记得你上次是怎么想的

ISBE 就是为此做的。它不是聊天机器人，而是**一个跑在你自己机器上的服务**：一个 Python 程序、一个 SQLite 数据库和一个本地数据目录，不需要单独部署数据库，也不需要构建前端。主题、报告和记忆都保存在本地。

---

## 它现在能干什么

| 功能 | 状态 |
| --- | --- |
| arXiv / essaies 论文采集、RSS 采集、GitHub 仓库动态 | 已支持 |
| 规则与 LLM 筛选、入选/剔除理由、核心/次级分类 | 已支持 |
| arXiv HTML 全文、原文配图与 LLM 事实抽取 | 默认路径；事实附引文并校验数字、网址，适合的配图存入本地报告 |
| metrail-web 解析论文 PDF、提取配图与事实卡片 | 可选的全文路径 |
| 论文主题中英文日报/周报、LLM 复核、Markdown 下载、浏览器打印 PDF | 已支持 |
| 固定材料中文专题研究：CLI 生成、冻结原文与引文、网页核验和下载 | 已支持指定材料摘录；不自动检索，尚需人工语义核验 |
| 来源管理：RSS／栏目配置、同站发现与试读、搜索分页、批量启停 | 已支持本机与共享管理员使用；尚未接入定时采集 |
| 主题联网检索、原文日期核对、材料汇编 | 通过 SearXNG 检索；未知和冲突日期保留 |
| 媒体正文保存、按议题组织的行业周报 | 支持编辑稿发布；尚无网页编辑器或自动编写下一期 |
| Web 总览、报告书架、筛选记录、运行与 LLM 调用记录 | 已支持 |
| 网页分步生成报告、持久任务队列与阶段进度 | 已支持论文主题；排队、历史、撤回排队任务 |
| 单机模式 / 共享服务器登录 | 已支持；共享用户的主题、报告与运行记录分开保存 |
| Web 设置页：模型服务、论文服务、API 地址与密钥 | 已支持 |
| Web 主题管理：创建、编辑、启停与删除主题配置 | 已支持；删除保留历史数据 |
| 手动邮件投递：当前历史报告、临时收件人、CID 内联配图、配置测试 | 支持通用 SMTP / ZeptoMail API 与页面配置；需实际收件验证 |
| 读取本地 memory、生成待审阅草稿 | 已支持；草稿不会自动写入正式记忆 |
| RSS 文章自动报告、知乎采集、memory 审批界面、内置定时调度、定时邮件通知、月报 | 尚未实现 |

模型服务支持 Anthropic、DeepSeek 和 OpenAI 兼容接口（包括本地免鉴权服务），在设置页切换即可。

---

## 怎么用

### 第一次运行（本地，uv）

准备 Python 3.14 与 [uv](https://docs.astral.sh/uv/)，依赖版本由 `uv.lock` 固定。在仓库根目录执行：

```bash
uv run isbe --data data init
cp examples/topics/nowcasting.yaml data/topics/
uv run isbe --data data topics
uv run isbe --data data serve
```

打开 [控制台](http://127.0.0.1:8080) → [设置](http://127.0.0.1:8080/settings)，选择模型服务、填写 API Key 并保存。默认的全文方式只用这一个模型，不需要另一个 API Key。

### 生成第一份报告

从设置进入 [主题管理](http://127.0.0.1:8080/topics)，编辑刚复制的 `nowcasting`，或创建自己的论文主题。保存后会打开分步指引，确认模型与主题配置，再点击「生成报告」。页面依次显示**采集 → 筛选 → 全文补充 → 报告生成**的进度，完成后直接打开本期报告。

也可以用命令行：

```bash
uv run isbe --data data collect nowcasting   # 采集论文与仓库动态，不需要 LLM Key
uv run isbe --data data digest nowcasting    # 筛选 → 全文补充 → 生成报告（不含采集）
```

筛选、全文和报告会调用模型，可能产生服务费用。提示"当前窗口无可用论文"时，先去筛选页看逐篇排除理由，见[排错说明](docs/configuration.md#网页生成任务与排错)。

### 用 Docker 跑

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

宿主机的 `data/` 需要容器内 UID/GID `1000:1000` 可读写。目录权限、环境变量和升级步骤见 [Docker 说明](docs/configuration.md#docker)。

### 让它定期跑

ISBE 目前**没有内置定时调度**：主题里的 `schedules` 只校验格式。需要自动运行时，用 cron 或服务管理器依次执行同一数据目录的 `collect` 和 `digest`，例如每周一早上：

```bash
0 8 * * 1  cd /path/to/isbe && uv run isbe --data data collect nowcasting && uv run isbe --data data digest nowcasting
```

避免同一主题的任务重叠，见[外部调度](docs/configuration.md#外部调度)。

### 加一个新主题

在 [主题管理](http://127.0.0.1:8080/topics) 用表单创建，或直接写 `data/topics/<ID>.yaml`（文件名与 `id` 一致），再运行 `isbe topics` 检查格式。可以从这两个示例改起：

| 示例 | 内容 |
| --- | --- |
| [nowcasting.yaml](examples/topics/nowcasting.yaml) | 中文论文周报：研究范围、排除规则、仓库跟踪和全文解析配置 |
| [rust-news.yaml](examples/topics/rust-news.yaml) | 英文 RSS 主题：目前支持采集与筛选，尚不能生成文章类报告 |

字段说明见[主题配置](docs/configuration.md#主题配置)。

### 不只是论文

| 场景 | 入口 |
| --- | --- |
| 管理 RSS / 网页栏目来源，收进材料收件箱 | 控制台「来源」页、`isbe sources`，见[新闻来源与材料收件箱](docs/configuration.md#新闻来源与材料收件箱) |
| 用 SearXNG 按主题联网检索媒体报道，核对原文日期 | 「联网检索」，见[来源接口](docs/source-interface.md#主题联网检索)和[本地 SearXNG](deploy/searxng/README.md) |
| 把检索材料整理成按议题组织的行业周报 | `isbe media-weekly`、`isbe media-briefing`，见[媒体材料与行业周报](docs/configuration.md#媒体材料与行业周报) |
| 基于指定材料摘录做中文专题研究，冻结原文与引文 | `isbe research <清单.json>`，见[固定材料专题研究](docs/configuration.md#固定材料专题研究) |
| 多人共用一台服务器 | `isbe serve --shared`，见[密钥与访问权限](docs/configuration.md#密钥与访问权限) |

### 产出在哪

| 路径 | 内容 |
| --- | --- |
| `data/reports/<主题>/` | 各周期 Markdown 报告 |
| `data/figures/` | 论文与导入报告的配图 |
| `data/research/<主题>/<版本>/` | 专题研究冻结的原件、材料文本和引用清单 |
| `data/memory/topics/` | 你维护的主题记忆 |
| `data/memory/.pending/` | 模型提出的待审阅草稿 |
| `data/settings.json` | 设置页保存的服务配置，可能含密钥 |
| `data/isbe.db` | 数据库：条目、筛选结果、任务队列、账户与 LLM 调用记录 |

备份时停止服务，备份整个数据目录，见[数据与备份](docs/configuration.md#数据与备份)。

---

## 怎么设计的

### 一条单向流水线

```
collect  →  triage  →  enrich  →  digest
采集         筛选        全文与事实卡片   报告（含 LLM 复核）
```

每个阶段只读前面阶段写下的数据，从不回调前一阶段；每个阶段只写自己的表。所有模型调用都记录在 `llm_calls` 里，可以在网页上逐条查看。

### 报告结构固定

论文报告分六段：**TL;DR → 论文逐篇 → 仓库逐条 → 名词 → 分析 → 蒸馏**。「分析」把本期事实与你的记忆对照，「蒸馏」提出的记忆修改只写进 `.pending/` 草稿。

### 记忆只由人来改

模型写不进正式记忆：草稿留在 `memory/.pending/`，由你审阅后手动合并。外部文本（RSS、网页、模型输出）一律视为不可信，渲染前先清洗，来自模型输出的路径写盘前先检查。

### 一个服务，一个数据目录

没有独立数据库、调度器或对象存储：SQLite（WAL 模式）加本地文件。essaies、metrail-web、SearXNG、模型服务和邮件服务都是可选的外部依赖。

详见 [设计文档](docs/DESIGN.md) 与 [架构说明](docs/architecture.md)。

---

## 已知短板

- 没有内置定时调度和定时邮件：自动运行要靠 cron 等外部工具
- 没有记忆审阅命令或页面：`.pending/` 草稿需要手动审阅、合并
- RSS 文章类主题只能采集和筛选，还不能生成报告；网页生成队列只处理论文主题
- 知乎采集、月报和周与周对比尚未实现
- 来源管理尚未接入定时采集；固定材料专题和行业周报的输入还不能在网页上编辑
- arXiv 并非每篇论文都有 HTML 全文，没有时报告只用摘要

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [docs/configuration.md](docs/configuration.md) | 完整参考：服务配置、邮件、全文抽取、密钥与共享部署、主题、命令、Docker、数据备份、开发 |
| [docs/source-interface.md](docs/source-interface.md) | RSS / 网页来源、联网检索方案、日期核对和材料收件箱 |
| [docs/DESIGN.md](docs/DESIGN.md) | 各阶段设计、数据模型和规划（英文） |
| [docs/architecture.md](docs/architecture.md) | 代码结构、兼容约定、开发约定与测试（英文） |
| [docs/upgrading-from-rust.md](docs/upgrading-from-rust.md) | 从早期 Rust 版迁移数据目录（英文） |
| [deploy/searxng/README.md](deploy/searxng/README.md) | 在本地运行 SearXNG |
| [docs/README.md](docs/README.md) | 文档索引 |

欢迎提交 Issue 和 Pull Request，开发与测试方式见[开发与贡献](docs/configuration.md#开发与贡献)。不要提交密钥、个人数据目录或带凭据的服务地址。

## License

MIT — see [`LICENSE`](LICENSE).
