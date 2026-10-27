# Question runs: screening

The third stage of a question run is the filter. The search is tuned for
recall, so a run brings 1,500–2,500 candidates (see [search.md](search.md)), and
the model judges every one against the plan's include and exclude criteria
(`question_plan@1` `criteria`, see [planning.md](planning.md)) from its title and
abstract. Each judgement is a `screen_verdict@1`; a person can override any of
them at any time (`screen_override@1`), and the override wins. The code is
`isbe/question/screen.py`; the prompts are `prompts/{zh,en}/question_screen.md`,
chosen by the language of the question (the reasons come back in that language).

## No pre-filter

Every candidate goes to the model. A keyword pre-filter was tried on the
recorded candidates of the five samples (#47): keeping only papers that mention
at least two of the plan's concepts still kept 67% of the candidates and lost
about one must-find paper in ten, because the searches already require those
words. The model reads what a word count cannot.

## How a screening runs

1. **Order.** Papers found by the plan's narrow queries (priority 1) go first,
   by their best rank in those queries, then the broad tier's, then papers no
   search found (expansion). The likeliest papers are decided early, so the
   star map fills with included papers first. Nothing is skipped.
2. **Batches.** Papers are added to a batch while it stays within 20 papers
   (`BATCH_PAPERS`) and about 10,000 tokens of paper text (`BATCH_TOKENS`,
   estimated at three characters a token and capped at a quarter of the model's
   context), so a batch of long abstracts is smaller. Each paper is its title,
   year, venue and the first 1,500 characters of its abstract (`(none)` when it
   has none). The system prompt and the criteria come first and are the same in
   every batch, so a server that caches prompt prefixes (Strata does) reads them
   once. Calls run one at a time, as one `question_screen` job of the `runs`
   ledger; every call is in `llm_calls`.
3. **Checking the reply.** One JSON object, `{"verdicts": [{"n", "decision",
   "score", "reason"}]}` (a bare list, a Markdown fence or `<think>` text are
   tolerated). An entry counts when its number is in the batch and not seen
   before and its decision is include or exclude; a score outside 0–1 becomes
   null, a reason is cut at 300 characters.
4. **Bad batches.** Papers a reply leaves out are asked again as a batch of
   their own (`c1.b3.r`). A reply with no usable entry (broken JSON, cut off at
   its token limit, refused) is split in two and each half asked (`c1.b3.1`,
   `c1.b3.2`), down to one paper; a single paper that still gets no verdict is
   left **unscreened**.
5. **Failed calls.** A call that fails outright (the model unreachable or
   erroring after its one retry, 5 s later; a timeout is not retried) leaves its
   batch unscreened. After three such calls in a row, or the first that cannot
   connect at all or is refused (HTTP 401/403), the rest of
   the papers are left unscreened without calling. Each failed call is a
   non-fatal `error` event with its `batch_id`, and the papers left are one more
   `error` for all of them; either makes the run end `partial`. Unscreened papers count as kept, so a
   failure never costs recall; the next screening asks about them again.
6. **No model.** With no model configured, every paper is unscreened, the
   reason says why, and one non-fatal `error` (`model_not_configured`) says so
   for each screening: the stage's, and the expansion's of the papers it adds.
7. **The screened set.** A `screen` operation over the candidates
   (`set_op@1`, params `{"plan_version", "screen"}`) gives `s1.screened`: the
   candidates whose effective decision is not exclude.
8. **Events.** `stage screen begin`, one `verdict` per paper as its batch is
   decided, an `error` per failed batch, `stage screen end`.

A run screened again (an edited plan, or the same one) numbers its screenings
(`screen_verdicts.screen`, batch ids `c2.b1`, …, sets `s1.screened-2`). A paper
already judged under the same prompt, criteria and model (`criteria_sha`) keeps
its verdict without a call; a change to any of them judges everything again.

## Overrides

`screen.override(conn, run_id, key, decision, actor, note)` records a person's
include or exclude on a paper of the run (screened or not yet);
`screen.revoke(conn, run_id, override_id, actor)` undoes one with a new record
(`decision: null`, `revokes`). Nothing is deleted. A paper's effective decision
(`screen.decisions`) is its latest override that is not revoked, else its latest
verdict. Each record is an `override` event. The graph and exports read
`decisions`, so an override counts at once; the screened set stays as it was
made, and `screen.replay` rebuilds it with the decisions as they stood then.

## Measured on Strata

Strata (`qwen3.8-flash-next-iq3_s`, thinking off with
`ISBE_LLM_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}`) on
2026-10-23, over the candidates `replay.screening_run` builds from every recorded
essaies 0.5 search of each sample (`plan.json` and Strata's plan; the two-tier
searches of #47 are not recorded yet) with `plan.json`'s criteria. The replies
are in `tests/fixtures/question/<sample>/model.screen.json` and `test_screen`
replays them. The green-space sample is left out for now.

| Sample | Candidates | Included | Must-find kept | Calls | Tokens in / out | Time |
| --- | --- | --- | --- | --- | --- | --- |
| `radar-nowcasting-dl` | 959 | 464 (48%) | 8 / 8 | 49 | 247k / 44k | 295 s |
| `ml-medium-range-weather` | 948 | 326 (34%) | 10 / 10 | 48 | 241k / 44k | 293 s |
| `diffusion-image-generation` | 1,038 | 672 (65%) | 8 / 9 | 52 | 257k / 51k | 333 s |
| `neural-pde-solvers` | 953 | 921 (97%) | 7 / 7 | 48 | 202k / 46k | 283 s |

- **Recall.** 33 of the 34 must-find papers among the candidates are kept. The
  lost one is DDPM: its OpenAlex record carries the abstract of a DNA
  methylation paper, and the model excludes what that abstract describes.
- **Precision.** A random 20 included and 10 excluded papers per sample from the
  first run (seed 49), checked by reading title and abstract (by Claude, not yet
  by a person): about 94% of the included papers belong (radar 20/20, weather
  16/20, diffusion 20/20, PDE 19/20; the weather misses are subseasonal or
  generic forecasting papers), and about 92% of the excluded ones are right
  outside (the misses are borderline: a diffusion super-resolution method, a
  partial integro-differential solver). The PDE sample's searches are already
  precise, so nearly everything is included.
- **Time and tokens.** About 0.3 s and 250 input / 46 output tokens a paper,
  so ~5 minutes per 1,000 candidates and ~10–13 minutes for a two-tier run of
  2,000–2,500. Strata's prefix cache means the repeated prompt and criteria cost
  little.
- **Prompt.** The first run lost two must-find papers; saying that the field's
  datasets, benchmarks, software and baselines belong brought back Pysteps
  (radar) and raised the included share by 1–3 points. Strata's verdicts vary a
  little from run to run (±2% included between the two runs).
- **Order.** Here the priorities come from two different plans, so whether the
  narrow tier's papers come first is not measured yet: must-find papers were
  decided earlier than in set order for the diffusion sample (the first three in
  the first 10 papers rather than 202) and later for the others. It needs the
  two-tier searches of #47.
