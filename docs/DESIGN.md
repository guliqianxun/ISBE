# ISBE — design

## Scope

The design below describes the paper workflow and its planned stages. ISBE
also has a fixed-material extraction workflow (`reports/research.py`) and
media compilation / editorial briefing workflows (`reports/media.py`,
`reports/briefing.py`). They share the application and report reader, but keep
separate input contracts and material records. The web generation queue still
serves paper topics; a unified workflow selector has not been implemented.

In the material workflows, LLMs extract and organize source content.
Deterministic checks establish such things as quote locations; people decide
relevance, semantic support and acceptance. The paper triage and reviewer
judgments are model judgments and have not been migrated to that policy.

ISBE (Information System with Backbone of Evolution) is a self-hosted research
radar: topics declared in YAML pull papers and articles, an LLM triages and
digests them into a fixed five-section report, and the digest proposes
memory updates that only a human can accept. Next period's report is written
against that memory.

It ships as one `isbe` command (CLI + web console) with a SQLite file per
workspace under one data directory. The code layout is in
[architecture.md](architecture.md); an earlier Rust build is covered in
[upgrading-from-rust.md](upgrading-from-rust.md).
This document also includes planned stages such as scheduling and memory review;
see [features.md](features.md) for the current feature status.

## 1. Two systems, one direction

```
essaies (archive: what exists)            ISBE (judgment: what matters, what I think)
  arXiv OAI / CVF / HF / OpenAlex  ─HTTP─▶  topics → triage → full text → report → memory → email
  PDFs on disk, FTS5 index                  RSS / GitHub / Zhihu / exec sources
```

- **essaies owns the paper catalog when configured.** ISBE queries its
  archive; the metrail extractor fetches PDFs from it when an archive path
  exists. The LLM extractor obtains HTML directly from arXiv.
- **Without essaies** (public users), the `arxiv` source queries the public
  arXiv API instead. The default full-text path is arXiv HTML with selected
  captioned source figures, followed by LLM fact extraction; unavailable
  HTML leaves an abstract-only report.
  A configured metrail deployment can continue parsing PDFs instead.
- **The paper workflow records model judgments:** triage verdicts,
  report reviews and memory proposals. This is not the extraction contract of
  the material workflows.

## 2. Rules (non-negotiable)

1. One-way data flow: collect → triage → enrich → digest → notify. A stage
   reads what earlier stages wrote; it never calls them.
2. Memory changes only through `.pending/` → human review → merge with a
   revision bump. No code path writes the live memory tree from LLM output.
3. Human edits win. A draft may add beside a human-edited line, never
   replace it.
4. Scheduled work is deterministic jobs with fixed prompt templates, not an
   agent loop.
5. Every LLM call goes through `core/llm.py` and is logged to `llm_calls`.
6. The paper workflow uses a five-section report contract. Fixed-material
   research and editorial industry briefings use their own report structures.
7. External text (RSS, crawl, LLM output) is untrusted: sanitized (`nh3`,
   the Python binding of ammonia) before HTML, and any path from LLM output
   is checked before touching disk (`memory.py` refuses an unsafe draft path).
8. **Each stage owns its output tables.** Queue recovery may finish interrupted
   run records. Research content a human edits is a file; operational records
   such as tasks and accounts are rows.

## 3. Deployment

```
container:       isbe  (image built from ./Dockerfile; entry point `isbe`)
volume /data:    isbe.db  settings.json  topics/  memory/  reports/  research/  figures/  workspaces/
external, opt.:  ESSAIES_URL   METRAIL_API_URL   SEARXNG_URL   LLM endpoint   mail provider
```

There is no separate database, scheduler or object store: SQLite in WAL mode,
the `llm_calls` table for model-call records, and local files.

## 4. Data model

### Files (human-facing)

