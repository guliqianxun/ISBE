# Backend standard

Rules for the Python service in `isbe/`, its migrations, prompts and
scripts. Each rule has an id (`B-…`) that reviews cite. Every rule is a
**must** unless it is marked _(should)_. Breaking a must rule is a blocking
or major finding, depending on its effect
([review.md](review.md#findings)); breaking a should rule is minor. Names of
routes, events, settings and numbers given as "for example" are for the fix
issue to settle, not decisions. The conventions in
[architecture.md](../architecture.md#conventions) (compatibility contract,
`HttpError`, `db.immediate`, `core.http.Http`, `atomic_write`, no secrets in
logs, modules under ~400 lines) still hold and are not repeated here.

## Upstreams: fail fast

essaies, OpenAlex (through essaies), Strata and mail servers go down. A stage
that depends on one must find out quickly and stop.

- **B-U1** A stage must stop early when an upstream is clearly gone: on the
  first "could not be reached" (connection refused, no route, DNS), or after
  three upstream failures in a row (transport errors, timeouts, 5xx; a 4xx
  about one query or paper does not count). It must not wait out every
  query's or paper's retries one by one. _(backend M1, product B2: 2½–4
  minutes per failed run.)_
- **B-U2** A run checks the upstreams it needs before it spends model
  time: a cheap reachability check of essaies before the plan is written,
  failing the run at once with the reason.
- **B-U3** A failed request must not be cached as an answer. A capability,
  feature list or lookup that failed is retried on the next use, not
  remembered as empty. _(backend M1: capabilities cached as `frozenset()`
  turned batch lookups into one request per paper.)_
- **B-U4** Every outbound call must have a timeout, and a stage must have a
  total time budget it states in its docs. Interactive work (question runs)
  must not inherit a 15-minute model timeout. _(backend M5.)_
- **B-U5** Retries must be bounded and stated in code next to the call:
  count, backoff, and which errors retry (5xx, timeouts) and which do not
  (4xx, quota).
- **B-U6** The reason travels: the upstream's error ("essaies could not be
  reached: [Errno 113] No route to host") must reach the run's events and the
  log, with secrets and internal hosts left to the environment, so the
  console can show it (F-S5).

## Honest outcomes

- **B-O1** A degraded path must say so. A fallback plan, a stage skipped or
  cut short because something failed, or a batch left unscreened emits a
  non-fatal `error` (or its successor event) and counts in `degraded` and
  `problems`. A run is `completed` only when every stage did its full job. A
  stage that is not built yet (the graph today) is not a problem and the
  interface does not present it as a step that ran; since it shares the
  `skipped` state with a stage turned off (expansion off), its event says
  why it was skipped. _(backend M6: a
  fallback plan reported `completed`.)_
- **B-O2** A problem is attributed to the stage that had it ("citation
  lookups failed", not "searches had problems"). _(product M5.)_
- **B-O3** Long stages must report progress at least every 30 s (counts done
  and left), so a client can tell slow from stuck. _(product M5: 8½ minutes
  without an event.)_
- **B-O4** Error messages must say what failed and what to do. A message the
  console translates carries a stable code next to the text.

## Run lifecycle

Question runs, queue tasks and report jobs are state machines. Their states,
transitions and end states are documented, and every state has a way out.

- **B-L1** Creating work must be atomic with accepting it. A run exists only
  if the queue accepted it: create both in one transaction, or roll the run
  back when the queue refuses (429, conflict). _(backend B1: zombie runs after
  a 429.)_
- **B-L2** Idempotency is enforced by the database. A request key is a unique
  constraint, and a conflict on it returns the first request's answer (200
  replay), never a 500. Check-then-insert in two transactions is not allowed;
  use `INSERT … ON CONFLICT` or a conditional update. _(backend B1: four
  concurrent asks answered 202, 500, 500, 500.)_
- **B-L3** Every state must have its exits: a run can be cancelled while
  active, and deleted (with its rows) in any state. A state that no action
  can leave is a bug. _(backend B1, M3.)_
- **B-L4** Every run must come to rest with a reason: a terminal state (`done`,
  `failed`, `cancelled`), or a documented state that waits for a person and
  offers its actions (`paused` after a worker restart, as
  [api.md](../question/api.md) describes). No run stays `running` or `queued`
  with nothing left to move it, including after a crash or restart of the
  worker. When state is split across databases (workspace and root), write in
  the order that leaves a recoverable state if the second write fails, and say
  which in a comment. _(backend m3.)_
- **B-L5** A stream on a run that has come to rest must stop costing a
  database connection every poll: it ends, or it waits for a change
  (overrides) without polling. Today [api.md](../question/api.md) keeps the
  stream open after `done` so overrides keep coming; changing that is a
  contract change (B-A1, B-A4) that says how a page learns of later
  overrides. _(backend M4: 30 streams on finished runs cost 14–36 % of a
  core.)_
- **B-L6** A change to a lifecycle updates its table in the docs (states ×
  actions → result) and its tests cover each cell it touches.

## Data volume

- **B-D1** Store each record once and refer to it by key from hits, events
  and sets. A new table or event type that copies a record already stored
  says why in the merge request. _(backend M3: each paper stored four times,
  18 MB per run.)_
- **B-D2** Every collection the API returns is bounded: paging (for example
  `after` and `limit`) or a documented maximum. Views get projections with the
  fields they draw (F-D1).
- **B-D3** _(should)_ JSON answers over 8 kB are compressed when the client
  accepts it.
- **B-D4** Data that grows per run has a retention story: a delete route or
  command, and the size per run written in the docs. A merge request that
  adds stored data shows the database size before and after a realistic run
  (`ls -l isbe.db`, or `sqlite3` page counts).
- **B-D5** _(should)_ Listing endpoints do not scan per item; read summaries
  from stored columns or one query. Time the endpoint at 10 and 100 runs
  when it changes (by hand until a seed command exists, see
  [the gaps](README.md#gaps-these-pages-name-but-do-not-fix)). _(backend
  m2.)_

## Resource use and fairness

- **B-R1** Polling loops end: on the terminal state, the client leaving, or a
  cap. They reuse a connection instead of opening one per poll. _(backend
  M4: 30 streams on a finished run cost 14–36 % of a core.)_
- **B-R2** One owner's slow or stuck work must not stall everyone's: a step
  holds the worker for a bounded time, and shared mode schedules owners
  fairly. _(backend M5.)_
- **B-R3** Pause and cancel take effect within a time the stage documents
  (for example the next batch, at most 30 s), including during a model
  call.

## Logging and health

- **B-G1** Logging is configured once at start-up (level from an environment
  setting such as `ISBE_LOG_LEVEL`, default `INFO`, to stderr). Modules use
  `logging.getLogger(__name__)`. _(backend M2: a failed run left one log line.)_
- **B-G2** Each pipeline step logs its start, end and failure at `INFO` (or
  `WARNING` for failure) with the run id, stage, duration and counts. Each
  upstream failure logs once at `WARNING` with the service name and the
  error class, never with a key, token or provider body.
- **B-G3** The service exposes a health endpoint (for example
  `/api/health`) that says whether the worker is alive and whether each
  configured upstream answered recently, and the console shows "reachable",
  not only "configured". _(product B2.)_
- **B-G4** Logs and events are evidence: a merge request that changes a
  stage shows the log lines of a real run.

## API contract

The rules in architecture.md's compatibility contract hold. In addition:

- **B-A1** Event names must not collide with transport names. In server-sent
  events, `error`, `open` and `message` are EventSource's own; application
  events use other names (for example `problem`; the fix issue picks it).
  Renaming an existing event is a contract change: keep the old name readable
  for a release and update [api.md](../question/api.md), the JSON Schemas and
  both consoles.
- **B-A2** Every free-text input has a size limit, and the error for a limit
  names the right thing ("Question is too large", not "Settings request is
  too large"). _(backend m1, m5.)_
- **B-A3** Status codes say what happened: 404 for a missing resource, 409
  for a state conflict, 429 for a limit, 503 for an upstream that is down.
  Each new route documents its errors.
- **B-A4** A route or event change lands with its docs page and a contract
  test in the same merge request.

## Model output

- **B-M1** Text the model writes for a person to read (plan rationale,
  concepts, criteria, screening reasons) is in the run's language (`lang`),
  and prompts say the output language explicitly. `lang` defaults to the
  interface language the question was asked in (the API's default is `zh`
  today), and the ask form keeps a visible choice for it (F-T1; decided by
  Zhiheng, 2026-10-24). _(product M6: a
  Chinese question in the 中文 console got an English plan.)_
- **B-M2** A prompt change keeps the cache-key rule in architecture.md and is
  measured as [process.md](../process.md#what-counts-as-evidence) asks for
  numbers: two runs on Strata, before and after.
- **B-M3** _(should)_ Titles and abstracts are stored as received; display
  clean-up (LaTeX accents) happens in one shared function used by every
  view and export (F-T6).

## Code

- **B-C1** _(should)_ New modules stay under ~400 lines; a change that pushes a
  module over splits it or says why.
- **B-C2** _(should)_ Type hints on every new function; new pydantic input
  models use `extra="forbid"`.
- **B-C3** _(should)_ No module-level mutable state beyond the documented ones;
  caches say how they are invalidated.

## Tests

- **B-X1** Each failure path a change adds or touches has a test: upstream
  unreachable, timeout, 5xx, quota, bad model output (the fake model's
  faults in [testing.md](../testing.md#the-fake-model)). A bug fix starts
  with a test that fails on `main`.
- **B-X2** Concurrency: a route with an idempotency key or a check-then-act
  has a test that sends concurrent requests (threads against the test app)
  and checks the stored rows afterwards. _(backend B1.)_
- **B-X3** Lifecycle: tests cover each state × action cell the change
  touches (B-L6), including the listing after a refused request.
- **B-X4** Cross-database and crash paths: when a step writes to two
  databases, a test fails the second write and checks the run can still
  finish or be cancelled.
- **B-X5** _(should)_ Tests do not sleep real seconds; inject clocks and
  timeouts. _(backend m7: twelve SMTP tests at 5 s each.)_
- **B-X6** `scripts/py-test` passes on a clean checkout, without a frontend
  build. _(backend B2, #70.)_

## Evidence for a backend merge request

On top of [done.md](done.md):

- the real command or `curl` and its output;
- the log lines of the run (B-G4) and the events that show the stage;
- a `sqlite3` excerpt or API answer showing what was stored;
- for upstream-facing code, the same run with the upstream down (the
  stand-ins in the backend and product reviews), with how long it took to
  fail;
- for stored data, the database size before and after (B-D4); for listings
  and streams, timings;
- the tail of `scripts/py-test`.
