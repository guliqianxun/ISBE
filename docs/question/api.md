# Question runs: API, event stream and queue

How the console and the CLI drive a question run: the `/api/question/*` routes,
the server-sent event stream, the queue and its single worker, and `isbe
question`. What the person sees in each state is [hsi-flow.md](hsi-flow.md); the
documents are the contracts in [`docs/contracts/question/`](../contracts/question/).
The code is `isbe/question/{queue,flow,service}.py` and `isbe/web/question_api.py`.

## Steps and the queue

A run goes through four steps. The worker takes one when it is ready, in the
order runs were asked, under the execution lock every CLI command takes, so a
step never overlaps a CLI run or a report task (report tasks go first).

| Step | Events | Then |
| --- | --- | --- |
| `plan` | `state` running, `stage` plan begin, a reachability check of essaies ([when an upstream fails](#when-an-upstream-fails)), `plan` (and an `error` when it is a fallback) | The plan review: the run waits until `start` or an edited plan (`/plan`), or not at all when it was asked with `review: false` |
| `search` | `stage` plan end, then the search stage ([search.md](search.md)) | Straight on to `screen` |
| `screen` | The screening stage ([screening.md](screening.md)) | Straight on to `finish` |
| `finish` | The expand stage: `stage` expand begin, `citations` after each batch of lookups, `stage` expand end; then the graph stage: `stage` graph begin, `graph`, `stage` graph end; `state` done, `done` | The run is over |

The expand stage gathers the citations between the candidates
(`isbe/question/citations.py`, #50): essaies' archive batch for every candidate
(about 15 s per 1,000, no OpenAlex call), then a full lookup through OpenAlex
for the `citations_full` most cited kept ones (300 by default, 0 for none),
four at a time, about 3 s each. An essaies without `full_references` gets a
non-fatal `error` asking to upgrade it. The full pass ends early, with one
non-fatal `error` saying how far it got, when OpenAlex is out of quota
(429/503), essaies cannot be reached, or three lookups in a row fail (504s
from a gateway, say); each failed paper has its own `error` too, and the
papers left are looked up when the stage runs again. Then, unless the run was
asked with `expand: false`, `isbe/question/expand.py` adds the papers one hop
away from the included ones (unscreened count as included) as candidates:
ranked by how many included papers they neighbour, then by citation count, at
most `expand_per_paper` (20) per included paper and `expand_total` (200) per
run. The expansion set, the new candidate set and the `expand` events are
written in one transaction. The screening then judges the picks alone (verdict
events, no screen stage events, titles only since lookups carry no abstract);
a rerun's carried overrides are applied to them once they are candidates; and
a last `citations` event counts the edges to them. A resumed stage does not
pick again. Pause and cancel act between two lookup batches and between two
screening batches.

The graph stage then builds the run's knowledge graph over the kept papers
(`isbe/question/graph.py`, #51, [graph.md](graph.md)): keywords and citations,
Louvain communities, PageRank, betweenness and bridge papers. It takes about
two seconds for 2,000 papers and is stored once per run; its `graph` event gives the
fingerprint and the node, edge and community counts, and the page fetches the
graph itself from `/graph`. A graph that cannot be built is a non-fatal `error`
(`stage_failed`, stage `graph`): the papers and citations stay, and `isbe
question-graph <run>` builds it later. `done` is `partial` when the run had any
non-fatal `error`, else `completed`.

### When an upstream fails

A run depends on essaies (and, through it, OpenAlex) and on the model. When one
of them is gone the run finds out in seconds and says why, instead of waiting
out every query's or paper's retries (docs/standards/backend.md B-U1–B-U6):

- **Before the plan.** The `plan` step first asks essaies for its capabilities
  (one request, 10 s at most, no retries). If essaies cannot be reached, or
  answers 5xx, the run fails at once with that reason, before any model time.
  Without `ESSAIES_URL` it fails the same way (`essaies_not_set`).
- **essaies goes during a stage.** A request that cannot connect (refused, no
  route, DNS, a connect timeout) is not retried; three failed attempts in a
  row, retries included (a request is retried twice, after 2 s and 4 s, on
  timeouts, 429 and 5xx), count as down too, and from then on the stage's
  client fails every request at once. 401, 403 and 405 are essaies (or a proxy
  before it) refusing the service: `essaies_down`, not the query's fault. The search stops asking queries (fatal when none was answered,
  else one non-fatal `error` and the run goes on with what it found); the
  citation lookups stop with one `error` for the papers left; a failed
  capabilities request is never remembered as "no capabilities", so batch
  lookups are not turned into one request per paper.
- **The model.** Question work gives each model call at most 300 s
  (`plan.QUESTION_TIMEOUT`, less when `ISBE_LLM_TIMEOUT_S` is lower), retries a
  failed call once after 5 s (`plan.QUESTION_WAITS`; other work waits 5 s then
  30 s), and does not retry a refused connection. A model that cannot be reached when the
  plan is written fails the run (`model_unreachable`), and so does one that
  refuses the call with 401 or 403 (`model_rejected`: a wrong key does not mend
  itself, so it is not retried either). A plan the model could
  not write for another reason, or no model at all, falls back to the question
  itself as the only query and is a non-fatal `error` (`plan_fallback`,
  `model_not_configured`), so such a run ends `partial`, never `completed`.
  Screening stops at the first call that cannot connect or is refused, or after
  three failed calls in a row, with one `error` for all the papers left unscreened.

The worst case per stage, with an upstream down, is then:

| Upstream state | Until the stage stops |
| --- | --- |
| nothing listens, no route, DNS | one connect attempt: under a second, or the connect timeout |
| essaies answers 5xx at once | three attempts and 6 s of backoff: about 6 s |
| essaies hangs | three attempts of `sources.TIMEOUT` (60 s) and 6 s: about 3 min |
| the model answers 5xx | three screening calls of two attempts and 5 s: about 15 s plus the calls |
| the model hangs | three calls of one attempt each (a timeout is not retried), up to 300 s each |
| the model answers 401/403 | the first refused call: the stage stops at once |

No event is written while a request hangs, so the last two show as a stage with
no progress until it stops; the reason comes with the `error` that ends it. A healthy run's
stage times are in the [expand stage](#steps-and-the-queue),
[search.md](search.md) and [screening.md](screening.md).

#### Problem codes

Every `error` event's `data.code` says what kind of problem it is, so a console
can explain it in the reader's language while `message` keeps the details
(B-O4). `data.detail`, when set, is the cause alone, without the advice the
message adds: the upstream's words for an essaies, OpenAlex or model failure (`HTTP 503:
…`, no model call or model id), why no model can be used (`ANTHROPIC_API_KEY is
not set …`), and for a stopped screening how many papers were left
(`…; 107 unscreened`). Events written before #90 have none.

| Code | Meaning | What to do |
| --- | --- | --- |
| `essaies_unreachable` | No connection to essaies | Start essaies, check `ESSAIES_URL`, run again |
| `essaies_down` | essaies answers 5xx or times out, request after request | Try later; check essaies' logs |
| `essaies_not_set` | No `ESSAIES_URL` | Set it in Settings |
| `openalex_quota` | OpenAlex (directly or through essaies) is out of quota or rate-limited | Wait for the daily reset; set `OPENALEX_API_KEY`; with essaies, use the arXiv engine |
| `query_failed` | One query failed (a 4xx about it); the others went on | Rewrite it in the plan |
| `search_failed` | Every query failed for reasons of its own | Change the plan |
| `citations_failed` | Some citation lookups failed | Run the stage again later |
| `essaies_old` | essaies lacks `full_references` | Upgrade essaies |
| `openalex_unreachable` | No connection to the OpenAlex API (the paper source, #121) | Check that this machine reaches `OPENALEX_API_URL`; set `HTTPS_PROXY` if it needs one |
| `openalex_down` | The OpenAlex API answers 5xx or times out, request after request | Try later |
| `openalex_rejected` | The OpenAlex API answered 401 or 403 | Fix or clear `OPENALEX_API_KEY` |
| `model_unreachable` | No connection to the model | Start it, check `ISBE_LLM_BASE_URL` |
| `model_not_configured` | No model is configured | Configure one |
| `model_rejected` | The model answered 401 or 403: a wrong key, or a model the key may not use | Fix the key or the model names |
| `model_failed` | Model calls failed (5xx, timeouts, no usable reply) | Try later; check the model server |
| `plan_fallback` | The model wrote no usable plan; the question was the only query | Check the model, run again |
| `stage_failed` | Anything else | Check the service log |

The fatal error of a failed run is also the run object's `failure`.

Pause and cancel act between two queries or two screening batches: the worker
reads what was asked before each one. A paused run gives the worker back; on
resume its stage continues where it stopped (finished queries are not asked
again, judged papers are not judged again, and the stage's `begin` event is not
repeated). A cancel keeps everything found so far and ends with `state`
cancelled and `done` cancelled. A run that is waiting (queued, or under plan
review) is paused or cancelled at once.

A step left running by a worker that died (a restart, a killed `isbe question`)
is noticed the next time the worker holds the execution lock, when nothing
else can be running: the run is marked **paused**, and resuming it continues
where it stopped. Nothing is lost and nothing is retried on its own.

The queue is `question_tasks` in the root database (one row per run; owner,
step, `ready_at`, a pending `control`); the run, its events and its results are
in the owner's workspace. Each account may have five runs in progress.

An ask (and a rerun) creates the run in the workspace first and queues it
second, so the worker never meets a task without its run; when the queue
refuses it (429 at the limit) or the `request_id` turns out to belong to a
concurrent ask, the new run is deleted again and nothing is left behind. The
request id check, the limit and the insert are one transaction on the root
database, with `UNIQUE (owner, request_id)` and `ON CONFLICT DO NOTHING` behind
it, so any number of concurrent asks with one id make one run: the first
answers 202, the others 200 with that run.

### What each action does in each state

| Run (queue task) | cancel | delete | pause / resume |
| --- | --- | --- | --- |
| `queued`, waiting for the plan step | cancelled at once | deleted | 409 / 409 |
| `running`, plan under review | cancelled at once | deleted | paused at once / 409 |
| `running`, a step in hand | at the next query or batch (`cancelling`) | 409: cancel first | at the next query or batch / 409 |
| `paused` | cancelled at once | deleted | 409 / resumed |
| `done`, `failed`, `cancelled` | 409 | deleted | 409 / 409 |
| `queued` or `running` with no queue row (left behind before #89, or by a delete or a refused ask whose workspace write failed) | cancelled at once | deleted | 409 / 409 |

The worker writes the queue before the workspace, so for a moment a task can
read finished or paused while the run's last events (`done`, the `paused`
status) are still being written; a delete then answers 409 "still stopping"
and works a moment later. That wait is bounded: a task unchanged for
`queue.SETTLE` (30 s) has no writer left (a worker killed between its two
writes, or a workspace write that failed), so a delete then goes through and a
cancel finishes the run (`cancelled`, `done`) instead of answering 409. The
check and the removal are one decision under the queue's write lock, so the
worker cannot end a step in between.

Delete takes the run off the queue first, then removes its rows from every
question table of the workspace in one transaction (`runs.delete`), with
the paper records no other run uses (search.md). A GET reads in one snapshot,
so a run deleted while it is read answers 404, not part of the run. If the
second step fails the run is still listed with no queue row, which the last
row of the table covers: it can be cancelled and deleted again.

## Routes

All routes are under `/api/question/runs`, take and return JSON, and answer
errors as `{"error": "..."}`. They pass the same Host, CSRF and session guards
as the other routes: in shared mode each account sees only its own workspace,
and an administrator may read another account's runs with `?owner=<id>` (GET
only). Writes need the `X-ISBE-CSRF` header. A GET answer of 1 KB or more is
gzip-compressed when the request's `Accept-Encoding` takes gzip (browsers do;
`gzip;q=0` refuses it); the CSV export and the event stream are not.

| Method and path | Body | Answer |
| --- | --- | --- |
| `GET /api/question/runs` | | `{"runs": [run…], "engines": ["openalex", "arxiv"], "default_engine": "openalex"}`: the runs newest first, what `engine` may be, and the one a run gets without it (the page preselects it) |
| `POST /api/question/runs` | `{question, lang?, year_from?, year_to?, review?, engine?, expand?, expand_per_paper?, expand_total?, citations_full?, request_id?}` | `202 {"run"}`. The expand stage's options: `expand: false` skips adding one hop of citations (the citations are still looked up); `expand_per_paper` (0–100, default 20) and `expand_total` (0–1000, default 200) cap the papers it adds; `citations_full` (0–1000, default 300) is how many kept papers get a full lookup through OpenAlex, 0 for none. A rerun keeps them; `200` with the earlier run when `request_id` was seen |
| `GET /api/question/runs/<id>` | | `{"run"}` |
| `GET /api/question/runs/<id>/events` | | Server-sent events, or a JSON page (see below) |
| `GET /api/question/runs/<id>/plan[?version=n]` | | `{"plan": question_plan@1}`, the latest version by default |
| `GET /api/question/runs/<id>/plans` | | `{"versions": [{version, parent_version, source, created_at}]}` |
| `POST /api/question/runs/<id>/plan` | `{version?, changes}` | Run edited plan, only under review: `{"plan", "run"}`; the search starts at once. 409 later: rerun instead |
| `POST /api/question/runs/<id>/start` | `{}` | Start search (end the review): `{"run"}` |
| `POST /api/question/runs/<id>/hold` | `{}` | Keep the plan under review while it is edited (the review already waits, so the console no longer calls it): `{"run"}` |
| `POST /api/question/runs/<id>/pause` | `{}` | `{"run"}`; `run.pausing` is true until the `state` paused event |
| `POST /api/question/runs/<id>/resume` | `{}` | `{"run"}` |
| `POST /api/question/runs/<id>/cancel` | `{}` | `{"run"}`; `run.cancelling` is true until the `state` cancelled event |
| `DELETE /api/question/runs/<id>` | | `204`: the run and all its rows are gone; 409 while a step is in hand (cancel first, see the table above) |
| `POST /api/question/runs/<id>/rerun` | `{version?, changes?}` | Edit plan and rerun: `202 {"run"}`, a new run from a copy of that plan version with `changes` applied; overrides in force carry over to the papers it finds again |
| `GET /api/question/runs/<id>/papers` | | `{"papers": [{paper, provenance, decision, decided_by, verdict, override}]}`: the candidates (during the search, the papers hit so far) |
| `GET /api/question/runs/<id>/field` | | `{"papers": [{key, decision, cited, queries}], "citations", "fetched_at"}`: what Home's star field needs, without titles or abstracts: the candidates in `/papers` order with their decision (null before screening), citation count and the ids of the queries that found them, and the edges and time of `/citations` |
| `GET /api/question/runs/<id>/citations` | | `{"citations": [[citing key, cited key]…], "fetched_at"}`: the citation edges between candidates, by the keys `/papers` gives, sorted; `fetched_at` is when the last lookup was saved, null (and no edges) before the expand stage ran. A `citations` event says when there are more |
| `GET /api/question/runs/<id>/overrides` | | `{"overrides": [screen_override@1…]}`, revokes included |
| `POST /api/question/runs/<id>/overrides` | `{key, decision, note?}` | `201 {"override"}` |
| `POST /api/question/runs/<id>/overrides/<override_id>/revoke` | `{note?}` | Revert to the model verdict: `{"override"}` |
| `GET /api/question/runs/<id>/sets` | | `{"sets": [{set_id, kind, made_by, size, created_at}]}` |
| `GET /api/question/runs/<id>/sets/<set_id>` | | The `paper_set@1` |
| `GET /api/question/runs/<id>/export.csv` | | The papers as CSV (UTF-8 with BOM), overrides applied; a download (`Content-Disposition: attachment`, never gzipped) |
| `GET /api/question/runs/<id>/export.json` | | `{"run_id", "question", "papers"}`, `papers` as in `/papers`; a download |
| `GET /api/question/runs/<id>/graph` | `?form=full` (default), `compact` or `map` | `{"graph": question_graph@1, "stale"}`: the run's knowledge graph as last built, with each paper node's record; `stale` is true when an override or a new citation lookup changed what it is built from since. `form=compact` is what the run page reads (#57): the same graph with `edges` replaced by `edge_kinds` and `edge_rows` (`[kind, source, target, weight]`, indexes into `edge_kinds` and `nodes`) and paper records without `abstract`; 321 KB instead of 1.25 MB for the 217-paper demo run (71 KB instead of 170 KB gzipped). `form=map` is what Home's map of a selected galaxy reads (#115), a shape of its own (`schema` `question_graph_map@1`, with the graph's `run_id` and `fingerprint`): `papers` as `[id, community, year, title, cited_by_count, pagerank]` rows for the papers not excluded, `communities` and `main_paths`, nothing else. 404 until the graph stage has run |
| `GET /api/question/runs/<id>/graph.json` | | The full `question_graph@1` as a download |
| `GET /api/question/runs/<id>/graph.graphml` | | The graph as GraphML (Gephi, Cytoscape, networkx): one directed graph whose `co_citation`, `coupling` and `keyword_cooccur` edges carry `symmetric` true (not `directed="false"`, which networkx refuses in a directed graph); nodes carry kind, label, community and its label, PageRank, betweenness, degree, bridge, and a paper's title, year, citations, DOI, URL, decision and origin. A download |
| `POST /api/question/runs/<id>/graph` | `{}` | Builds the graph of an ended run again (the run page's Rebuild, after overrides or new citations made it stale): `{"graph": summary, "cached"}`, `cached` true when nothing had changed. 400 with any option, 409 while the run is going, 404 when the run never built a graph (`isbe question-graph <run>` builds the first one). Adds no event: other open pages keep their stale banner until they read the graph again |

`plan` changes are the editable plan fields of [planning.md](planning.md)
(`concepts`, `queries`, `criteria`, years, …); edited concepts recompose the
queries. `engine` is `openalex` or `arxiv`, essaies' local arXiv
index; the default is `compose.DEFAULT`, `openalex` (it was `arxiv` while
essaies could not reach the OpenAlex API, 2026-10-23 to 2026-10-26); the page
reads both from `GET /api/question/runs`, and `run.options.engine` is the one
a run searches. Actions answer 409 when the run is not in a state that allows them
(hsi-flow.md §3).

### The run object

```json
{
  "run_id": "run-20261023-1a2b3c4d",
  "question": "Which deep learning methods work best for precipitation nowcasting?",
  "lang": "en",
  "status": "running",
  "stage": "screen",
  "degraded": false,
  "problems": 0,
  "failure": null,
  "plan_version": 1,
  "review": {"on": true, "active": false, "ends_at": null},
  "options": {"year_from": null, "year_to": null, "engine": "arxiv", "expand": true, "citations_full": 300, "expand_per_paper": 20, "expand_total": 200},
  "pausing": false,
  "cancelling": false,
  "counts": {"hits": 2210, "candidates": 1630, "included": 412, "excluded": 300, "unscreened": 0, "overridden": 1},
  "last_seq": 4120,
  "created_at": "2026-10-23T09:30:00.000Z",
  "updated_at": "2026-10-23T09:31:12.000Z"
}
```

`status` is the run status of hsi-flow.md §2 (`queued`, `running`, `paused`,
`cancelled`, `failed`, `done`); `stage` is the earliest stage begun and not
ended while the run is running or paused, else null (`plan` during the plan
review). `degraded` is set by any non-fatal `error`; `problems` counts every
`error` event. `failure` is null unless the run failed; then it is its fatal
error, `{"stage": "plan", "code": "essaies_unreachable", "message": "essaies
could not be reached: [Errno 113] No route to host. Check that essaies is
running…"}` ([problem codes](#problem-codes)). `problem` is null unless the run
is `done` with a non-fatal coded `error`; then it is the first one, in the same
shape, for a list to say what went wrong without reading the events.
`review.active` is true while the plan is shown and the search is not due;
`review.ends_at` is null: the review waits for a person (it is a time only
under a test's countdown, `flow.Services.countdown`).
`options.rerun_of` names the run a rerun was made from. `counts` follow the
effective decisions (an override wins over the model). `last_seq` is the
newest event's `seq`. The page itself is built from events; the run object is
for lists, headers and the first paint.

## Health

`GET /api/health` needs no sign-in and shows nothing private:

```json
{
  "ok": false,
  "worker": {"alive": true},
  "upstreams": {
    "papers": {"source": "essaies", "configured": true, "state": "down",
               "checked_at": "2026-10-24T17:40:02.114Z", "error": "ConnectError: [Errno 113] No route to host"},
    "model": {"configured": true, "state": "ok", "checked_at": "2026-10-24T17:38:51.020Z", "error": null}
  }
}
```

`error` is only the kind of failure (`HTTP 503`, `ReadTimeout`, `ConnectError:
[Errno 111] Connection refused`), never what essaies or a proxy answered: that
may name internal hosts, and it goes only to the log and to the run's events,
which need sign-in. `worker.alive` is whether the queue's worker thread runs. Each upstream's
`state` is how its latest request went (`ok`, `down`, or `unknown` before any
request or when it is not configured), from the requests runs and reports make.
`papers` is the paper source the settings choose (`source`: `openalex`, the
OpenAlex API, which needs nothing set up, or `essaies`, configured once
`ESSAIES_URL` is set; #121 renamed the key from `essaies`). It is asked again
(essaies: one capabilities request; OpenAlex: its free API root; 10 s at most)
when it was last heard from more than 60 s ago; the model is never called for this. `ok` is true
when the worker is alive and every upstream is configured and not down. It
answers 200 either way. Home reads it every minute while its tab is in view, and
says so before anyone asks when the paper source or the model is down or not configured,
or the worker is not running.

## Logging

The service and the CLI log to stderr at `ISBE_LOG_LEVEL` (`DEBUG`, `INFO`,
`WARNING`, `ERROR`; `INFO` by default), set in the environment at start-up.
Every question step logs its start, its end with how long it took and the run's
counts, and its failure (WARNING); every failed essaies request logs once at
WARNING with the route and the error class:

```
2026-10-24 17:40:02,118 WARNING isbe.question.sources: essaies /api/openalex/capabilities failed (ConnectError): essaies could not be reached: [Errno 113] No route to host
2026-10-24 17:40:02,121 WARNING isbe.question.flow: question run run-20261024-1a2b3c4d failed at plan after 3.0 s: essaies could not be reached: [Errno 113] No route to host
```

## The event stream

`GET /api/question/runs/<id>/events` with `Accept: text/event-stream` answers
server-sent events, one per `question_event@1`:

```
id: 42
event: hit
data: {"schema":"question_event@1","run_id":"run-…","seq":42,"at":"…","type":"hit","data":{…}}
```

The `id` is the event's `seq`, the `event` name its `type`, and `data` the whole
event document on one line. The stream starts after `Last-Event-ID` (which the
browser sends when it reconnects) or `?after=<seq>`, from the first event when
neither is given, so a page is rebuilt by replaying from the start and resumes
without duplicates. It sends a `: keep-alive` comment after 15 s of silence and
ends after five minutes, when the browser reconnects by itself (`retry:
3000`). It ends sooner once it has sent `done` and every event after it: a run
that has come to rest costs no polling. A client that comes back with nothing
newer than `done` (the browser's own reconnect, with `Last-Event-ID`) gets
`204 No Content`, which tells `EventSource` to stop. Events written after
`done` (overrides) reach a client that asks again with `?after=<last seq>`: the
new stream sends them and ends. The run page folds in the overrides it saves
itself at once; one made in another tab shows after a reload. Each stream holds one database connection for
its life. It needs only the session cookie, like any GET.

A problem in the run is an event named `error`, the same name `EventSource`
gives its own connection errors (both reach `onerror`). The front end's
`subscribe` tells them apart: a server event carries `data`, a connection error
does not. The front end's `subscribe` (frontend/src/api/sse.ts)
reads it as is, listening for the event types of the contract.

Without `text/event-stream`, the same path answers a JSON page:
`{"events": [...], "last_seq": n}` after `?after=` (at most `?limit=`, 5,000).

## The CLI

```bash
isbe question "Which neural operators solve PDEs best?" --lang en
```

Plans, searches and screens the question in the local workspace, printing the
stages as they happen and the counts at the end. It holds the execution lock,
uses the model and `ESSAIES_URL` from Settings (`--essaies-url` overrides it),
skips the plan review (`--review` waits it out) and takes `--from`, `--to`,
`--engine` and the expand stage's options (`--no-expand`, `--expand-per-paper`,
`--expand-total`, `--citations-full`). The run is an ordinary run: the console shows it while it runs and
can pause or cancel it; Ctrl-C pauses it, and the console resumes it.

```bash
isbe question-citations run-20261023-8f593591 --full 0
```

Looks up the citations of a run that finished before the expand stage existed
(or whose full pass ran out of OpenAlex quota), without adding events: `--full`
is how many kept papers to look up through OpenAlex (300 by default; 0 asks the
archive only). It prints the problems and the edge count.

```bash
isbe question-graph run-20261023-8f593591 --top 10
```

Builds the knowledge graph ([graph.md](graph.md)) of a run that has ended, when it ended before the
graph stage existed, or after overrides or new citations made it stale; an
unchanged graph is not built again. Adds no events. It prints the node and edge
counts by kind, the `--top` largest communities with their keywords and the
most central papers by PageRank, then one JSON line with the counts and the
fingerprint.

## Testing

`tests/py/question/test_flow.py` runs whole runs offline on a sample's recorded
plan reply and essaies searches (`tests/fixtures/question/`) with a scripted
screening model (`tests/py/question_flow.py`): every event against the
contract, the plan review, pause and resume in the middle of the search and of
the screening, cancel, a dead worker's run, a rerun, failures, the CLI and the
queue's worker thread. `tests/py/web/test_web_question.py` covers the routes,
workspace isolation and the event stream, over a real connection too.
