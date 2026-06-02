# ISBE — Your Personal Research Assistant

> Information System with Backbone of Evolution

[![CI](https://github.com/guliqianxun/ISBE/actions/workflows/ci.yml/badge.svg)](https://github.com/guliqianxun/ISBE/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

[中文](README.md) | **English**

Every Monday morning, you open your inbox to a digest:

- **What happened in every field you care about over the past 7 days** — not a pile of headlines, but a plain-language summary the LLM writes after reading the full sources.
- **Per-item review**: which pieces are worth reading, which are noise, and why.
- **Cross-check against your "thesis library"**: you previously believed "trend X will happen" — does this week's evidence strengthen or weaken it?
- **Distillation proposals**: based on this week's new facts, the system proposes adding/editing a thesis in your library — you accept or reject.

Next week, another one. **Month after month, your thesis library grows into your research worldview.**

---

## The problem it solves

You're probably this kind of person:

- You follow several fields (a research sub-area, public markets, industry news, a vertical hobby...), but **have no time to patrol them daily**.
- You've tried Feedly / RSS readers / X Lists — enough sources, but **too much to read**, ending in "999+ unread".
- You've tried asking ChatGPT "what's new in field X" — the answers are vague, sources untrustworthy, **no persistent memory**.
- What you actually want: **let information flow to you instead of chasing it**, and have a system that **looks like you** — remembers what you believe and what you don't.

ISBE is built for that. It is not a chatbot. It is **a periodic pipeline running on your machine**: pull data → use an LLM to summarize what you need to see this week → distill "what you believe" into accumulable memory → next digest does comparison analysis based on that memory.

---

## What it can do today

6 active topics, each starting from a single YAML file, auto-running and auto-producing digests:

| Topic                 | Sources                                | Cadence | Use case                          |
|-----------------------|----------------------------------------|---------|-----------------------------------|
| `nowcasting`          | arxiv + github                         | weekly  | precipitation nowcasting research |
| `video-generation`    | arxiv                                  | weekly  | video-generation model research   |
| `image-restoration`   | arxiv                                  | weekly  | image-restoration research        |
| `nvda`                | stock price + news + SEC filings       | daily   | NVDA financial daily              |
| `motorcycle`          | overseas moto media RSS + opt. crawler | weekly  | 250cc purchase decision           |
| `china-tech`          | 36Kr + Zhihu columns                   | weekly  | China tech / VC weekly            |

Adding a new topic = write one `topic.yaml` + pick a built-in collector preset. A zero-code case can be up and running in an evening.

Built-in source presets (out of the box, no scraper to write):

- **arxiv**: subscribe to papers by keyword + auto-download PDFs
- **GitHub**: track releases / stars / commits of given repos
- **RSS**: any standard RSS feed
- **RSSHub** (self-hosted): turn non-RSS sites (Weibo / Bilibili / Xueqiu...) into RSS
- **morerssplz** (self-hosted): Zhihu columns, no cookies needed
- **Crawl4AI**: headless Chrome + LLM extraction for anti-scraping sites
- **Stock price / SEC**: financial data ingestion

---

## Getting started

### First install (one coffee)

```bash
git clone https://github.com/guliqianxun/ISBE && cd ISBE
cp .env.example .env
# fill two keys:
#   DEEPSEEK_API_KEY=...      # or ANTHROPIC_API_KEY, sets ISBE_LLM_PROVIDER
#   GITHUB_TOKEN=...          # optional, gives 5000 req/hr headroom

uv sync --all-extras
docker compose up -d                 # start the infra containers
uv run alembic upgrade head          # create tables
uv run pytest                        # should be all green
```

### Try a digest immediately

```bash
uv run radar topics run nowcasting --collect    # pull arxiv + github -> DB
uv run radar topics run nowcasting --digest     # LLM reads -> writes digest
```

Open `artifacts/nowcasting/<this-week>/latest.md` — that's your first digest.

### Review distillation proposals

```bash
uv run radar review memory                                            # list pending drafts
uv run radar review memory --accept topics/nowcasting.theses.md       # accept one
uv run radar memory reindex                                           # rebuild index
```

Accepted theses go into `memory/me/topics/*.md` and are automatically included in the next digest's prompt.

### Let it run itself

```bash
uv run radar scheduler serve     # long-running; cron triggers all topics
# or use the docker worker (auto-restarts on boot):
docker compose --profile worker up -d --build
```

### Where the output lives

| Path                                       | Contents                                   |
|--------------------------------------------|--------------------------------------------|
| `artifacts/<topic>/<period>/latest.md`     | this period's digest (mirrored in MinIO + Postgres) |
| `papers/<arxiv_id>.pdf`                    | downloaded paper PDFs                       |
| `memory/me/topics/*.md`                    | theses / preferences you've accepted        |
| `memory/me/.pending/`                      | memory drafts awaiting review               |
| `memory/me/MEMORY.md`                      | auto-generated index                        |

---

## How it's designed

### Three data layers, never mixed

```
facts        atomic facts pulled from the outside world; re-fetchable, deletable
memory       your state: preferences, theses, profile — one file per item, git-friendly
artifacts    LLM-written output (digests) — for archival, never fed back into prompts
```

The LLM prompt only ever reads `facts × memory` and writes `artifacts + .pending memory drafts`. **This is the backbone of the whole system.**

### Isomorphic topic interface

```
collector(s)  -> write sources into the facts table
digester      -> read facts (by time window) x memory -> 5-section LLM template -> write artifact + distillation .pending
```

The shared 5-section digest contract (all topics): TL;DR, per-item review, cross-item/period comparison, analysis vs. memory, and distillation proposals. Adding a new domain must not change this interface — that is what separates ISBE from "a pile of one-off scripts".

### Self-hosted, one stack

Postgres (facts DB) · MinIO (blobs) · Prefect (cron orchestration) · Phoenix (LLM tracing) · RSSHub · morerssplz · Qdrant (reserved for semantic retrieval) · Uptime Kuma (health).

### Switchable LLM provider

`ISBE_LLM_PROVIDER=deepseek|anthropic` — switching is one environment variable. Phoenix traces both.

---

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`AGENTS.md`](AGENTS.md) (red lines & locked decisions), and [`SECURITY.md`](SECURITY.md). In short: TDD, conventional commits, `uv run ruff check` + `uv run pytest -m "not integration"` before pushing.

## Known gaps

- Qdrant not yet enabled (MVP filters facts by time window, no semantic retrieval).
- `radar review accept` does not yet run frontmatter lint.
- No one-click deploy image yet.

## License

MIT — see [`LICENSE`](LICENSE).
