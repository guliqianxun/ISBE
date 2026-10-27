# Development standards

These pages say what good work on ISBE looks like and how it is checked. They
sit under [process.md](../process.md), which says how issues and merge requests
carry real-run evidence and get a separate review; these pages say what that
review holds the work to.

| Page                       | Read it when you                                                                                               |
| -------------------------- | -------------------------------------------------------------------------------------------------------------- |
| [frontend.md](frontend.md) | change `frontend/` or `web/`: states, data, navigation, accessibility, layout, text, tests                     |
| [backend.md](backend.md)   | change `isbe/`, `migrations/` or `scripts/`: failure handling, run lifecycle, data volume, logging, API, tests |
| [done.md](done.md)         | think a task is finished: what a merge request must contain before review, and before merge                    |
| [review.md](review.md)     | review a merge request or close an issue: reviewer roles, severities, rounds, the closing checklist            |

Reviewer roles are defined as Claude Code agents in
[`.claude/agents/`](../../.claude/agents): `frontend-reviewer`,
`backend-reviewer` and `product-reviewer`. The checklists live only in
[review.md](review.md#reviewer-roles); a person reviewing uses them as they
are, and the agent files only add how an agent sets up and runs.

## Why these rules exist

Three independent reviews of `main` at `b208226` (2026-10-24) found the same
few failures from different sides: the frontend review
(`docs/reviews/2026-10-24-frontend.md` on branch `ai/review-frontend`), the
backend review (`docs/reviews/2026-10-24-backend-review.md` on
`ai/review-backend`) and the product review
(`docs/reviews/2026-10-24-product-review.md` on `ai/product-review`). They
move to `docs/reviews/` on `main` when those branches merge. A citation such
as "(backend B1)" names a finding in one of them. Each rule
below names the finding it is there to prevent, so a rule can be dropped once
its reason is gone and a new one can be argued from evidence.

| Theme                        | What the reviews saw                                                                                                                                                                             | Rules                                                                                                                                                                  |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fail fast, fail honestly     | 2½–4 min of per-query retries against a dead essaies, then "This run failed." with no reason; capabilities cached empty after one failed request; a fallback plan reported as `completed`        | [backend: Upstreams](backend.md#upstreams-fail-fast), [backend: Honest outcomes](backend.md#honest-outcomes), [frontend: States](frontend.md#every-state-has-a-screen) |
| Run lifecycle end states     | zombie runs after a 429 or a concurrent ask that cannot be cancelled or deleted; streams that never end on `done`; a failed run showing its last stage as running; a missing run retried forever | [backend: Run lifecycle](backend.md#run-lifecycle), [frontend: States](frontend.md#every-state-has-a-screen), [frontend: Streams](frontend.md#streams-and-polling)     |
| `main` is green              | no CI; `main` red on a clean checkout (#70), which also stops `scripts/py-test` before the frontend tests                                                                                        | [done.md: Green](done.md#3-green-on-current-main), [review.md: Closing](review.md#closing-an-issue)                                                                    |
| One navigation               | two consoles with two sidebars; unknown paths answer 200 with Overview                                                                                                                           | [frontend: Navigation](frontend.md#one-console-one-navigation)                                                                                                         |
| Data volume                  | Home downloads 5.2 MB of abstracts to draw dots; every paper stored four times, 18 MB per run, nothing deleted                                                                                   | [backend: Data volume](backend.md#data-volume), [frontend: Data](frontend.md#fetch-what-the-view-shows)                                                                |
| Logging                      | a failed run left one log line; no health endpoint                                                                                                                                               | [backend: Logging](backend.md#logging-and-health)                                                                                                                      |
| Accessibility and responsive | 222 of 422 papers reachable only by mouse; no skip link; the Galaxies table and the run page break at 390 px                                                                                     | [frontend: Accessibility](frontend.md#accessibility), [frontend: Layout](frontend.md#layout-at-1440-and-390)                                                           |
| Tests of interaction paths   | no test for 404, 401 or a failed run; risky canvas code at 30 % coverage; concurrency untested; tests that sleep                                                                                 | [frontend: Tests](frontend.md#tests), [backend: Tests](backend.md#tests)                                                                                               |
| Honest copy                  | "review the plan" with a 10 s countdown; "Unjudged 0"; "Done" for a partial run; an Answer language for answers that do not exist; a Chinese question answered with an English plan              | [frontend: Text](frontend.md#text-says-what-is-true), [backend: Model output](backend.md#model-output), [review.md: Product reviewer](review.md#product-reviewer)      |
| First run                    | an empty Home that says nothing about what ISBE does, searches or needs                                                                                                                          | [F-S7](frontend.md#every-state-has-a-screen), [review.md: Product reviewer](review.md#product-reviewer)                                                                |
| Classic console              | `web/app.js` unlinted, escaping by convention; a second shell with its own navigation                                                                                                            | [F-W1, F-R1–F-R5](frontend.md#retiring-the-classic-console)                                                                                                            |

## Where these come from

Borrowed, adapted to this repository:

- [Google engineering practices, code
  review](https://google.github.io/eng-practices/review/): approve a change once
  it improves the code's health, even if it is not perfect; review design,
  function, tests and naming; comment on the code, not the author.
- [Conventional Comments](https://conventionalcomments.org/): every finding
  says its severity up front, so the author knows what blocks.
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) level AA for the console, checked by
  keyboard walks today, and by [axe-core](https://github.com/dequelabs/axe-core)
  once it is in the browser tests (a gap below).
- The evaluator pattern in agent review: the reviewer **runs** the change and
  attaches its own output; reading the diff alone is not a review.
- The [Twelve-Factor](https://12factor.net/logs) view of logs as an event
  stream to stderr, for the service's logging.

## Gaps these pages name but do not fix

Some rules need tooling that does not exist yet. Until it does, the rule is
checked by hand and the review says so. Each is a follow-up issue, not part
of this change:

- #74 `eslint-plugin-jsx-a11y` and type-aware ESLint rules (`no-floating-promises`).
- #75 A Python type checker.
- #74 ESLint for `web/app.js`, or its pages moved to React (F-W1).
- #76 A seed command that fills a scratch data directory with recorded question
  runs (finished, failed, partial; 5 and 100 runs) without network, so
  F-D3 and B-D5 numbers are comparable between authors and reviewers.
- #77 A browser probe script (headless Chrome over the DevTools protocol) that
  prints a page's network total, 10 s idle cost, keyboard tab order and
  390 px `scrollWidth`, with axe-core, for F-D3, F-I2, F-A8 and F-L1.
- #78 `docs/testing.md` still says the Frontend and Browser layers "arrive with
  the React shell"; it needs updating to what exists.
