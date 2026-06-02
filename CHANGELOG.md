# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to adhere to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Open-source project scaffolding: CI (GitHub Actions), issue/PR templates,
  `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, this changelog,
  pre-commit config, and an English `README.en.md`.

### Fixed
- License mismatch: project is MIT-licensed; the `LICENSE` file now contains the
  MIT text (previously Apache-2.0 while the README claimed MIT).

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
