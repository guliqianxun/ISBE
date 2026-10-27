# Frontend standard

Rules for the React console in `frontend/` and the classic console in `web/`.
Each rule has an id (`F-…`) that reviews cite. Every rule is a **must** unless
it is marked _(should)_. Breaking a must rule is a blocking or major finding,
depending on its effect ([review.md](review.md#findings)); breaking a should
rule is minor. Names of new routes, events and settings given "for example" are
for the fix issue to settle, not decisions; names of existing browser and API
features (`visibilitychange`, EventSource's events) are fixed. The conventions
already in
[architecture.md](../architecture.md#two-consoles-during-the-move-to-react) (API
client, `ExternalLink`, `i18n.ts`, CSP) still hold and are not repeated here.
For look and feel, the run page (`Flow.tsx`, `question.css`) is the style
reference.

## Retiring the classic console

The classic console in `web/` is frozen and leaves page by page (decided by
Zhiheng, 2026-10-24). Until the last page is gone:

- **F-R1** `web/` takes bug fixes and exit work only (the link, redirect and
  sidebar changes F-R2 and F-R4 ask for). A new page, a new feature or a
  redesign of a classic page is built in `frontend/`, and moves that page
  (the steps are in
  [architecture.md](../architecture.md#two-consoles-during-the-move-to-react)).
- **F-R2** Each classic page has one exit issue, labelled `classic-exit`,
  listed in the table below with its state (`classic` until work starts,
  `moving` while its merge request is open, `moved` with that merge request
  once merged). An
  exit issue's acceptance is:
  - the React page does everything the classic one does for the same routes
    and data: a parity list of its actions, each shown working in the
    evidence (screenshots at 1440 and 390, both languages);
  - its path is in `REACT_PATHS` and marked `react` in `nav.ts`;
  - its code, strings and tests are removed from `web/` in the same merge
    request, and old links and bookmarks still land on the page: classic
    links and redirects to it become whole-page loads (not the classic
    `go()` with `pushState`), checked by clicking each one;
  - when the page is a target of the classic console's own fallbacks (today
    `/overview`: unknown paths and the non-admin redirect in `render()`),
    the same merge request moves those fallbacks: unknown paths get the
    React not-found page with a 404 status (F-N2), and the redirect goes to
    a React page;
  - a product review of the moved page (review.md).
- **F-R3** Order: the pages a new install needs and the ones the reviews
  hit first. The order is in the table; an exit issue may change it with a
  reason.
- **F-R4** While both consoles exist, the classic shell links back to Home
  and lists every React page (F-N1), so nobody is stranded in it.
- **F-R5** When every row is `moved`, `web/index.html`, `web/app.js`, the
  classic fallback in `isbe/web/routes.py` and the classic tests are
  removed.

| Order | Classic page        | Path        | Why first                                                                | Exit issue | State       |
| ----- | ------------------- | ----------- | ------------------------------------------------------------------------ | ---------- | ----------- |
| 1     | Settings            | `/settings` | a new install needs it first (product M2)                                | #79        | moved (!35) |
| 2     | Reports             | `/reports`  | in the React sidebar already; the digest is the best output (product M2) | #80        | classic     |
| 3     | Overview            | `/overview` | answers every unknown path today (frontend M2)                           | #81        | classic     |
| 4     | Topic settings      | `/topics`   | the digest flow starts here                                              | #82        | classic     |
| 5     | Sources & materials | `/sources`  | polls every 2 s in background tabs (frontend M6)                         | #83        | classic     |
| 6     | Task center         | `/tasks`    | polls every 2 s (frontend M6)                                            | #84        | classic     |
| 7     | Triage              | `/triage`   |                                                                          | #85        | classic     |
| 8     | Runs & LLM          | `/runs`     |                                                                          | #86        | classic     |
| 9     | Users (shared mode) | `/users`    |                                                                          | #87        | classic     |

Bugs in a classic page that is next in line are fixed by moving it, not
by patching `web/`.

- **F-W1** In `web/app.js`, every value put into `innerHTML` goes through
  `esc()` (or is a constant), and a merge request touching `web/` lists the
  `innerHTML` lines it adds. Nothing lints `web/app.js` yet (see
  [the gaps](README.md#gaps-these-pages-name-but-do-not-fix)), so the
  reviewer checks each one. _(frontend M7.)_

## Every state has a screen

A view that loads or streams data is a state machine, and every state is
drawn on purpose.

- **F-S1** A view must render each of: loading, loaded, empty, error, **not
  found** (404) and **signed out** (401). A 404 shows a not-found screen with
  a way back; a 401 goes to the session layer (`api/client.ts`), which signs
  the person out everywhere. _(frontend B1: a missing run said "Loading…"
  forever.)_
- **F-S2** Retries must be bounded and must stop on answers that will not
  change: any 4xx except 408 and 429 ends retrying. Network retries back off
  and give up after a stated number of attempts or seconds, then show the
  error state with a retry button. Nothing retries forever.
- **F-S3** Terminal statuses win. When the server says a run is `failed`,
  `cancelled` or `done`, no stage, chip, spinner or button may say it is still
  running. Derive what is shown from the run's status first and from events
  second. _(frontend M4: "Expanding citations" next to "Run failed".)_
- **F-S4** A finished run opens as finished. Replaying a finished run's events
  must not show live controls (Pause, Cancel) or a "Connection lost" banner on
  the way. _(product M1.)_
- **F-S5** An error message must say what failed, why (the server's reason,
  translated where it is a known case) and what the person can do: retry,
  check a service, change a setting. "This run failed." alone is not enough.
  _(product B2.)_
- **F-S6** Degraded is not done. A `partial` run, a skipped stage or a fallback
  plan must be visible as such wherever the run's state is shown (Home card,
  Galaxies row, run header). _(product M5.)_
- **F-S7** An empty state explains the page to someone who has never used
  it: what it shows, what to do first, what must be configured for it to
  work (and whether it is reachable), and how long the first result takes.
  _(product M3: "No galaxies yet." and nothing else.)_

## Streams and polling

- **F-P1** Streams go through `src/api/sse.ts` and must tell a transport
  failure from an application event. Server events must not be named after
  EventSource's own events (`error`, `open`, `message`). A stream on a run
  that has come to rest must not reconnect in a loop or show "Connection
  lost"; whether it closes on `done` is the contract in api.md
  ([B-L5](backend.md#run-lifecycle)). _(product M1,
  backend M4; the event names are an API contract, see
  [backend.md](backend.md#api-contract).)_
- **F-P2** Because EventSource cannot see HTTP statuses, a stream that fails
  must check the resource with a normal request (through the API client)
  before reconnecting, so 401 and 404 reach F-S1.
- **F-P3** Polling must pause while the tab is hidden (`visibilitychange`),
  stop when nothing it watches is active, and use the longest interval the
  view can bear. _(frontend M6.)_

## Fetch what the view shows

- **F-D1** A view must request only the fields it draws. When an endpoint
  returns more (abstracts for dots), ask for a slim endpoint or projection in
  the same issue instead of discarding the rest in the browser. _(frontend M1:
  5.2 MB to open Home.)_
- **F-D2** A list must be bounded and say so: "200 of 422" with a way to see
  the rest (paging, "show more", search). A list that silently cuts is a
  missing-data bug. _(frontend M5.)_
- **F-D3** The evidence for a view that reads per-item data includes its
  network total for a realistic data directory (at least five finished runs
  of 400+ candidates): request count and bytes, from the browser's network
  log. A page opens with under 1 MB of JSON; above that, the merge request
  says why. Until a seed command exists (see
  [the gaps](README.md#gaps-these-pages-name-but-do-not-fix)), the evidence
  says how the data directory was made.

## One console, one navigation

- **F-N1** Every page must be reachable from every other page through the
  same sidebar. A page that cannot be in the React sidebar yet is listed
  under System, and the classic shell must link back to Home. _(frontend M2,
  product M2.)_
- **F-N2** An unknown path must show a not-found page; it must not answer
  with a different page. Server-side 404 status for unknown paths is a
  backend task tracked with it.
- **F-N3** _(should)_ The sidebar highlights the page that is shown, and there
  is one ask box per page.

## Accessibility

Target: [WCAG 2.2](https://www.w3.org/TR/WCAG22/) level AA.

- **F-A1** Everything a mouse can reach must be reachable and operable from
  the keyboard, in a sensible order, with a visible focus. Content drawn on a
  canvas or WebGL must have a keyboard-reachable alternative (a list or table
  with the same items and actions). _(frontend M5: 222 of 422 papers only by
  mouse.)_
- **F-A2** Each page starts with a skip link to its main content.
- **F-A3** Controls must have names that make sense out of context: six
  "Open" links are six "Open <run title>". Icon buttons have an
  `aria-label`.
- **F-A4** A destructive confirmation names the action on its button
  ("Cancel run" / "Keep running"), never "Cancel" against "Close".
- **F-A5** User and model text in another language than the interface gets a
  `lang` attribute (`lang="zh"` on a Chinese title in the English console).
- **F-A6** Colour must not be the only carrier of meaning, legends must match
  what the colours mean on the map, and text contrast meets AA (4.5:1, 3:1 for
  large text and graphics).
- **F-A7** Motion respects `prefers-reduced-motion` (already true; keep it).
- **F-A8** A keyboard walk of the changed page is part of the evidence: the
  order of tab stops, and that each new action works with Enter or Space.

## Layout at 1440 and 390

- **F-L1** Every changed page must be checked at 1440×900 and 390×844 with
  screenshots. At 390 the page must not scroll sideways:
  `document.documentElement.scrollWidth <= innerWidth`, printed in the
  evidence. _(product M7: the run page was 965 px wide.)_
- **F-L2** Tables either stack into cards at narrow widths or give text
  columns a minimum width and show that they scroll. A cell must never be one
  character wide. _(frontend M3.)_
- **F-L3** Labels on canvases must stay inside the canvas and not overlap
  each other; when they would, drop the lower-priority label.

## Idle pages do no work

- **F-I1** A page that is not changing must not keep the CPU busy: no endless
  CSS animation or `requestAnimationFrame` loop while nothing moves, and
  canvases redraw on change. Decorative motion (twinkle, breathe) stops after
  10 s without input, and whenever the tab is hidden. _(frontend M6: Home at 15×
  the script time of other pages.)_
- **F-I2** A merge request that adds or changes animation or a canvas
  includes a 10-second idle measurement (script time and frames, from a
  DevTools trace) before and after. By hand until the browser probe in
  [the gaps](README.md#gaps-these-pages-name-but-do-not-fix) exists.
- **F-I3** _(should)_ Heavy pages (map renderers, the flow board) are split into
  their own chunk, so the sign-in form does not load them.

## Text says what is true

- **F-T1** Interface copy must describe what the product does now. A switch
  that says "review the plan before searching" waits for the person; a field
  that sets the language of the plan and criteria is named for that, not
  "Answer language", until runs end in an answer. Strings for
  features that are not built are not shown. _(product M4, B1.)_
- **F-T2** Counters must be defined: say in a comment next to the counter
  what it counts, and name it so a reader would guess the same. _(product M5:
  "Unjudged 0" counted failed batches.)_
- **F-T3** All interface text lives in `src/i18n.ts` in both languages.
  Plurals use `Intl.PluralRules`; numbers and dates use `Intl` formatters
  with the chosen interface language, not the browser's. _(frontend minor 1.)_
- **F-T4** Server messages shown to people are mapped from a stable code or
  known case to an i18n string; raw English server text is a fallback, not
  the normal path. _(frontend minor 2.)_
- **F-T5** _(should)_ One term per concept in each language, listed in `i18n.ts`
  comments when a term is new (judged / not screened / failed batch). Internal
  ids (model ids, run ids) are not shown as content.
- **F-T6** Text from papers and models is shown as a reader expects it:
  LaTeX accents and markup in titles are turned into characters
  ("Ciar\\'an" → "Ciarán"), and model text that the person reads (plan
  rationale, criteria) is in the run's language (see
  [B-M1](backend.md#model-output)). _(product M6.)_

## Code

- **F-C1** _(should)_ Server data is shaped by typed functions next to the view
  (`run.ts`-style reducers), not inside components, so the logic is testable
  without a DOM. Canvas code keeps layout and hit-testing in pure modules.
- **F-C2** No non-null assertions (`!`) on data from the server; handle the
  missing case.
- **F-C3** Every promise is awaited or handled; `void p` only where a failure
  is already handled inside `p`.
- **F-C4** _(should)_ Effects list their dependencies; one effect does one
  thing.
- **F-C5** _(should)_ State updates per streamed batch are proportional to the
  batch, not to everything seen so far, once a view handles 1,000+ items.

## Tests

- **F-X1** Each state in F-S1, and each interaction a person can do on a
  changed view (click, key, submit, cancel, reconnect), has a test. A bug fix
  starts with a test that fails on `main`.
- **F-X2** Tests use fake timers (`vi.useFakeTimers`) instead of waiting real
  seconds, and one test checks one behavior, so a failure names what broke.
- **F-X3** _(should)_ Changed files keep 80 % line coverage or better, printed
  in the evidence once coverage is part of `scripts/web-test` (see [the
  gaps](README.md#gaps-these-pages-name-but-do-not-fix)). Canvas and worker code
  is not exempt: move its logic into testable modules (F-C1).
- **F-X4** `scripts/web-test` passes: node tests, ESLint, Prettier, `tsc`,
  Vitest, build.

## Evidence for a frontend merge request

On top of [done.md](done.md):

- screenshots at 1440 and 390, in the interface language(s) whose text
  changed, from `isbe serve` on a seeded data directory;
- the error, empty and not-found states the change touches, shown;
- the 390 px `scrollWidth` line (F-L1) and the keyboard walk (F-A8);
- the network total for data-loading views (F-D3), and the idle measurement
  for animated ones (F-I2);
- the tail of `scripts/web-test`.
