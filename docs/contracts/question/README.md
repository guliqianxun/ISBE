# Question research contracts

The data shapes shared by the question → paper set → knowledge graph feature:
the backend writes them, the API and event stream carry them, the React pages
read them, and the tests check them. Each is a JSON Schema (draft 2020-12) in
this directory, with a valid instance in [`examples/`](examples/).
`tests/py/contracts/test_question_contracts.py` checks every schema, every
example and the sample questions.

| Schema | File | What it is |
| --- | --- | --- |
| `question_plan@1` | [question_plan.schema.json](question_plan.schema.json) | The search plan: the engine its queries are written for, reformulation, concepts (the queries are composed from them), subject/method/outcome terms, layered keywords, prioritized queries, year range, include/exclude criteria. Each edit is a new version. |
| `paper_set@1` | [paper_set.schema.json](paper_set.schema.json) | A set of papers with how each one was found. |
| `set_op@1` | [set_op.schema.json](set_op.schema.json) | One operation on sets (union, intersection, difference, dedup, screen, limit), recorded so every derived set can be replayed. |
| `screen_verdict@1` | [screen_verdict.schema.json](screen_verdict.schema.json) | The model's include / exclude / unscreened decision on one paper, with reason, score, model and batch. |
| `screen_override@1` | [screen_override.schema.json](screen_override.schema.json) | A person's decision on one paper, or the revert of an earlier one. |
| `question_event@1` | [question_event.schema.json](question_event.schema.json) | One entry in a run's event stream. |
| `question_graph@1` | [question_graph.schema.json](question_graph.schema.json) | The keyword + citation graph with communities and node metrics. |
| `question_metrics@1` | [question_metrics.schema.json](question_metrics.schema.json) | What a run achieved and cost (see [Metrics](#metrics)). |
| `question_samples@1` | [question_samples.schema.json](question_samples.schema.json) | The fixed sample questions; the list itself is [samples.json](samples.json). |

[common.schema.json](common.schema.json) holds the shared pieces: run and
local ids, timestamps, the identity key and the paper record. Schemas refer to
each other by `$id` (`urn:isbe:question:<name>`); load them all into one
registry to resolve the references.

## Rules every consumer relies on

- **Versioning.** Every document carries `"schema": "<name>@<major>"`. A
  backward-compatible change (a new optional field) keeps the major version; a
  change that can break a reader bumps it and the old schema stays until no
  stored data uses it.
- **Unknown fields are rejected.** Add the field to the schema first.
- **Identity keys.** A paper is identified by `openalex:W…`, `doi:…` (lower
  case) or `arxiv:…` (no version suffix). A paper's `key` is the first it has
  in that order; `keys` lists all of them. Two records are the same paper when
  any keys match; `dedup` folds them and keeps the preferred key. A preprint
  and its journal version are different OpenAlex works and stay separate
  papers.
- **Decisions.** A paper's effective decision is its latest unrevoked override,
  otherwise its latest verdict. `unscreened` (a batch the model failed to
  judge) stays in the set like `include`, so screening never drops recall.
  Nothing is deleted: excluded papers remain in the run.
- **Events.** `seq` starts at 1 and increases by one within a run; a client
  reconnects by asking for events after the last `seq` it saw, and a page can
  be rebuilt from the events alone. `state` carries the run status (queued,
  running, paused, cancelled, failed, done), `stage` marks a stage beginning,
  ending or being skipped, and the last event is `done`. `hit` and `expand`
  set `new: false` when the paper was already in the run. A non-fatal `error`
  leaves the run going and ends it `partial`; a fatal one ends it `failed`.
- **Graph.** Paper nodes are keyed by identity key, keyword nodes by `kw:` and
  the normalized keyword. `cites` points from citing to cited and `has_keyword`
  from paper to keyword; the other kinds are undirected, endpoints in sorted
  order. Clients cache a graph by its `fingerprint`.
- **Untrusted text.** Titles, abstracts, keywords and model reasons come from
  outside; render them as text, never as HTML.

## Sample questions

[samples.json](samples.json) holds five fixed questions, used for acceptance
runs and recorded test data. Each lists 5–10 papers it must find; every key was
looked up in the essaies OpenAlex mirror on the `checked_on` date. A
must-find paper counts as found when any of its keys matches a paper in the
run.

| Id | Language | Breadth | What it tests |
| --- | --- | --- | --- |
| `radar-nowcasting-dl` | zh | narrow | The existing nowcasting topic; Chinese terms must become English queries |
| `ml-medium-range-weather` | en | broad | Preprint and journal versions of the same work |
| `diffusion-image-generation` | zh | broad | A crowded field inside the cs.CV archive, where direct search finds only minor recent papers |
| `neural-pde-solvers` | en | narrow | Papers mostly outside the archive |
| `green-space-mental-health` | en | broad | A field outside computer science with no arXiv papers |

Asked verbatim as one search of 100 results, the questions find 0/10 (5/10
with English terms), 6/10, 0/10, 1/8 and 0/6 of their must-find papers.

## Metrics

Recorded per run in `question_metrics@1`.

| Metric | Definition |
| --- | --- |
| Candidate recall | `recall.candidates_found / recall.must_find`: must-find papers among the deduplicated candidates (expansion included), before screening. Measures retrieval. |
| Key-paper recall | `recall.included_found / recall.must_find`: must-find papers whose effective decision is include or unscreened at the end of the run. The headline number. |
| Screening accuracy | `screen_audit.agreed / screen_audit.sampled`: draw 30 papers uniformly at random from all screened papers (random seed recorded), a person judges each against the question, and the share where the model's verdict agrees. Unscreened papers are not drawn. |
| Paper counts | `counts.hits` (raw results), `counts.candidates` (after deduplication), `counts.included`, `counts.excluded`, `counts.overridden`, `counts.expanded`. |
| Stage time | `stage_ms` per stage and in total, wall-clock from the stage's `begin` to its `end` event; pauses count. |
| Tokens | `tokens.input` / `tokens.output` over every model call in the run, with `by_stage` usage from the model call log. |
