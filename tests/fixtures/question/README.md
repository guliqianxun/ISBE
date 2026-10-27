# Recorded data for the sample questions

One directory per question in
[`docs/contracts/question/samples.json`](../../../docs/contracts/question/samples.json),
replayed by `tests/py/replay.py` (see [Testing](../../../docs/testing.md)).

| File | What it is |
| --- | --- |
| `plan.json` | A `question_plan@1` for the question: five English queries over the keyword layers, written by hand as a model would. |
| `essaies.json` | `cassette@1`: every essaies response a run of the plan asks for. One `/api/openalex/search` (limit 100) per query of `plan.json` and of the plan ISBE makes from `model.strata.json`, one for the question as asked, and one `/api/openalex/lookup` per must-find DOI or arXiv id. |
| `essaies.arxiv.json` | `cassette@1`: the searches of essaies' arXiv index (`/api/search`) that the plan made from `model.strata.json` for that index asks (`replay.py --arxiv`), text squashed to single spaces. Four samples; green space has none (its papers are not in the index). |
| `model.json` | `model_replies@1`: the model's replies. Until the planning prompt exists, one hand-written reply matched by the question text: the plan without the fields ISBE fills in. Recorded replies are keyed by `prompt_sha`. |
| `model.screen.json` | `model_replies@1`: Strata's screening replies for the candidates `replay.screening_run` builds (every recorded search of `plan.json` and Strata's plan), with `plan.json`'s criteria; replayed by `test_screen`. Recorded by `test_screen_live`. |
| `screen.queries.json` | The queries `model.screen.json` was recorded on (`plan.json`'s and Strata's plan before its queries were composed per engine, #47); `replay.screening_plan` uses them so the recorded verdicts keep matching. |
| `essaies.screen.json` | `cassette@1`: the first page of each of those queries' searches (essaies 0.5, with abstracts), kept from the cassette they were recorded in; `replay.screening_run` builds the screening candidates from it. Re-record both with `model.screen.json`. |
| `essaies.citations.json` | `cassette@1`: the expand stage's citation lookups for the candidates of `tests/py/question_flow.py`'s run (`replay.py --citations`): the archive batches and the full `scope=all` lookups of its 20 most cited, with every paper kept (`question_flow.KEEP_ALL`), text squashed. Recorded 2026-10-26 for every sample; `test_flow` pins the must-find papers the expansion adds. |

## `cassette@1`

```json
{
  "schema": "cassette@1",
  "service": "essaies",
  "recorded_on": "2026-10-22",
  "interactions": [
    {
      "request": {"method": "GET", "path": "/api/openalex/search", "query": [["limit", "100"], ["q", "..."]]},
      "response": {"status": 200, "json": {"items": []}}
    }
  ]
}
```

A request matches on method, path and the sorted query; the host is never
recorded. An interaction added later by `replay.py --missing` carries its own
`recorded_on`. Bodies are essaies' as returned, minus its `path` field (where the
archive keeps a PDF). Failed requests are kept as they happened.

## `model_replies@1`

```json
{
  "schema": "model_replies@1",
  "replies": [
    {"purpose": "plan", "contains": "<question>", "reply": "<reply text>"},
    {"purpose": "plan", "prompt_sha": "<sha256 of [system, user]>", "reply": "<reply text>"}
  ]
}
```

## What the recordings show (2026-10-22)

| Sample | Must-find papers in the plan's searches | The question as asked |
| --- | --- | --- |
| `radar-nowcasting-dl` | 8 of 10 | 0 (Chinese) |
| `ml-medium-range-weather` | 9 of 10 | HTTP 502 |
| `diffusion-image-generation` | 7 of 10 | 0 (Chinese) |
| `neural-pde-solvers` | 5 of 8 | HTTP 502 |
| `green-space-mental-health` | 5 of 6 | HTTP 502 |

`essaies.json` also holds what ISBE's own client asked essaies 0.5 on
2026-10-23 (each such interaction has its `recorded_on`): its capabilities,
every query of both plans with `cursor=*`, `include=abstract,keywords` and the
plan's years, and one batch lookup of the must-find ids. `model.strata.json` holds Strata's concept replies (2026-10-23). Their composed
OpenAlex searches are not recorded yet (the OpenAlex quota ran out); the
searches of the earlier, model-written Strata plans were dropped. Their
searches of essaies' arXiv index are, in `essaies.arxiv.json`.

The 502s are real: essaies passes an English question ending in `?` to the
OpenAlex API, which answers 400. A run that falls back to the question as its
only query needs to strip that punctuation first.