| Path | Edited by | Notes |
|---|---|---|
| `topics/<id>.yaml` or `.yml` | human or Web topic manager | topic config, schema `deny_unknown_fields`; one file per ID |
| `settings.json` | Web settings | service configuration, including local plaintext secrets |
| `subscriptions.yaml` (planned) | human | extra recipients per topic for scheduled notifications |
| `memory/**.md` | human, via review | frontmatter + markdown; `.pending/`, `.audit/rejected/`; per workspace |
| `reports/<topic>/<period>.md` | `digest` | the report itself |
| `figures/**` | `enrich` | selected source figures, including panels of a grouped figure, shown in reports |

The first administrator retains the root workspace. Other accounts use
`workspaces/<account-id>/`, each with its own database, topics, reports,
figures and memory. Service settings remain at the data root.

### Core workspace tables (`isbe.db`)

```sql
-- Everything any source found. Writer: collect/
CREATE TABLE items (
  id           TEXT PRIMARY KEY,  -- arxiv:2609.01234 | rss:<url> | gh:owner/repo | zhihu:<id>
  kind         TEXT NOT NULL,     -- paper | article | repo
  source       TEXT NOT NULL,     -- essaies | arxiv | rss:<feed> | github | zhihu | exec:<name>
  title        TEXT NOT NULL,
  summary      TEXT,
  url          TEXT NOT NULL,
  authors      TEXT,              -- JSON array
  lang         TEXT NOT NULL,
  published_at TEXT NOT NULL,
  extra        TEXT NOT NULL DEFAULT '{}'  -- JSON: categories, hf_upvotes, citations, stars
);

-- Which topic pulled which item. Writer: collect/
CREATE TABLE topic_items (
  topic_id   TEXT NOT NULL,
  item_id    TEXT NOT NULL REFERENCES items(id),
  first_seen TEXT NOT NULL,
  PRIMARY KEY (topic_id, item_id)
);

-- Relevance verdicts; cached because the judge is an LLM. Writer: pipeline/triage
-- A changed retrieval block changes contract_sha, which forces a re-judge.
CREATE TABLE triage (
  topic_id     TEXT NOT NULL,
  item_id      TEXT NOT NULL,
  contract_sha TEXT NOT NULL,
  verdict      TEXT NOT NULL,     -- kept | judge_dropped | gate_dropped
  tier         TEXT,              -- core | secondary
  reason       TEXT NOT NULL,     -- gate hit or judge explanation
  judged_at    TEXT NOT NULL,
  PRIMARY KEY (topic_id, item_id)
);

-- Full text for kept papers only. Writer: pipeline/enrich
CREATE TABLE fulltext (
  item_id      TEXT PRIMARY KEY,
  via          TEXT NOT NULL,     -- metrail | arxiv-html
  body         TEXT NOT NULL,
  extracted_at TEXT NOT NULL,
  figure       TEXT               -- JSON {files: [...], caption}; legacy metrail {file, caption}
                                 -- '{}' no supported figure; NULL pending or retryable failure
);

-- Facts from a paper's full text, each with a quote checked against it. Writer: pipeline/enrich
-- The key hashes the extraction prompt and the full text; a change to either rebuilds.
CREATE TABLE cards (
  item_id    TEXT NOT NULL,
  lang       TEXT NOT NULL,       -- language of the values: en | zh
  key        TEXT NOT NULL,
  card       TEXT NOT NULL,       -- JSON: {"fields": {name: {value, quote}}, "rejected": [name]}
  created_at TEXT NOT NULL,
  PRIMARY KEY (item_id, lang)
);

-- Report index; the body is the .md file. Writer: pipeline/digest, reports/
CREATE TABLE reports (
  id          TEXT PRIMARY KEY,
  topic_id    TEXT NOT NULL,
  period      TEXT NOT NULL,      -- 2026-W41 | 2026-10 | 2026-10-12
  kind        TEXT NOT NULL,      -- weekly | monthly | daily
  path        TEXT NOT NULL,
  fingerprint TEXT NOT NULL,      -- JSON: model, memory revisions, contract sha, item ids
  created_at  TEXT NOT NULL,
  UNIQUE (topic_id, period, kind)
);

-- Ledger. Writer: the job wrapper; generation also recovers interrupted runs.
CREATE TABLE runs (
  id          TEXT PRIMARY KEY,
  job         TEXT NOT NULL,      -- collect | triage | enrich | digest | notify
  topic_id    TEXT,
  status      TEXT NOT NULL,      -- running | ok | failed
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  stats       TEXT NOT NULL DEFAULT '{}',
  error       TEXT
);

-- Every LLM call, replayable. Writer: core/llm.py
CREATE TABLE llm_calls (
  id         TEXT PRIMARY KEY,
  run_id     TEXT REFERENCES runs(id),
  purpose    TEXT NOT NULL,       -- judge | digest | cards | reviewer | extract
  model      TEXT NOT NULL,
  prompt_sha TEXT NOT NULL,
  prompt     TEXT NOT NULL,
  response   TEXT NOT NULL,
  tokens_in  INTEGER, tokens_out INTEGER, ms INTEGER,
  created_at TEXT NOT NULL
);
```

