# Question runs: the search plan

The first stage of a question run turns the research question into a search
plan (`question_plan@1`, see [the contracts](../contracts/question/)): an
English restatement, concepts with their terms, subject / method / outcome
terms, five keyword layers, prioritized English queries composed from the
concepts, a year range and the include / exclude criteria
screening uses. The person sees it on the plan card (see
[hsi-flow.md](hsi-flow.md)) and can edit it before or after the run starts.
The code is `isbe/question/plan.py`; the prompts are
`prompts/{zh,en}/question_plan.md`, chosen by the language of the question.

## How a plan is made

1. **One model call** with the question verbatim and any years the person
   set. It runs as a `question_plan` job in the `runs` ledger, so the call is
   logged in `llm_calls` like every other model call.
2. **Lenient reading.** The JSON object is taken as is, from a Markdown fence,
   after `<think>` text, or between the first `{` and last `}`. A reply cut off
   at its token limit is closed after its last complete value.
3. **Checks.** Required fields and types; 1–8 English queries (only the
   fallback query may be in another language), each with one of the five
   layers and a priority; years in order; terms trimmed, deduplicated and
   capped. Punctuation that breaks a full-text search (`?`, `!`, `:`, `;` and
   their full-width forms) is removed from queries (see "How queries are
   written" below for how they are made). Every query should share
   a word (beyond generic ones such as "model" or "learning") with the plan's
   restatement, terms or keyword layers.
4. **One repair call** when any check fails, listing the problems and quoting
   the reply. Queries that still share none after it are dropped
   unless that would leave none.
5. **Fallback.** No model configured, a refusal, a failed call or a plan still
   broken after the repair: the plan is the question itself (punctuation
   removed) as the only query, `layer: "original"`, and `rationale` says why.
   The run goes on; nothing is lost but the plan's breadth.

Years the person set always win over the model's. The plan's `llm` field sums
the job's calls (model, tokens, time, call count).

## How queries are written

The model names concepts, not queries: each concept has a role (`subject`,
`method`, `context`, `landmark`, `outcome`) and 1–6 interchangeable terms papers
use for it (synonyms, spellings, an abbreviation and its full form), as plain
words; subjects get their shortest common names, and qualifiers such as
`urban` or `radar` are a `context` concept rather than part of every term.
`isbe/question/compose.py` writes the queries from them, at most sixteen, in two
tiers:

| Tier | Priority | Query | Results |
| --- | --- | --- | --- |
| narrow | 1 | all the subject concepts together | 100 |
| narrow | 1 | all the subjects and one method or context concept, for each | 100 |
| narrow | 1 | up to three landmarks with the first subject concept | 100 |
| broad | 2 | each subject concept alone (when there are several) | 200 |
| broad | 2 | the landmarks on their own, three at a time | 200 |
| broad | 2 | each method with each subject concept separately (when there are several) | 200 |
| broad | 2 | all the subjects and one outcome concept, for each | 200 |

A query that requires every subject concept misses each paper that words one
of them differently: on the sample questions that was most of what composed
plans missed, so the broad tier pairs concepts one at a time and asks for more
results (`Engine.broad_limit`, stored as the query's `limit`). The search stage
goes on to the broad tier unless the narrow one already found 1,500 papers;
screening sorts out the noise the broad tier brings.

How a concept is written depends on the search engine (`compose.Engine`):

- **OpenAlex** (`OPENALEX`, through essaies) takes upper-case `OR` inside
  parentheses and nested groups. A concept becomes an OR group of its terms; a
  term of several words becomes a group of its words, `(physics informed
  neural network)`, which matches with plurals and other endings folded. A
  quoted phrase would not: OpenAlex takes it literally.
- **essaies' arXiv index** (`ARXIV`, `/api/search`: SQLite FTS5 with Porter
  stemming) reads the same OR groups, but two concepts are joined with an
  explicit `AND` (FTS5 rejects a group next to a word without one), and a
  word with a hyphen or other symbol is quoted on its own:
  `("physics-informed" neural network) AND PDE`.
- **An engine without boolean search** (`PLAIN`) gets each concept's first
  term as plain words.

`plan.generate(..., engine=)` picks the engine and the plan records it
(`engine`, OpenAlex when absent); an edit recomposes for the same engine, and
the search stage needs a source of that engine (`Essaies(engine="arxiv")`).
A new engine is a new `Engine`. Composed queries are checked like any others;
fewer than four gets the repair call, which asks for more concepts. The term
fields and keyword layers are filled from the concepts when the model gives
none. A person who edits the concepts gets the queries composed again; one who
edits the queries keeps them as written.

A plan without concepts (a reply in the older shape that writes queries
itself) is still read. Its queries are tidied: outside parentheses only the
first quoted phrase of two or three words stays quoted, alternatives of several
words inside an OR group are quoted, `or` becomes `OR`, unbalanced parentheses
are dropped, and a bare abbreviation whose initials match a phrase of the
plan's own terms gets it as an alternative (`PINN` → `(PINN OR
"physics-informed neural networks")`).

## Versions

Plans are stored in `question_plans`, one row per version of a run. The
model's (or fallback) plan is version 1. `plan.edit` saves a person's changes
to any version as the next version, with `source: "human"` and
`parent_version` set to the version edited; earlier versions never change, and
a rerun uses the version it is given. Edits are checked strictly: unknown
fields, non-editable fields (`question`, `lang`, ...) and anything over a limit
are refused with the full list of problems instead of being trimmed.

## Choosing the model

Planning uses whatever model ISBE is configured with (Settings page or
environment), through its smart tier, with an output budget of 32,768 tokens.
The plan itself is about 700 tokens; the rest is room for a model that
reasons before answering. A reply cut off at the budget, or one without
include criteria, gets the repair call.

### A local model (Strata)

Strata, like any OpenAI-compatible service, is set up as:

```sh
ISBE_LLM_PROVIDER=openai
ISBE_LLM_BASE_URL=http://<strata-host>:<port>/v1
OPENAI_API_KEY=<the service's key>
ISBE_LLM_MODEL_FAST=<model id from /v1/models>
ISBE_LLM_MODEL_SMART=<model id from /v1/models>
```

Strata's Qwen model reasons by default. Its reasoning comes back apart from the
answer (`reasoning_content`), so it never reaches the plan, but it counts
against the output budget: 2,000–4,000 tokens per plan in the recorded runs.
`ISBE_LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}`
turns it off where speed matters more than the plan's quality. Strata serves
one request at a time, so a plan waits behind any other call in its queue.
When the prompt and the output budget do not fit its context, `core/llm.py`
lowers the budget once from the numbers in Strata's rejection.

### The Claude API

```sh
ISBE_LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=<key>
# Optional; the defaults are claude-haiku-5-5 (fast) and claude-sonnet-5-5 (smart).
ISBE_LLM_MODEL_SMART=claude-sonnet-5-5
```

### Recorded runs

`tests/fixtures/question/<sample>/model.strata.json` holds Strata's replies for
the five sample questions (2026-10-23, `qwen3.8-flash-next-iq3_s`, reasoning
on), in the `model_replies@1` format keyed by the hash of the system and user
text. The offline tests replay them and check that each plan passes without a
repair.

| Sample | Queries | Tokens in + out | Time |
| --- | --- | --- | --- |
| `radar-nowcasting-dl` | 7 | 1,418 + 10,002 | 58 s |
| `ml-medium-range-weather` | 6 | 1,433 + 5,140 | 31 s |
| `diffusion-image-generation` | 7 | 1,406 + 5,476 | 32 s |
| `neural-pde-solvers` | 7 | 1,433 + 9,024 | 51 s |
| `green-space-mental-health` | 7 | 1,426 + 4,751 | 29 s |

What these plans find is in [search.md](search.md#recorded-runs).

A changed prompt changes the hash, so record again after editing a prompt:

```sh
ISBE_LIVE_LLM_URL=http://<strata-host>:<port>/v1 ISBE_LIVE_LLM_KEY=<key> \
ISBE_LIVE_LLM_MODEL=<model id> ISBE_LIVE_RECORD_FILE=model.strata.json \
uv run pytest -m live tests/py/question/test_plan_live.py -s
```

For the Claude API, use `ISBE_LIVE_LLM_PROVIDER=anthropic` and the key instead
of the URL. The test plans every sample question, prints the queries, tokens
and time, and keeps each reply in the named file (default `model.json`).
`ISBE_LIVE_RECORD_DIR` writes the files somewhere else for a look first.
