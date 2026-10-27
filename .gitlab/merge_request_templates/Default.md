<!-- See docs/process.md. Every section needs real material, not prose. -->

Closes #

## What changes

**Before:**

**After:**

## How to reproduce

```bash
# from a clean checkout of this branch: setup, data, env (no secrets), commands

```

## Evidence from a real run

<!-- Screenshots of the real UI, CLI output, log excerpts, API or sqlite output.
     Drag files here, or paste the links from scripts/evidence. -->

### Tests

```text
# tail of scripts/py-test / scripts/web-test / the single test file

```

## Checklist

See `docs/standards/done.md` for what each item means.

- [ ] Branch is on current `main`; one issue, no unrelated changes
- [ ] The CI pipeline for the head commit is green (link), or, when it cannot run, `scripts/py-test` (whole suite, includes `scripts/web-test`) and `scripts/py-lint` pass on the head with the tail above and the short sha
- [ ] Ran the change for real, including the failure modes it touches; evidence above is from the current head
- [ ] Area evidence: frontend (1440/390 screenshots, 390 `scrollWidth`, keyboard walk, network total, idle cost) / backend (logs, stored rows, upstream-down timing, sizes)
- [ ] Tests added or updated for each behavior and failure path, and they fail without the change
- [ ] Docs updated for user-visible behavior (`docs/`, README, `docs/README.md`); interface text in both languages
- [ ] No secrets or internal hosts in committed files; migrations appended only
- [ ] Not verified, and why:
- [ ] Reviewed by a separate reviewer in each role needed (frontend / backend / product, `docs/standards/review.md`); every blocking and major finding resolved and every minor one answered (link):