The root database also stores `tasks` and `task_requests` (writer:
`generation/`) for the durable queue and request deduplication, and
`auth_users`, `auth_sessions` and `auth_login_attempts` (writer: `auth.py`)
for accounts, sessions and login throttling. Web stage runs carry a `task_id`
link to their queue entry. The search and source-inbox tables (`search_*`,
`source_*`) and `article_text` are described in
[source-interface.md](source-interface.md). Question-run search plans
(`question_plans`) are described in [question/planning.md](question/planning.md);
question runs, their events, queries, hits, paper sets and set operations
(`question_runs`, `question_events`, `question_queries`, `question_hits`,
`question_papers`, `question_paper_uses`, `paper_sets`, `set_ops`) in [question/search.md](question/search.md); screening verdicts and
overrides (`screen_verdicts`, `screen_overrides`) in [question/screening.md](question/screening.md); the
root database's queue of question runs (`question_tasks`, writer: `question/queue.py`) in
[question/api.md](question/api.md). See `migrations/` for the complete
current schema.

## 5. Jobs and modules

Each implemented stage is a job you can run alone. `serve` hosts the console,
configuration editors and report queue; it does not execute cron schedules.

| Job | Reads | Writes |
|---|---|---|
| `isbe collect <topic>` | essaies / arxiv / rss / github (zhihu and exec: planned) | `items`, `topic_items` |
| `isbe triage <topic>` | `topic_items` in window, retrieval contract | `triage` |
| `isbe enrich <topic>` | kept papers → arXiv HTML → LLM, or PDF → metrail → LLM | `fulltext`, `cards`, optional `figures/` |
| `isbe digest <topic>` | `triage`, `fulltext`, memory | report file, `reports`, `.pending/` |
| `isbe notify <topic>` (planned) | `reports`, `subscriptions.yaml` | email |
| `isbe review` (planned) | `.pending/` | memory (human) |
| `isbe import-reports <dir>` | `<dir>/<topic>/<period>/latest.md` | report files, `reports`, `figures/` |
| `isbe serve` | database and local configuration | Web console, report queue, manual email, service settings and topic files |
| `isbe settings import <file>` / `isbe settings export [--secrets]` | a settings file ([settings.md](settings.md)) / the settings in effect | `settings.json` / the file on stdout, keys only with `--secrets` |
| `isbe account-add <username> --password-file <file> [--admin]` | protected password file | account |
| `isbe sources …`, `isbe media-weekly`, `isbe media-briefing`, `isbe research` | source configs, saved search candidates, editor input, fixed-material manifests | see [source-interface.md](source-interface.md) and [research.md](research.md) |

Topic schedules (`collect`, `digest`, `monthly`) are validated configuration,
not active timers. The current `digest` command runs triage → enrich → digest;
scheduled notification and built-in scheduling remain planned. Triage and
enrichment reuse their caches where valid; digest generation and review still
make LLM calls on a rerun.

