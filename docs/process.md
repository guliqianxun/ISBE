# Issues, merge requests and review

Every issue update and every merge request carries **objective material from a
real run**, not only prose. A reader must be able to see what happened and
repeat it. Before an issue is closed or a merge request is asked to merge, a
**separate reviewer** (a person or an agent that did not write the change)
reviews it and records the findings on the merge request.

This applies to people and AI agents alike. What the work and the review are
held to is in [standards/](standards/README.md): the
[frontend](standards/frontend.md) and [backend](standards/backend.md)
standards, the [definition of done](standards/done.md) and the
[review standard](standards/review.md) with its reviewer roles, severities and
closing checklist.

## What counts as evidence

| Kind | When | Example |
| --- | --- | --- |
| Commands | always | the exact commands, environment variables (secrets and internal hosts redacted in anything that can reach the public mirror) and data directory used |
| Test output | any code change | the CI pipeline link for the head commit; when the pipeline cannot run, the tail of `scripts/py-test` with the short sha ([done.md](standards/done.md#3-green-on-current-main)). The tail of a single test file shows a failure before a fix |
| Screenshot | anything a person sees: console pages, the star map, reports | a PNG of the real page served by `isbe serve`, at the widths the issue names (1440 and 390 by default) |
| CLI output | commands, stage changes | the real output of `isbe ...`, trimmed but not edited |
| Log excerpt | background work, the queue, the model, essaies | the lines that show the stage ran: request counts, timings, errors |
| Data excerpt | stages that store rows | a `sqlite3` query or an API response (`curl .../api/...`) showing the stored result |
| Numbers | recall, screening, performance | before/after with how they were measured; Strata output varies, so two runs |

Prose ("works", "tested locally") never stands in for any of these. If something
could not be run (a quota is out, a service is down), say so, show the failure,
and name what is still unverified.

### Capturing it

```bash
# test output
scripts/py-test 2>&1 | tail -20
scripts/web-test 2>&1 | tail -30

# CLI output and logs into a file you can attach
isbe --data "$DATA" question "..." --engine arxiv 2>&1 | tee run.log

# a screenshot of a page, plus its HTTP status, console errors and main text
PORT=18090   # your own free port, see below
scripts/screenshot.mjs http://127.0.0.1:$PORT/ home-1440.png 1440 900
scripts/screenshot.mjs http://127.0.0.1:$PORT/ home-390.png 390 844 20000
```

`scripts/screenshot.mjs` drives headless Chrome (swiftshader gives it WebGL),
waits a fixed time (15 s by default, the last argument) and then takes the
shot; it fails without a PNG when the page does not load or answers other than
2xx. `google-chrome --screenshot` takes the shot before requestAnimationFrame
runs, so the run page shows "Loading…" there. The
desktop app's browser pane has no WebGL and pauses animation frames, so it is
not a source of screenshots either.

Run the server for evidence on a free port of your own (not 18081 or 18082,
which the console and e2e checks use) and on a scratch `--data` directory.

### Where it goes

Drag files into the GitLab note or merge request box when you work in the web
UI. From a terminal, `scripts/evidence` stores files on the `evidence` branch of
`origin` and prints Markdown you can paste:

```bash
export ISBE_EVIDENCE_WEB=http://<gitlab host>:<port>   # needed when origin is an ssh URL
scripts/evidence 66-galaxy-shell home-1440.png home-390.png run.log
# ![home-1440.png](http://<gitlab host>:<port>/moxunzhi/isbe/-/raw/<sha>/66-galaxy-shell/home-1440.png)
```

The links are absolute and pinned to the evidence commit. A host-relative link
(`/moxunzhi/isbe/-/raw/...`) does not work in an issue: GitLab rewrites it to
the default branch (`-/blob/main/...`), where the file is not.

The `evidence` branch is never merged and never pushed to the public `github`
mirror: screenshots and logs can show internal hosts. Nothing enforces this
except how the mirror is pushed, so push it by name (`git push github main`)
and never with `--mirror` or `--all`, which would send `origin/evidence` too.
Files with the same name in one call are refused; put them in separate folders.

## Issues

Use the templates in [`.gitlab/issue_templates/`](../.gitlab/issue_templates):
**Bug** (steps to reproduce, expected, actual, with the failing output) or
**Feature** (goal, scope, acceptance with the evidence that will prove it).

An update on an issue that reports progress carries the evidence for what it
claims. An issue is closed only when its acceptance items each have evidence on
the issue or on the merge request that closes it, and that merge request has
passed review. An issue closed without a merge request (an investigation, a
duplicate, won't fix) gets a closing note with the evidence for that outcome,
and a reviewer other than its author agrees in a note before it is closed.

## Merge requests

Use the [default template](../.gitlab/merge_request_templates/Default.md). It
asks for:

1. What changes for a reader, before and after.
2. How to reproduce: commands from a clean checkout.
3. Evidence from a real run (see above), including test output.
4. Docs and tests touched, and anything left unverified.

Because `git push -o merge_request.create` only sets a one-line description,
open the merge request, then fill the template in the web UI or put it in a note
on the merge request (or on its issue, when the merge request cannot be
edited).

## Review

The author asks for review when the merge request is complete and its evidence
is attached. The reviewer is never the author: for AI-authored work, a separate
reviewer agent that did not write the change, started with the merge request and
this page. The agents are defined in [`.claude/agents/`](../.claude/agents)
(`frontend-reviewer`, `backend-reviewer`, `product-reviewer`); which roles a
change needs is in [standards/review.md](standards/review.md#reviewer-roles).

The reviewer:

1. Checks out the branch and **runs it**: the tests, and the feature itself
   the way a user would (the page, the command, the API). The reviewer's own
   output and screenshots go into the review, so the review is evidence too.
2. Reviews to open-source project standards:
   - **Correctness**: does it do what the issue asks; edge cases, errors,
     concurrency, migrations, data that already exists.
   - **Tests**: the change is covered at the right layer
     ([testing.md](testing.md)); the tests fail without the change.
   - **Docs**: user-visible behavior, routes, options and limits are written
     down; `docs/README.md` links new pages.
   - **Reproducibility**: the merge request's commands work from a clean
     checkout; no hidden local state, no internal hosts or secrets committed.
   - **Evidence**: the material is real, current and shows the claim.
   - **Fit**: matches the surrounding code and [architecture.md](architecture.md)
     conventions; no unrelated changes.
3. Posts each finding as a merge request comment (or a note on the issue when
   the tooling cannot reach the merge request), marked with its severity
   (**blocking**, **major**, **minor** or **nit**), with the file and line,
   what goes wrong and how it was seen. A review with no findings still says
   what was run. What each severity means, the note template and how rounds
   converge are in [standards/review.md](standards/review.md).

The author resolves every blocking and major finding and answers every minor
one, in the ways the [severity table](standards/review.md#findings) allows for
each, and only then asks Zhiheng to merge. A
finding found after merge becomes a follow-up issue or merge request linked
from the original.
