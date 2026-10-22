# ISBE — your personal research assistant

> Information System with Backbone of Evolution

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.14](https://img.shields.io/badge/python-3.14-blue.svg)](https://www.python.org/)

[中文](README.md) | **English**

Write down the research directions you care about, and ISBE reads this period's new papers for you:
- **Collect**: pull papers from arXiv (or your own essaies archive) by category and keyword, and follow GitHub repositories and RSS feeds along the way
- **Triage**: title rules plus a per-paper LLM judgment, with a written reason for every paper kept or dropped, which you can review at any time
- **Read the source**: fetch the arXiv HTML full text and its figures, and extract fact cards; every quote, and every number and URL in it, must be found in the paper
- **Write**: produce a daily or weekly report in Chinese or English, read it on the web bookshelf, download the Markdown, print to PDF, or email it manually
- **Distill**: the report ends with proposed changes to your research memory, written as drafts for review; you decide what to keep

Reports read the memory you have confirmed. **The more you curate your memory, the closer the next report's analysis comes to your own judgment.**

---

## The problem it solves

You are probably someone who:

- follows several fields but **has no time to check arXiv and feeds every day**
- already has plenty of subscriptions and **can't keep up**, ending with "999+ unread"
- asked a chatbot "what's new in X lately" and got a vague answer, **with sources that are hard to check**, and no memory of what you thought last time

ISBE is built for that. It is not a chatbot but **a service that runs on your own machine**: one Python program, one SQLite database and one local data directory, with no separate database server and no front-end build. Topics, reports and memory are all stored locally.

---

## What it can do today

| Feature | Status |
| --- | --- |
| arXiv / essaies paper collection, RSS collection, GitHub repository activity | Supported |
| Rule and LLM triage, keep/drop reasons, core/secondary classification | Supported |
| arXiv HTML full text, source figures and LLM fact extraction | Default path; facts carry quotes with checked numbers and URLs, and suitable figures are stored with the local report |
| metrail-web PDF parsing, figure and fact-card extraction | Optional full-text path |
| Daily/weekly paper reports in Chinese and English, LLM review, Markdown download, print to PDF from the browser | Supported |
| Fixed-material Chinese research reports: generated from the CLI, frozen sources and quotes, verification and download on the web | Supported for given source excerpts; no automatic retrieval, and semantic checks are still manual |
| Source management: RSS / section pages, same-site discovery and trial reads, search and paging, bulk enable/disable | Supported locally and for shared-mode admins; not yet wired to scheduled collection |
| Topic web search, source date checks, material compilations | Search through SearXNG; unknown and conflicting dates are kept |
| Saved media article text, industry briefings organized by issue | Publishing from an editor's draft; no web editor and no automatic next issue yet |
| Web overview, report bookshelf, triage records, runs and LLM call records | Supported |
| Step-by-step report generation on the web, durable task queue with stage progress | Supported for paper topics; queueing, history, withdrawing queued tasks |
| Single-user mode / shared server with login | Supported; shared users' topics, reports and runs are stored separately |
| Web settings: model service, paper services, API addresses and keys | Supported |
| Web topic management: create, edit, enable/disable and delete topic configs | Supported; deleting keeps the history |
| Manual email delivery: a past report, ad-hoc recipients, CID inline figures, configuration test | Generic SMTP / ZeptoMail API, configured on the page; check real delivery yourself |
| Reading local memory, writing drafts for review | Supported; drafts never enter the live memory automatically |
| Automatic RSS article reports, Zhihu collection, memory review UI, built-in scheduler, scheduled email, monthly reports | Not implemented |

Model services: Anthropic, DeepSeek and OpenAI-compatible APIs (including local services without authentication), switched on the settings page.

---

## How to use it

### First run (local, uv)

You need Python 3.14 and [uv](https://docs.astral.sh/uv/); dependency versions are pinned in `uv.lock`. From the repository root:

```bash
uv run isbe --data data init
cp examples/topics/nowcasting.yaml data/topics/
uv run isbe --data data topics
uv run isbe --data data serve
```

Open the [console](http://127.0.0.1:8080) → [Settings](http://127.0.0.1:8080/settings), choose a model service, enter its API key and save. The default full-text path uses that same model, so no second API key is needed.

### Your first report

From Settings, open [Topics](http://127.0.0.1:8080/topics) and edit the `nowcasting` topic you just copied, or create your own paper topic. Saving opens a step-by-step guide: confirm the model and topic configuration, then click "Generate report". The page shows progress through **collect → triage → full text → report**, and opens the report when it is done.

Or from the command line:

```bash
uv run isbe --data data collect nowcasting   # collect papers and repo activity; no LLM key needed
uv run isbe --data data digest nowcasting    # triage → full text → report (does not collect)
```

Triage, full text and reports call the model, which may cost money. If you see "no papers available in the current window", check the per-paper drop reasons on the triage page; see [troubleshooting](docs/configuration.md#网页生成任务与排错) (Chinese).

### Run with Docker

```bash
docker build -t isbe .
docker run --rm -v "$PWD/data:/data" isbe init
cp examples/topics/nowcasting.yaml data/topics/
# ISBE_ADMIN_PASSWORD_FILE points to a protected file outside the repository that holds only the initial password.
docker run --rm -v "$PWD/data:/data" -v "$ISBE_ADMIN_PASSWORD_FILE:/run/admin-password:ro" \
  isbe account-add admin --password-file /run/admin-password --admin

docker run --rm -p 127.0.0.1:8080:8080 -v "$PWD/data:/data" \
  isbe serve --shared --bind 0.0.0.0:8080
```

The host `data/` directory must be readable and writable by UID/GID `1000:1000` inside the container. Directory permissions, environment variables and upgrades are covered in the [Docker notes](docs/configuration.md#docker) (Chinese).

### Run it on a schedule

ISBE **has no built-in scheduler** yet: a topic's `schedules` are only validated. To run automatically, have cron or a service manager run `collect` and then `digest` on the same data directory, for example every Monday morning:

```bash
0 8 * * 1  cd /path/to/isbe && uv run isbe --data data collect nowcasting && uv run isbe --data data digest nowcasting
```

Avoid overlapping runs of the same topic; see [external scheduling](docs/configuration.md#外部调度) (Chinese).

### Add a topic

Create one with the form under [Topics](http://127.0.0.1:8080/topics), or write `data/topics/<ID>.yaml` (the file name must match `id`) and run `isbe topics` to check it. Start from one of the examples:

| Example | Contents |
| --- | --- |
| [nowcasting.yaml](examples/topics/nowcasting.yaml) | A Chinese weekly paper report: research scope, exclusion rules, repository tracking and full-text settings |
| [rust-news.yaml](examples/topics/rust-news.yaml) | An English RSS topic: collection and triage work today; article reports are not available yet |

Field reference: [topic configuration](docs/configuration.md#主题配置) (Chinese).

### Beyond papers

| Use | Entry point |
| --- | --- |
| Manage RSS / web section sources and collect into the material inbox | The console's Sources page and `isbe sources`; see [sources and material inbox](docs/configuration.md#新闻来源与材料收件箱) |
| Search the web for media coverage per topic through SearXNG and check source dates | Web search; see the [source interface](docs/source-interface.md#主题联网检索) and [local SearXNG](deploy/searxng/README.md) |
| Turn collected material into an industry briefing organized by issue | `isbe media-weekly`, `isbe media-briefing`; see [media and industry briefings](docs/configuration.md#媒体材料与行业周报) |
| Chinese research reports from given source excerpts, with frozen sources and quotes | `isbe research <manifest.json>`; see [fixed-material research](docs/configuration.md#固定材料专题研究) |
| Share one server between several people | `isbe serve --shared`; see [keys and access](docs/configuration.md#密钥与访问权限) |

These pages are in Chinese.

### Where things end up

| Path | Contents |
| --- | --- |
| `data/reports/<topic>/` | Markdown reports per period |
| `data/figures/` | Figures for papers and imported reports |
| `data/research/<topic>/<version>/` | Frozen sources, material text and quote lists of research reports |
| `data/memory/topics/` | Topic memory that you maintain |
| `data/memory/.pending/` | Drafts proposed by the model, waiting for review |
| `data/settings.json` | Service settings saved from the settings page; may contain keys |
| `data/isbe.db` | The database: items, triage results, the task queue, accounts and LLM call records |

To back up, stop the service and copy the whole data directory; see [data and backup](docs/configuration.md#数据与备份) (Chinese).

---

## How it is designed

### One one-way pipeline

```
collect  →  triage  →  enrich                   →  digest
collect     triage     full text and fact cards    report (with an LLM review)
```

Each stage reads only what earlier stages wrote and never calls them; each stage writes only its own tables. Every model call is recorded in `llm_calls` and can be inspected on the web.

### A fixed report structure

A paper report has six sections: **TL;DR → papers → repositories → terms → analysis → distillation**. "Analysis" compares this period's facts with your memory; memory changes proposed under "distillation" only go to `.pending/` drafts.

### Only people change memory

The model cannot write the live memory: drafts stay in `memory/.pending/` until you review and merge them by hand. External text (RSS, web pages, model output) is treated as untrusted: it is sanitized before rendering, and any path taken from model output is checked before it touches the disk.

### One service, one data directory

There is no separate database, scheduler or object store: SQLite (WAL mode) plus local files. essaies, metrail-web, SearXNG, the model service and the mail provider are all optional external dependencies.

See the [design document](docs/DESIGN.md) and the [architecture notes](docs/architecture.md).

---

## Known gaps

- No built-in scheduler or scheduled email: automatic runs need cron or a similar tool
- No memory review command or page: `.pending/` drafts are reviewed and merged by hand
- RSS article topics can be collected and triaged but not yet reported; the web generation queue handles paper topics only
- Zhihu collection, monthly reports and week-over-week comparison are not implemented
- Source management is not yet wired to scheduled collection; inputs for fixed-material research and industry briefings cannot be edited on the web
- Not every arXiv paper has an HTML full text; without one, the report uses the abstract only

---

## Documentation

| Document | Contents |
| --- | --- |
| [docs/configuration.md](docs/configuration.md) | Full reference: service settings, mail, full text, keys and shared deployment, topics, commands, Docker, backup, development (Chinese) |
| [docs/source-interface.md](docs/source-interface.md) | RSS / web sources, web search plans, date checks and the material inbox (Chinese) |
| [docs/DESIGN.md](docs/DESIGN.md) | Stage design, data model and roadmap |
| [docs/architecture.md](docs/architecture.md) | Code layout, compatibility contract, conventions and tests |
| [docs/upgrading-from-rust.md](docs/upgrading-from-rust.md) | Moving a data directory from the earlier Rust build |
| [deploy/searxng/README.md](deploy/searxng/README.md) | Running SearXNG locally (Chinese) |
| [docs/README.md](docs/README.md) | Documentation index |

Issues and pull requests are welcome; see [development and contributing](docs/configuration.md#开发与贡献) (Chinese) for how to run the tests. Do not commit keys, personal data directories or service addresses with credentials.

## License

MIT — see [`LICENSE`](LICENSE).
