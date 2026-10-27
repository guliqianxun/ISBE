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
| Settings | Keys, validation, precedence (non-empty environment > `settings.json` > defaults) and redaction; the import/export file (`settings_file`, [settings.md](settings.md)). |
| Topics | YAML schema and validation errors; `${VAR}` expansion rules. |
| Accounts | PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte salt, 32-byte hash; session tokens are 64 hex chars stored as SHA-256. |
| HTTP API | Paths, methods, request bodies, status codes and JSON shapes; errors are `{"error": "..."}`. The console (`frontend/`, and `web/` until its pages move over) depends on them. |
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
  question/      question research: search plan, runs and events, essaies search, paper sets
                 (screening and graph to come)
  web/           FastAPI app: guards (host, CSRF, session, workspace), routes per area
  cli.py         argparse front end
tests/py/        pytest suite mirroring the package layout
```

Outside the package: `frontend/` (the React console: React 19, TypeScript,
Vite; `npm run build` writes `isbe/_assets/app/`, git-ignored), `web/` (the
classic console: plain HTML, CSS and JavaScript, retired page by page as
`frontend/` takes over), `prompts/` (`zh/` and `en/`), `migrations/`,
`examples/`, `deploy/searxng/` (an optional local SearXNG), `docker/` (the
test image) and `scripts/` (`py-test`, `web-test`). `web/`, `prompts/`,
`migrations/` and the React build are copied into the package
(`isbe/_assets`) when the image is built; Node runs only in the image's
build stage.

### Two consoles during the move to React

`isbe serve` answers the classic console's pages (`CLASSIC_PATHS` in
`isbe/web/routes.py`, exactly `PAGES` in `web/app.js`) with
`web/index.html`, and every other page with the React build's page: with
200 for the pages React routes (Home `/`, the paths in `REACT_PATHS`,
currently `/galaxies`, `/settings`, `/system`, `/help` and `/question`, and a
run under `/question/runs/`), and with 404 for any other path, where React
draws its not-found page. Both consoles draw the same sidebar (Home, Galaxies,
Reports, Topics, Settings, System, Help) from `NAV` in
`frontend/src/shell/nav.ts` and its copy `NAV` in `web/app.js`; a Vitest
test keeps the two equal, and a Python test keeps `CLASSIC_PATHS` equal to
`PAGES`, and another keeps `REACT_PATHS` and `REACT_PREFIXES` equal to the
routes in `Pages` in `frontend/src/App.tsx`. Home reads one small view,
`GET /api/home` (topics, the newest three reports, service readiness). The classic pages that are not in the sidebar are listed on System,
and the classic overview lives at `/overview`. Help (`/help`) holds the
first-run guide, the report guide that queues a topic report
(`frontend/src/help/guide.ts`; the classic pages link to it with
`/help?topic=`, `?task=` or `?new=1`, plus `#report-guide`), and for
administrators a link to the service guide (docs/configuration.md and docs/deployment.md) rendered at `/help/service`. The build's files are served from `/app/assets/` with
content-hashed names and long-lived caching; nothing else under `/app/` is
served. Both consoles run under the same CSP (`script-src 'self'`, no inline
script), share the session cookie, the CSRF token from `GET /api/session`,
the `isbe.ui` preferences in `localStorage` and the theme tokens of
`web/app.css`, which the React build imports. Navigation between them is a
whole-page load. To move a page: implement it in `frontend/src`, add its path
to `REACT_PATHS` and mark it `react` in `NAV` or `SYSTEM` in
`frontend/src/shell/nav.ts` and in `NAV` in `web/app.js` (Vitest tests check
that the lists agree), take it out of `CLASSIC_PATHS` and `PAGES`, then
remove it from `web/`.

Home draws every question run as a galaxy on a 2D canvas
(`frontend/src/home/`). A galaxy's papers and citations are read only once it
is at least 40 px across on screen (`DETAIL_PX`), the selected galaxy first
and one at a time, and are kept for the browser session; a running galaxy is
read again at most every 15 s. Each field is settled in a module worker
(`field.worker.ts`, served from `/app/assets/` like the rest of the build, so
the CSP needs nothing more), with at most 600 papers as stars (`MAX_STARS`).
The canvas redraws while the camera moves and at 20 frames a second
otherwise (only on change under reduced motion), and not at all while it is
scrolled out of sight.

React conventions: data goes through `src/api/client.ts` (CSRF on writes,
`{"error": ...}` failures, a 401 returns to sign-in) and long runs through
`src/api/sse.ts`; outside text is rendered as React text, never as HTML, and
links to it go through `ExternalLink` (http(s) only); interface text lives in
`src/i18n.ts` in both languages.

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

  `httpx.MockTransport` or a local `http.server` thread, and the recorded
  essaies data and fake model of `tests/py/replay.py` for question research
  (see [testing.md](testing.md)). Tests marked `live` need real services and are
  skipped by default.

- Front-end tests: `scripts/web-test` runs the classic console's
  `node --test web/*.test.cjs tests/*.test.cjs`, then the React console's
  ESLint and Prettier check, type check, Vitest (React Testing Library) and
  production build. `scripts/py-test` without arguments runs it after
  pytest.