# Review standard

How a merge request is reviewed, how its findings converge, and when an issue
may close. It adds to the review section of [process.md](../process.md#review):
the reviewer never wrote the change, **runs** it, and records findings with
evidence. The standards it reviews against are [frontend.md](frontend.md),
[backend.md](backend.md) and [done.md](done.md).

## Reviewer roles

A merge request is reviewed in each role its change needs. One reviewer may
hold several roles in one review; each role's checklist is still gone through
and reported.

| Role              | Needed when the change touches                                                                                                                            | Agent                                                            |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Frontend reviewer | `frontend/`, `web/`, interface text                                                                                                                       | [`frontend-reviewer`](../../.claude/agents/frontend-reviewer.md) |
| Backend reviewer  | `isbe/`, `migrations/`, `prompts/`, `scripts/`, `docker/`                                                                                                 | [`backend-reviewer`](../../.claude/agents/backend-reviewer.md)   |
| Product reviewer  | a flow a person goes through (a Feature issue that adds or changes a screen, a command's output, or interface copy), and every milestone before it closes | [`product-reviewer`](../../.claude/agents/product-reviewer.md)   |

Docs-only and test-only changes need one reviewer in the role of the area
they describe. When unsure, take the role; an extra review is cheap.

### Frontend reviewer

Holds the change to [frontend.md](frontend.md). Runs `scripts/web-test`,
serves the branch with a seeded data directory, and checks:

- every state of the changed views (F-S1–F-S7), forcing 404, 401, a failed
  and a finished run opened later, and an empty data directory, counting the
  requests that follow in 20 s;
- streams and polling end and pause (F-P1–F-P3);
- data fetched against what is drawn, with the network total (F-D1–F-D3);
- navigation in and out of the changed page (F-N1–F-N3);
- a keyboard walk, names, `lang`, contrast (F-A1–F-A8);
- 1440 and 390 screenshots and the `scrollWidth` check (F-L1–F-L3);
- idle cost for animated pages (F-I1–F-I3);
- copy and both languages (F-T1–F-T6);
- for `web/`, each `innerHTML` line added (F-W1);
- tests for the interaction paths (F-X1–F-X4), and that one fails without
  the change.

### Backend reviewer

Holds the change to [backend.md](backend.md) and architecture.md. Runs
`scripts/py-test` and `scripts/py-lint` on a clean checkout, runs the feature
with the CLI or `curl`, and checks:

- the change with each upstream it uses taken down (a stand-in answering 503,
  or a closed port), timing how long it takes to fail (B-U1–B-U6);
- the run's outcome and problems match what happened (B-O1–B-O4);
- each lifecycle cell touched, including concurrent and refused requests
  (B-L1–B-L6);
- stored size and response size (B-D1–B-D5), resource use of loops and
  streams (B-R1–B-R3);
- the log lines a run leaves (B-G1–B-G4);
- contract changes against api.md and the schemas (B-A1–B-A4);
- model output language and prompt measurements (B-M1–B-M3);
- migrations: appended only, and applied to a data directory made by `main`;
- tests for the failure, concurrency and lifecycle paths (B-X1–B-X6), and
  that one fails without the change.

### Product reviewer

Uses the product as a first-time user would, from an empty data directory,
in both languages, and checks:

- the first run: does the empty product say what it does, what it searches,
  what must be configured and how long a result takes (F-S7);
- the flow reaches the issue's goal for a person, not only for the API:
  what they asked for is what they get at the end;
- every promise the interface makes is kept (F-T1): switches do what they
  say, counters count what their names say, "done" means done;
- failure is quick and says what to do (F-S5, B-U1);
- the person can correct the system where the design says they can
  (overrides, edits, cancel) and take their result away (export);
- the change fits one navigation and one vocabulary (F-N1, F-T5), and model
  text is in the person's language (B-M1);
- docs and README describe what is on screen.

It times the flow (how long to the first useful screen, to the result) and
attaches screenshots of each step.

## Findings

Every finding has:

- an id within the review (`B1`, `M3`, `m2`, `n1`);
- a severity, one of:

  | Severity     | Meaning                                                                                                | Answered before merge by                                                                                                           |
  | ------------ | ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------- |
  | **blocking** | wrong result, data loss, security or privacy, broken contract, red tests, missing evidence for a claim | a fix the reviewer has re-checked; or the reviewer agreeing in a note that it is not blocking, which turns it into a major finding |
  | **major**    | a real defect with a clear failure mode, or a must rule broken where the effect is limited             | a fix; a follow-up issue that Zhiheng agrees to in a note; or a declined reply the reviewer accepts                                |
  | **minor**    | worth fixing, little harm today, or a should rule                                                      | a fix; a linked follow-up issue; or a declined reply the reviewer accepts                                                          |
  | **nit**      | taste, wording                                                                                         | nothing; the author may ignore it                                                                                                  |

  This table is the only rule for what answers a finding; process.md and the
  rounds below refer to it. Merge waits for every blocking, major **and**
  minor finding to be answered in one of these ways; a minor finding is
  never left without a reply.

- where: `file:line`, route, or screen;
- what goes wrong, and **how it was seen**: the command, output or screenshot
  from the reviewer's own run. A finding that was only inferred from reading
  says "inferred, not reproduced", and cannot be blocking unless the code
  makes the failure certain;
- the rule it breaks (`F-S1`, `B-L2`), when there is one.

Review the change, not the author. A finding about code the merge request did
not touch is a separate issue, not a blocker for this one, unless the change
makes it worse.

**Existing breaches.** These standards are newer than most of the code. When
a page or module the change touches already breaks a rule on `main` (the run
page scrolls sideways at 390 px, no page has a skip link), the reviewer
reports it as a minor finding whose answer is a linked follow-up issue, not
a fix in this merge request. It becomes blocking or major only if the change
makes it worse or adds a new instance, or if the issue being closed is the
one about it.

## Rounds and convergence

1. **Review.** The reviewer posts the review note (template below) on the
   merge request, or on its issue when the merge request cannot be
   commented on. The verdict is **approve** or **changes requested**, tied
   to the head sha that was reviewed.
2. **Response.** The author answers every blocking, major and minor finding
   in one note: _fixed in `<sha>`_ with new evidence, _declined_ with the
   reason, or _follow-up #n_, as far as [the table](#findings) allows for its
   severity. Nits need no answer.
3. **Re-review.** The reviewer re-checks each answered finding on the new
   head, re-runs the tests, and looks at the new diff for regressions (not
   the whole change again). Each finding becomes **resolved**, **open** or
   **accepted** (declined with a reason the reviewer accepts). New findings
   get new ids.
4. Repeat until no blocking or major finding is open and every minor one is
   answered. The reviewer's last
   note says **approve** with the sha.

Commits after approval need a re-review of the delta before merge. If the
author and reviewer still disagree on a finding after two rounds, the author
puts both positions to Zhiheng in one note, and his answer settles it.

A reviewer agent starts fresh for each round from the merge request, the
previous notes and these pages; it does not need the author's context, and
should not be given the author's conclusions.

## Closing an issue

An issue closes only when each line holds, written in the closing note:

- [ ] Each acceptance item is ticked and links its evidence (a merge request
      note, a review, or a file on the `evidence` branch).
- [ ] The merge request that closes it is merged, and its approving review is
      linked.
- [ ] `main` after the merge passes `scripts/py-test` (sha and tail), or the
      CI pipeline for that commit is green.
- [ ] For a bug, the issue's own reproduction steps now pass, with their
      output.
- [ ] Every finding that was not fixed has its follow-up issue linked.
- [ ] Docs for the change are on `main`.

An issue closed without a merge request (investigation, duplicate, won't fix)
has a closing note with the evidence for that outcome, and a reviewer other
than its author agrees in a note before it is closed (process.md).

### Closing a review report

A review report (such as the three 2026-10-24 reviews, see
[README.md](README.md#why-these-rules-exist)) is closed by its
findings, not by time. Each finding becomes an issue or a line of a grouped
issue; the report's issue lists them with their state. A finding is closed
only when a reviewer other than the fixer has re-checked it on `main`, using
the report's own reproduction where it gave one. The report's issue closes
when every blocking and major finding is closed and every minor one is
closed or filed.

## Review note template

```markdown
## Review: <frontend | backend | product> — <approve | changes requested>

Reviewed `<sha>` of !<n> by <reviewer>; I did not write this change.

### What I ran

<commands, data directory, env names (no secrets), ports>

<test tails, screenshots, logs from this run>

### Findings

- **B1 (blocking)** `<file:line or screen>` — <what goes wrong>.
  Seen: <command/output/screenshot>. Rule: <F-…/B-…>.
- **M1 (major)** …
- **m1 (minor)** …
- **n1 (nit)** …

### Checked and fine

<the checklist items that held, one line each>

### Not verified

<what could not be run, and why>
```

A response note uses the same ids:

```markdown
## Response to review of `<sha>`

- **B1** fixed in `<sha>`: <one line>. Evidence: <link>.
- **M1** follow-up #<n> (Zhiheng agreed: <link>).
- **m1** declined: <reason>.
```