The package layout and the dependency rule between its parts are in
[architecture.md](architecture.md#layout). `frontend/` and `web/` (the UI), `prompts/{zh,en}/`,
`migrations/` and `examples/` live beside the package. `cli.py` and
`generation/` chain the stages.

All outbound HTTP is blocking `httpx` through `core/http.py`: jobs are plain
functions over one SQLite connection, and `serve` runs queued report jobs on
one background worker.

**Web generation.** A submitted paper topic runs collect → triage → enrich →
digest in a persistent FIFO queue, with stage progress and task history.
Each account may have at most five queued or running tasks; queued tasks can
be cancelled. Request IDs are idempotent, and an active task for the same
account and topic is reused. Topic configuration is snapshotted at submission;
changed non-secret service settings require resubmission, while credentials
are read at execution and never stored in task records. Restart resumes queued
tasks and marks running tasks interrupted without replaying model calls.
CLI mutations and the worker share an execution lock.

**Manual email.** The report reader sends the selected topic and period by
SMTP or the ZeptoMail HTTPS API without regenerating it. An administrator
configures the transport, sender and credentials in Settings or the
environment (nonempty environment variables win); Settings also manages
optional default recipients and provides a test email. A report send may use
temporary recipients without saving them. Sanitized HTML and plain text are
sent with referenced local figures embedded by CID. Delivery uses one bounded
request with no automatic retry; provider acceptance is distinct from inbox
delivery. Each account is rate-limited (members 10 per hour and 30 per day,
administrators 60 and 300; `mail/limit.py`). Subscriptions and scheduled
sending remain planned.

**Sources (`isbe collect`).** Papers: a paper is kept if it is listed in any of
`arxiv.categories` and its title or abstract contains any of
`arxiv.include_keywords`, updated within `window_days`. With `ESSAIES_URL` that
is one `/api/search` call (keywords as FTS5 phrases, `since=`, `categories=`,
whole window); without it, the arXiv API (`ti:"…" OR abs:"…"`, newest
`max_results`, 3 s pause per call). RSS filters: `lookback_days`,
`exclude_url_patterns` (substring of the link), `exclude_keywords` (title or
summary, case-insensitive); an undated entry counts as new. A failing source
does not stop the others: what the rest found is stored and the run is marked
failed with every source's error. `github`: one REST call per repo for stars
and last push (`GITHUB_TOKEN` optional); a renamed repo is followed. `zhihu`
blocks are accepted and reported as `not_yet_supported` in the run stats.

Every stage uses the same window: from midnight UTC `window_days` ago, in whole
days because papers carry dates, not times.

**Triage (`isbe triage`).** Each item in the window not yet judged under the
current contract goes through three steps:

1. Title gates (an abstract's motivation sentence would trip them): an
   `out_of_scope_keywords` hit, or no `require_any` term → `gate_dropped`.
2. The judge: fast tier, 20 items per call, title + first 280 characters of the
   abstract, one `n|IN or OUT|facet|reason` line each → `kept` or
   `judge_dropped`.
3. Tier: a kept item that mentions a `secondary_terms` entry is `secondary`,
   the rest `core`.

The contract hash covers the retrieval block and the judge prompt, so editing
either re-judges the window. When a judge call fails, its batch is kept
(recall-safe) without the hash, so the next run judges it again, and the run
is marked failed. A topic without a `retrieval` block keeps everything.

**Enrich (`isbe enrich`).** Full text for the papers the digest will review
(its order and limit), selected by `ISBE_FULLTEXT_EXTRACTOR` in the settings
page or environment:

- `llm`: fetch arXiv HTML, normalize the article into text with headings,
  table rows and links, then use the existing fast-tier LLM evidence-card
  step below. HTML normalization is deterministic; the LLM extracts facts
  from the resulting text. Save suitable captioned source figures locally
  for reports, keeping the panels of a grouped figure. This path needs no
  separate service or API key and adds no vision-model calls. It performs
  no PDF OCR or image-content recognition, and does not reconstruct merged
  table cells. Not all papers have usable HTML, and conversions can be incomplete; see the
  [arXiv HTML documentation](https://info.arxiv.org/about/accessible_HTML.html).
  Unavailable HTML is recorded as an `abstract_fallback` reason without
  failing the enrich run; the report uses the abstract. There is no
  automatic switch to metrail.
- `metrail`: parse PDFs through the optional metrail-web service, then use
  the same LLM evidence-card step.
- `off`: skip new full-text fetches and new cards. Previously cached full
  text and cards remain available to the digest.

When the selector is absent, an existing global `METRAIL_API_URL` or topic
`metrail.base_url` keeps the metrail path unless `metrail.enabled` is false;
otherwise the default is `llm`. `metrail.enabled: false` only disables
metrail, not the LLM path; use `off` to disable new full text and cards.
Successful full text is cached in `fulltext`, tagged `arxiv-html` or
`metrail`, rather than fetched again on every run. Cached HTML papers with
no figure decision yet, including a temporary download failure, can fetch
the HTML again on a later enrich run to add figures, without replacing their
stored body or rebuilding unchanged cards. A completed decision that no
supported figure exists is cached and is not retried on every run.

HTML figures are selected from captioned figures in the source article:
prefer a method, architecture, framework, or workflow diagram, then an
overview, then the first captioned figure. Retain the selected figure's
multiple panels and save them under `figures/` for report display, with
metadata `{files: ["figures/a.png", ...], caption: "source caption"}`. Existing
metrail `{file, caption}` metadata remains supported. Downloads accept PNG,
JPEG, and WebP: at most 2 MiB per panel, 5 MiB for the entire selected group,
and 12 panels. Do not truncate a group to fit these limits. If the group
exceeds a limit or any panel fails to download, record the reason and omit
the incomplete group while preserving the body and allowing the report
to continue. Deterministically unsupported groups, such as those exceeding
12 panels or containing unsafe URLs, produce figure warnings and do not
trigger repeated retries. Temporary network or download failures leave the
figure decision pending (`NULL`) so the next enrich run can retry. No suitable
source figure also leaves a text report. Images are shown
as source evidence, not sent to a vision model or interpreted as additional
textual facts.

In metrail mode, the PDF comes from essaies (`/api/file`) when the item has
an archive path, else from arxiv.org. All are
uploaded to metrail-web before any is polled, so its workers parse in
parallel. `metrail.poll_timeout_s` is a stall timeout: when no document
finishes for that long, the rest wait for the next run. metrail answers a
re-upload of the same file with the document it already has, so a re-run
costs downloads, not parses; a document that failed there stays failed until
it is reprocessed in metrail. The body is markdown: `## Page N`, the page text
with metrail's chunk overlap removed, then each table as a `### <caption>`
block. The paper's method figure is saved under `figures/` (metrail's
downscaled copy, at most 500 KB): the first figure whose caption opens by
naming a framework, architecture, pipeline or workflow, else an overview,
else the first captioned one. A paper with metrail text but no figure
decision yet is uploaded again, which metrail answers with the parsed
document. Selecting metrail without its URL cannot fetch new full text.

In either enabled mode, each of those papers with full text gets an evidence card (one fast-tier
call over the first 60,000 characters, prompts `card.md`): source, method,
data, code, compute, results and limits, each with a quote copied from the
paper. The extraction prompts treat paper content as untrusted evidence,
not instructions. A fact is kept only when its quote (at least 15 characters) and every
number and URL in its value appear in the full text, compared without
whitespace or case; the others are listed as rejected. Cards are per item and
report language, and rebuilt only when the prompt or the full text changes.
Missing HTML is recorded as an abstract fallback. Database writes and card
generation errors still fail the enrich run; the digest can continue with
the available evidence or the abstract.

**Digest (`isbe digest`).** Runs triage and enrich, then one smart-tier call with the kept
papers (core first, then newest; at most `digest.max_papers`, default 25, the
rest are listed by title), the tracked repos and the topic's memory notes
(its own `topic` notes plus every `feedback` and `user` note). A reviewed paper
with full text also brings its tables, whole lines, at most
`digest.fulltext_per_paper_chars` (3000) each and `fulltext_total_chars`
(24000) in all, and its card's facts; the prompt accepts both as evidence.
The reviewer (one smart-tier call, `review.md`) then lists the reply's claims
that drift from or lack support in the same input; it sees the reply without
its glossary (general knowledge, which no evidence states) and memory drafts.
Any flag sends the reply back once with those notes, and the rewrite is
reviewed again; it is kept unless it draws more flags than the first draft
(on a local 27B model it often does). The flags of the text the report
carries, or a reviewer failure, are shown in its audit block; a reply that is
not a JSON array is asked for again once, then counts as a failure, which
also fails the run.
The report is rendered from database facts plus the reply's judgments, parsed
leniently; a card's verified source, method, data, code and results replace
the reply's, its compute and limits add rows, and its quotes sit under each
paper. The
TL;DR's paper count, reading list (papers the reply rates 值得细读 / read in
full) and active repos are built by code, so they cannot contradict the
reviews. Drafts go to `memory/.pending/` after a path check; re-running a
digest does not duplicate them. The period of a weekly report is the ISO week
of yesterday in the topic's timezone (a Monday run names last week); the file
is `reports/<topic>/<period>.md` and a re-run overwrites it. A reply that skips
papers fails the run; the report is still written, with those papers
unreviewed.

**LLM.** `ISBE_LLM_PROVIDER` = `anthropic` (default; `ANTHROPIC_API_KEY`,
fast `claude-haiku-5-5`, smart `claude-sonnet-5-5`) or `deepseek`
(`DEEPSEEK_API_KEY`, `deepseek-chat` for both) or `openai` (`OPENAI_API_KEY`,
`gpt-5-mini` / `gpt-5`). `ISBE_LLM_MODEL_FAST`,
`ISBE_LLM_MODEL_SMART` and `ISBE_LLM_BASE_URL` override; `openai` with a base URL
covers any OpenAI-compatible server (vLLM, Ollama). `ISBE_LLM_MAX_TOKENS` caps
every call's output, for servers whose context cannot hold prompt + the 24k
report budget and whose rejection does not say how large the context is; a
rejection that names the prompt and context sizes is retried once with the
output the context leaves; `ISBE_LLM_EXTRA_BODY` merges JSON fields into each request (e.g.
`{"chat_template_kwargs": {"enable_thinking": false}}` for Qwen on vLLM);
`ISBE_LLM_TIMEOUT_S` (default 900) bounds one call. Up to two retries on
408/429/5xx/529 and network errors, not on a timeout; a reply cut off at `max_tokens` is logged
and fails the call.

Settings saves service configuration to `settings.json`; nonempty environment
variables override saved values. Saving validates configuration without testing
the connection and allows incomplete model configuration. Saved secrets are
not returned to the browser. Explicit `ISBE_LLM_NO_AUTH=true` is supported only
for OpenAI-compatible endpoints on literal loopback or private-network addresses
(or `localhost`); it suppresses the Authorization header, including a saved key.

`exec` sources (planned, not implemented): `exec: ./collectors/x.py` in
topic.yaml, where the command prints one JSON item per line on stdout, so that
anything ISBE lacks (a site scraper, a market-data feed) can live outside it.

## 6. topic.yaml

Schema: `isbe/topics/model.py`. Examples: `examples/topics/` (a papers topic and an
articles topic). Unknown keys, bad crons, unknown timezones and an `id` that
differs from the file name are all load errors.

The Web topic manager at `/topics` creates and edits files under the current
workspace's `topics/`. An ID cannot be changed after creation. Updates
overlay form fields onto the existing configuration, preserving fields the
form does not expose. The resulting YAML is reserialized, so comments and
formatting are not preserved. RSS environment expressions are read and saved
without expansion; runtime loading resolves them. Both `.yaml` and `.yml`
are supported, and management operations reject an ID present in both.
Ordinary shared users are limited to bounded paper topics and cannot override
source-service addresses or use environment-variable expansion.

`active: false` excludes a topic only from CLI jobs that omit topic names;
an explicitly named inactive topic can still run. Saved changes apply to
subsequent Web submissions and CLI invocations using the same workspace.
Neither saving nor enabling a topic starts a job or a scheduler.

Deletion removes only the configuration file, retaining the topics directory,
database rows, reports, figures and memory. The overview no longer lists the
topic, but its historical reports remain on the report shelf. Recreating the
same ID reconnects that history; a new ID is needed for an independent topic.

```yaml
id: nowcasting                # must equal the file name
label: 临近降水预报科研订阅
lang: zh                      # report language → prompts/zh
cadence: weekly               # weekly | daily
schedule_timezone: Asia/Shanghai
window_days: 7
schedules: {collect: "0 6 * * *", digest: "0 8 * * 1", monthly: "0 9 1 * *"}
arxiv: {categories: [physics.ao-ph, cs.LG, eess.IV], include_keywords: [...]}  # essaies or arXiv API
github: {repos: [owner/name]}
rss: {feeds: [{name, url}], exclude_keywords: [...], lookback_days: 14}         # url may use ${VAR:-default}
zhihu: {queries: [...], count: 10, lookback_days: 3}
metrail: {enabled: true, poll_timeout_s: 600, table_backend: docling, figure_backend: docling}  # METRAIL_API_URL
retrieval: {intent, in_scope, out_of_scope, out_of_scope_keywords, require_any, facets, secondary_terms, ...}
```

The digest flavor is derived, not configured: a topic with `arxiv` gets the
papers digest, otherwise the articles digest.

## 7. Web UI

| Page | Purpose | Writes |
|---|---|---|
| Home (`/`) | the universe: every question run drawn as a galaxy (its papers are stars, citations the lines between them), a preview of the selected one, what needs the person, the newest topic reports, and a first-run guide while there are no galaxies | asks a new question |
| Galaxies (`/galaxies`) | every galaxy as a list row, newest first | — |
| Question run (`/question/runs/<id>`) | one run as a search flow: plan, queries, merge, AI screening, star map and papers | pause, resume, cancel, edit the plan |
| System (`/system`) | the working pages that are not in the sidebar | — |
| Help (`/help`) | what ISBE does and how to start, the report guide (check the model, confirm a topic, generate and follow one report), and for administrators the setup guide (docs/configuration.md and docs/deployment.md at `/help/service`) | submits one report task |
| Overview | signal map per topic, week funnel, topic cards, today's runs | — |
| Reports | a shelf of every issue as a cover; then the report as a paper sheet with older/newer, its sections, Save as PDF and .md | manual email of the selected report |
| Tasks | queue position, stage progress and history; each own task opens in the report guide on Help | cancel queued tasks |
| Triage explorer | every item with its gate trace and judge reason | feedback draft planned |
| Memory review (planned) | pending drafts as diffs; accept / edit / reject | memory |
| Runs & LLM | 8-day swimlane, run ledger, tokens by purpose, replayable calls | run again planned |
| Settings | model and service configuration, default recipients and email test | `settings.json`; test email |
| Topics | create, edit, enable/disable and delete topic configurations | `topics/<id>.yaml` or `.yml`; history retained |
| Accounts (shared mode) | administrator account management | create users, reset passwords, activate/deactivate accounts |

- **Navigation:** one sidebar on every page of both consoles: Home,
  Galaxies, Reports, Topics, Settings (administrators) and System, which
  lists Overview, Tasks, Triage, Runs & LLM, Sources and Accounts. Unknown
  paths get a not-found page with a 404 status.
- **Look:** dark "observatory" shell by default; reports keep the email's
  academic-ink paper look (Source Serif 4). IBM Plex Sans/Mono for UI and
  data. 中文 / EN toggle.
- **Themes (Customize panel, like Chrome's):** every color is a CSS custom
  property computed in OKLCH from one seed hue `--h` and a chroma switch
  `--c`, so contrast holds for any hue. Mode: light / dark / device
  (`prefers-color-scheme`). Presets: Signal 175 (default), Glacier 235,
  Iris 290, Coral 22, Ember 62, Moss 145, Graphite (chroma 0), plus a custom
  hue slider. Reading surface for reports: paper / sepia / night. Warning and
  danger colors stay fixed across themes. Stored in `localStorage` (`isbe.ui`,
  read by both consoles); the tokens live at the top of `web/app.css`.
- **Stack:** the console is moving to React (React 19, TypeScript, Vite) in
  `frontend/`, decided for M3; pages move over route by route and `web/`
  (plain HTML, CSS and JavaScript) serves the rest until it is retired. The
  React build is made in the Docker image's Node stage and packaged with the
  Python code as `isbe/_assets/app`; the runtime image has no Node. See
  [architecture.md](architecture.md#two-consoles-during-the-move-to-react).
  `isbe serve` runs uvicorn with bounded connections
  and request bodies, handlers in a thread pool, and a separate report-queue
  worker. It answers `/api/home` (Home's topics, newest reports and service readiness),
  `/api/overview`, `/api/report`,
  `/api/report.md`, `/api/triage`, `/api/runs`, `/api/call`, task, account,
  settings and mail APIs, plus `/figures/<file>`, the React build's files
  under `/app/assets/`, and the console's pages; any other path is a 404. Reports are rendered to HTML on the server
  (`markdown-it-py`) and sanitized with `nh3`; the UI escapes every other
  value and links only http(s) URLs. The font stacks name the design's faces
  first and fall back to system fonts; no web fonts are loaded.
- **Built so far:** Home with the galaxy universe, Galaxies, question runs
  with their star map, Overview, Reports (shelf and reader), Triage explorer,
  Runs & LLM, service Settings, Topics, guided generation, Tasks and shared
  Accounts. Generated reports can be sent by email; run ledgers remain
  read-only. A report prints as a clean PDF through the
  browser's print dialog: only the sheet, on white, figures kept with their
  captions, folded quotes left out unless unfolded.
- **History:** `isbe import-reports` brings in reports laid out as
  `<topic>/<period>/latest.md`; base64 figures become files, and a period ISBE also reported on
  lands beside its own report as `<period>-v1` for comparison. Memory review,
  replaying individual runs and feedback writes remain planned.
- **Access:** local mode requires no login and binds only to loopback.
  `--shared` requires a bootstrapped administrator and built-in login. Users
  manage their own paper topics, tasks and reports, and submit jobs directly
  without approval. Administrators manage service settings and accounts, view
  global tasks and may read other workspaces. Detailed runs, LLM calls and
  triage APIs are administrator-only. Passwords use salted PBKDF2 hashes;
  only hashes of 24-hour session tokens are stored. Password reset or account
  deactivation revokes sessions. Host and request-token checks also protect
  writes. Use HTTPS at a reverse proxy and `--secure-cookies` for shared access;
  the flag sets cookie attributes but does not provide TLS.

## 8. essaies integration

With `ESSAIES_URL` set, ISBE relies on these parts of the essaies API:

1. `GET /api/search` takes `since=YYYY-MM-DD` (on `update_date`),
   `categories=a,b` (any listed category, cross-lists count), `sort=date`, and
   `limit` up to 2000; every row carries `update_date` and `categories`.
   An older server answers ISBE's query with 422, which ISBE reports as
   "update essaies" instead of collecting unfiltered results.
2. The archive covers the arXiv categories a topic lists, with PDFs for the
   papers it returns.
3. `GET /api/file?path=` serves a paper's PDF to the metrail path.

## 9. Status and roadmap

Implemented: question runs (search plan, search, AI screening, one-hop
citation expansion, drawn as galaxies on Home), collection (essaies, arXiv,
RSS, GitHub), triage, full text and
evidence cards (arXiv HTML + LLM, or metrail-web), the digest with reviewer,
the durable report queue, manual email, the console (overview, reports,
triage, runs, settings, topics, tasks, accounts), shared mode, fixed-material
research reports, source management and web search, and media compilations
and editorial briefings.

Not implemented:

- the scheduler and scheduled notifications (`isbe notify`);
- memory review (`isbe review` and a review page); drafts under
  `memory/.pending/` are reviewed and merged by hand;
- `zhihu` and `exec` sources, the articles digest, the monthly roll-up and
  week-over-week comparison;
- a unified workflow selector in the web queue, and web editing of
  fixed-material or briefing inputs.

## 10. Out of scope

Postgres, Prometheus metrics, a plugin ABI and i18n of log messages.
