# Security Policy

## Reporting a vulnerability

**Please do not open a public GitHub issue for security problems.**

Instead, report privately via GitHub's
[Security Advisories](../../security/advisories/new) ("Report a vulnerability"),
or email the maintainer. We aim to acknowledge reports within 7 days.

## Scope notes specific to ISBE

ISBE handles a few sensitive areas — please be mindful when reporting or contributing:

- **API keys & secrets** live in `.env` (git-ignored). Never commit real keys.
  `.env.example` must contain placeholders only.
- **Self-hosted services** (Postgres, MinIO, Prefect, Phoenix, RSSHub, etc.)
  bind to localhost by default. Do not expose them to the public internet
  without authentication.
- **Collectors fetch third-party content** (RSS, crawlers, arxiv/SEC). Treat
  fetched HTML/PDF as untrusted input.

## Supported versions

This is an early-stage project; only the latest `main` is supported.
