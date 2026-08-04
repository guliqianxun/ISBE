# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to adhere to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Grounded delivery pipeline (`digest.pipeline: cards`)**: per-paper
  evidence cards extracted from the FULL metrail corpus — deterministic regex
  layer (code URLs / GPU / dataset lexicon) + one anchored LLM extraction call
  whose every field carries a verbatim quote, verified LangExtract-style
  (exact → normalized → rejected). The template renders fact fields
  (方法/数据/代码/复现/效果/局限 + figure interpretation) straight from cards;
  the writer produces opinions only; a RAGAS-style reviewer audits opinion
  claims against the evidence with a single rewrite round. Enabled for
  nowcasting; other topics stay on `legacy`.
- **Core tables verbatim**: the paper's comparison/ablation tables (metrail
  table atoms, GFM markdown) ship in the report unparaphrased.
- **Monthly reports**: `monthly_digester` rolls the month's weeklies + memory
  into 月度总览/本月必读/论点演化/下月关注, emailed and archived like any
  digest; `radar topics run <topic> --monthly`; nowcasting scheduled on the
  1st. Cadence family is now daily / weekly / monthly.
- Audit block integrity: real `artifact_id` (precomputed), real OTel
  `trace_id`, evidence-card and reviewer summaries.
- **metrail-web integration**: new shared `metrail_enrich` flow extracts full
  text from downloaded arXiv PDFs via the LAN metrail-web service
  (`METRAIL_API_URL`; unset = disabled). `papers.fulltext_uri` (migration 005)
  points at the mirror-relative `<id>.metrail.md`. The weekly digester now
  includes **abstracts** (always) and **budgeted fulltext excerpts**
  (`digest.include_abstract` / `fulltext_per_paper_chars` /
  `fulltext_total_chars`) in the prompt — previously the facts block was
  title-only while the prompt demanded abstract-grounded output.
- Shared HTTP retry helper `isbe/http_retry.py` (extracted from the pattern
  duplicated in `llm/client.py` and `_shared/arxiv.py`).
- Prefect DB bootstrap: `infra/initdb/create-prefect-db.sql` auto-creates the
  `prefect` database on a fresh postgres volume (existing deployments: run
  `CREATE DATABASE prefect;` once by hand).
- Open-source project scaffolding: CI (GitHub Actions), issue/PR templates,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, this changelog,
  pre-commit config, and an English `README.en.md`.

### Fixed
- **Memory accept no longer destroys the thesis file**: `accept_pending` merges
  into an existing target (frontmatter kept, `revision` bumped, draft body
  appended under a dated heading) instead of overwriting it wholesale;
  duplicate drafts in one run uniquify (`-2`, `-3`) instead of clobbering.
- **DB engine leak**: one cached SQLAlchemy engine per DB URL instead of a new
  engine (and connection pool) per session-factory call — previously leaked
  per RSS feed / crawled page / flow run.
- **Dispatch no longer swallows broken imports**: a typo'd import inside a
  topic's digester/collector now raises `DispatchError` instead of silently
  substituting the generic weekly digester; the scheduler logs skipped
  schedules via `logging.error` + summary instead of a bare `print`.
- **topic_run persist failure no longer masks the flow's own exception**.
- **Email HTML sanitized** (nh3) — LLM/crawled content can no longer inject
  script/stylesheet tags into digest emails; `premailer` runs with
  `allow_network=False` (SSRF fix).
- License mismatch: project is MIT-licensed; the `LICENSE` file now contains the
  MIT text (previously Apache-2.0 while the README claimed MIT).

### Changed
- Server compose hardened: filebrowser requires auth and drops root; pgweb is
  loopback-only (SSH tunnel); `POSTGRES_PASSWORD` is required (no `changeme`
  default on the server); morerssplz git build pinned to a commit.
- `.env.example` now documents all env vars actually read by the code
  (`POSTGRES_HOST/PORT`, `MINIO_ENDPOINT`, mirror paths, LLM tier models,
  `METRAIL_API_URL`); dead `OPENAI_API_KEY` removed.
- `AGENTS.md` rewritten to describe the actual architecture (the previous
  version described an abandoned hermes-based design with dead links).

### Removed
- Dead containers `qdrant` and `uptime-kuma` (zero code references; base stack
  is now 6 services). Old volumes can be dropped manually:
  `docker volume rm isbe_qdrantdata isbe_uptimekumadata`.

## [0.1.0] - 2026-06-02

First consolidated release of the v1 pipeline.

### Added
- Three-layer data model: `facts` (re-fetchable atoms) × `memory` (your theses,
  one-file-per-thesis, git-friendly) → `artifacts` (LLM-written digests).
- Uniform topic interface: `collector(s)` → `digester` with a shared 5-section
  weekly digest contract (TL;DR / per-item review / cross-item comparison /
  analysis vs. memory / distillation proposals).
- Six active topics: `nowcasting`, `video-generation`, `image-restoration`,
  `nvda` (price + news + SEC), `motorcycle`, `china-tech`.
- Built-in collector presets: arxiv, GitHub, RSS, RSSHub, morerssplz, Crawl4AI,
  stock price / SEC.
- Self-hosted stack via docker-compose: Postgres, MinIO, Prefect (cron
  orchestration), Phoenix (LLM tracing), RSSHub, morerssplz, Qdrant (reserved),
  Uptime Kuma.
- Switchable LLM provider (`ISBE_LLM_PROVIDER=deepseek|anthropic`).
- Memory review CLI: distillation drafts → accept/reject → reindex.
- Academic-ink HTML email digest with plaintext fallback.

[Unreleased]: https://github.com/guliqianxun/ISBE/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/guliqianxun/ISBE/releases/tag/v0.1.0
