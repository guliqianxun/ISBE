# Architecture

ISBE is one Python 3.14 package (`isbe/`) that serves the web console, runs
the command-line jobs and reads and writes a single data directory. This page
describes how the code is organised and which behavior contributors must not
break. For what the stages do, see [DESIGN.md](DESIGN.md); for a data
directory created by the former Rust build, see
[upgrading-from-rust.md](upgrading-from-rust.md).

## Compatibility contract (do not break)

A data directory and a browser session from an earlier release keep working
with the current service. Keep these surfaces compatible.

| Surface | Rule |
| --- | --- |
| Data directory | Layout: `isbe.db`, `settings.json`, `topics/`, `memory/`, `reports/`, `research/`, `figures/`, `workspaces/`, lock files and research bundles. |
| Database | `migrations/*.sql` are applied in order by `PRAGMA user_version`. New migrations are appended; existing ones never change. |
| Settings | Keys, validation, precedence (non-empty environment > `settings.json` > defaults) and redaction. |
| Topics | YAML schema and validation errors; `${VAR}` expansion rules. |
| Accounts | PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte salt, 32-byte hash; session tokens are 64 hex chars stored as SHA-256. |
| HTTP API | Paths, methods, request bodies, status codes and JSON shapes; errors are `{"error": "..."}`. The single-page UI in `web/` depends on them. |
| CLI | Existing commands and flags in `isbe --help` keep working (`python -m isbe` or `isbe`). |
| Reports | Markdown structure, file names, figure names and research/media bundle formats; old reports must still render. |
| Prompts | `prompts/` is shared by all report languages. Cache keys (prompt and retrieval hashes) stay stable; a change that must alter one documents the one-time cache invalidation. |

## Running and checking

- Tests: `scripts/py-test` (Docker, `python:3.14-slim`, locked dependencies).
- Image: `docker build -t isbe .`. The container uses the `/data` volume, uid
  1000, the `isbe` entry point and `ISBE_DATA` / `ISBE_BIND`.

## Layout

Dependencies point down this list only: an interface may import features and
core, a feature may import core and the features listed before it, and core
imports no feature.

```
isbe/
  core/          errors, assets, settings, validators, db, paths, locks,
                 http, netguard, llm, markdown
  topics/        model (topic schema), store (topic files)
  auth.py        accounts and sessions
  memory.py      memory notes and pending drafts
  collect/       paper-topic collectors: arxiv, essaies, rss, github
  articles/      original-page fetch, text extraction, publication dates, cache
  sources/       reader (RSS/Atom/page reading, discovery), registry, cli,
                 article identity and duplicate timeline
  search/        search plans, SearXNG, date rechecks, materials,
                 source capabilities, query syntax
  pipeline/      triage, enrich (arxiv_html, metrail, cards), digest (render, parse, review)
  reports/       library lookup, importer, research, media, briefing
  mail/          configuration, composition, smtp, zeptomail, send limits
  generation/    durable queue and the single worker
  web/           FastAPI app: guards (host, CSRF, session, workspace), routes per area
  cli.py         argparse front end
tests/py/        pytest suite mirroring the package layout
```

Outside the package: `web/` (the console: plain HTML, CSS and JavaScript, no
build step), `prompts/` (`zh/` and `en/`), `migrations/`, `examples/`,
`deploy/searxng/` (an optional local SearXNG), `docker/` (the test image) and
`scripts/py-test`. `web/`, `prompts/` and `migrations/` are copied into the
package (`isbe/_assets`) when the image is built.

## Conventions

- **Keep visible behavior stable.** JSON, files, rows, the messages the UI
  shows and CLI output are interfaces; change them deliberately and with a
  test.
- **Modules stay small.** Aim for under ~400 lines; split by responsibility.
- **Types.** Type hints everywhere; `dataclass` for internal records, pydantic
  models (`extra="forbid"`) for input that must reject unknown fields.
- **Errors.** Raise `core.errors.HttpError(status, message)` for failures the
  user can act on. Everything else is internal: logged, shown generically.
- **State.** No module-level mutable state beyond the documented settings
  cache and context variables (`db.task`, `settings.task_scope`).
- **Database.** `db.connect()`; explicit `with db.immediate(conn):` for writes
  that must be atomic; parameterized SQL only.
- **Files.** `paths.atomic_write` for every write that replaces data; refuse
  symlinks under the data directory (`paths.check_tree`).
- **Network.** All outbound HTTP through `core.http.Http`; anything fetching
  a user- or web-supplied URL uses the public-only mode from `core.netguard`.
  Bound every response body.
- **Secrets.** Never log or return keys, tokens, passwords or provider bodies.
- **Comments.** Explain why; no narration.
- **Logging.** `logging.getLogger(__name__)`.

## Tests

- `scripts/py-test [pytest args]` runs the suite in the pinned
  `python:3.14-slim` container (uv 0.13.3). `scripts/py-test --lock` refreshes
  `uv.lock` after a dependency change.
- Add a test with each behavior change. No network: use
  `httpx.MockTransport` or a local `http.server` thread.
- Front-end tests: `node --test web/*.test.cjs tests/*.test.cjs`.
