# Definition of done

A task is done when its issue's goal is met on `main`, not when its code is
written. This page lists what a merge request must contain before it is
**ready for review**, and what must be true before it is **ready to merge**.
The [merge request template](../../.gitlab/merge_request_templates/Default.md)
mirrors these items; [process.md](../process.md) says how evidence is
captured and stored.

## Ready for review

### 1. The issue says what done means

- The issue uses the Bug or Feature template. Each acceptance item names the
  evidence that will prove it (screenshot, CLI output, test, number).
- Scope that is out is written down, so a reviewer can tell a gap from a
  choice.
- A fix that comes from a review finding links the finding (for example
  "backend review B1").

### 2. One change, on its own branch

- Branch `ai/<issue>-<slug>` (or a person's own prefix) from current `main`, one
  issue per merge request. When the issue cannot be created first (the tracker
  is unreachable), name the branch `ai/<slug>`, open the issue as soon as it can
  be, and link it from the merge request. Stacked merge requests say which one
  they sit on.
- No unrelated changes: formatting, renames and drive-by fixes go in their
  own merge request.

### 3. Green on current main

- The branch is rebased on (or merged with) the current `origin/main`.
- The merge request's [CI pipeline](../testing.md#ci) for the head commit is
  green, and this item is the pipeline link. The pipeline runs `scripts/py-lint`,
  the whole Python suite and everything in `scripts/web-test` as separate jobs,
  with coverage.
- Locally, `scripts/py-test` with no arguments runs the same checks; use it
  before pushing. When the pipeline cannot run (no runner, GitLab down), the
  tail of its output with the head commit's short sha stands in, and the
  merge request says why. A selection (`scripts/py-test tests/py/...`) is fine
  while working, not as the evidence.
- A test that fails on `main` too is reported (an issue linked), never
  skipped or deleted to make the run green.

### 4. Tested at the right layer

- Every behavior change has a test, at the layer [testing.md](../testing.md)
  gives for it. A bug fix starts with a test that fails without the fix; the
  merge request shows that failure once (the test output against `main`, or
  the commit before the fix).
- Failure paths are tested, not only the happy path: see
  [F-X1](frontend.md#tests) and [B-X1 to B-X4](backend.md#tests).

### 5. Run for real, with evidence

- The change was run the way a user would meet it, and the merge request
  carries the material [process.md](../process.md#what-counts-as-evidence)
  lists, plus the extra items for the area:
  [frontend](frontend.md#evidence-for-a-frontend-merge-request),
  [backend](backend.md#evidence-for-a-backend-merge-request).
- The failure modes the change touches were run too: a service down, an id
  that does not exist, a signed-out session, an empty data directory.
- All evidence is from the current head. After new commits, re-take what
  they could change.

### 6. Docs match the behavior

- User-visible behavior, routes, events, options, limits and lifecycles are
  in `docs/` (and the README feature table when a feature is new);
  `docs/README.md` links new pages.
- Interface text is in both languages.

### 7. Safe to publish

- No secrets, keys, internal hosts or local paths in committed files; the
  repository is mirrored publicly.
- Migrations are new files appended in order; existing data directories keep
  working (architecture.md's compatibility contract).

### 8. Honest about what is left

- "Not verified" lists what could not be run and why (quota out, host
  down), with the failure shown.
- Known follow-ups are filed as issues and linked.

## Ready to merge

- A separate reviewer reviewed the current head under [review.md](review.md),
  in every role the change needs, and the review is linked.
- Every blocking and major finding is resolved, and every minor one answered,
  as review.md's severity table allows; the reviewer's last note says so.
- The merge request description is filled in (or, when it cannot be edited,
  the issue note that stands in for it is linked from the merge request).
- Then the author asks Zhiheng to merge, with one line: what changes, the
  review link, and anything left unverified.

## After merge

- Link the first green `main` pipeline that contains the merge commit on the
  issue (a later push to `main` cancels a running one), or, when it cannot
  run, the tail of `scripts/py-test` on the new `main`. A red `main` is fixed before other merges.
- Close the issue with the [closing checklist](review.md#closing-an-issue).
