# Question runs: search and the paper set

The second stage of a question run runs the plan's queries (see
[planning.md](planning.md)) against essaies' OpenAlex mirror and joins what they
find into the run's candidate set. Every step is a paper set
(`paper_set@1`) or an operation between sets (`set_op@1`), stored so the
candidate set can be rebuilt and checked (see [the contracts](../contracts/question/)).

| Module | Does |
| --- | --- |
| `isbe/question/sources.py` | essaies search and lookup; identity keys; a work → the paper record |
| `isbe/question/sets.py` | paper sets; union, intersection, difference, dedup; the stored operations and their replay |
| `isbe/question/search.py` | the stage: queries in priority order, hits, query sets, the candidate set |
| `isbe/question/runs.py` | question runs and their event stream |

## How a search runs

1. **Priority order.** Queries run one priority level at a time, lowest
   number first. Once the run has 1,500 distinct papers (`search.ENOUGH`), the
   remaining levels are skipped; a level that brings fewer leads to the next.
2. **One query.** `GET /api/openalex/search?q=…&limit=100` (or the query's own
   `limit`: a composed plan's broad queries ask for 200). The raw reply is
   stored in `question_queries` with its status and time; each work becomes a
   paper record and a row of `question_hits`, and the query's hits become its
   query set (`s1.q1`, kind `query`). The plan's years go with the search;
   papers outside them that still come back are left out (the raw reply keeps
   them), papers without a year stay in.
3. **Failures.** A timeout, a dropped connection, 429 or 5xx is retried twice
   (after 2 s, then 4 s); requests are spaced at least 0.2 s apart. A 502 in
   which essaies reports that OpenAlex rejected the query is not retried. A
   query that still fails is a non-fatal `error` event and the others go on.
   Only when every query fails does the search fail: a fatal `error`, the run
   `failed`, `done`. When essaies is gone (a request that cannot connect, which
   is not retried, or three failed queries in a row) the queries left are not
   asked: the search fails at once with the reason when no query was
   answered, else it ends with one non-fatal `error` and the run goes on
   ([api.md](api.md#when-an-upstream-fails)).
4. **The candidate set.** `union` of the query sets (`s1.union`), then `dedup`
   (`s1.candidates`), both recorded as `set_op@1`.
5. **Events.** `stage search begin`, one `hit` per paper of each query (with
   `new: false` when the search already had a paper under that key), one
   `dedup` per fold, `stage search end`.

Each paper record is stored once, in `question_papers` under a digest of its
content (writer: `question/papers.py`, migration 016); hits, set members and
`hit`/`expand` events hold its `paper_ref` and are read back with the record in
place, so the API answers as before. Raw replies are kept zlib-compressed
(`question_queries.response_z`; `search.response` reads one). A run of about
200 candidates takes about 2 MB; three runs of 554–964 candidates took
17–18 MB (49 MB before 016). Records are shared by runs, and
`question_paper_uses` (migration 017) lists the runs that use each: deleting a
run removes the records no other run uses, at a cost that grows with the run,
not the workspace (`papers.forget`, `papers.prune`). Readers of a run read its
rows and their records in one snapshot (`db.snapshot`), so a run deleted
meanwhile is not found rather than half read.

Migration 016 rewrites every stored record, about 30 s per GB of database.
`isbe serve` migrates every data directory (the root and each
`workspaces/<id>/`) before it listens and prints what it migrates, a line every
30 s while it runs and how long it took; any other opener waits for a migration
in progress (up to 5 minutes) instead of failing, and `serve` says when it waits
for a file another process is writing to. Each such locked workspace can add up
to 5 minutes before `serve` listens. The root database must
upgrade or `serve` stops with its path in the error; a member workspace that
cannot (corrupt, newer than the binary, locked, a link) is logged with its path
and skipped, and only its owner's requests fail.
After 016 the file is compacted (`VACUUM`); when that fails (another writer, a
full disk) a warning is logged and each `serve` start tries again while a
quarter of the file is free.

A run searched again (an edited plan, or the same one) numbers its searches:
the second one's rows carry `search = 2` and its sets are `s2.*`.

## Identity and deduplication

A paper's keys are its OpenAlex work (`openalex:W…`), its DOI (lower case,
`doi:10.…`) and its arXiv id without version (`arxiv:2202.11214`), preferred
in that order; an arXiv DOI (`10.48550/arxiv.<id>`) gives the arXiv id too.

- `union` joins members with the same preferred key into one, keeping every
  provenance entry.
- `dedup` folds members that share any key, first seen wins: the first record
  is kept, its gaps are filled from the later ones, provenance and keys are
  joined, and the preferred key becomes the member's key. A record that shares a
  DOI with one member and an arXiv id with another makes them one.
- Two different OpenAlex works stay two papers (a preprint and its journal
  version) even when both claim the same DOI or arXiv id; the first seen keeps
  that key, so no two members of a set share a key.
- `intersection` keeps the first input's members that share a key with every
  other input; `difference` keeps those sharing none with any.

Search results always carry their OpenAlex work, so within one search union
already joins repeats; dedup folds archive-only records (citation lookups
return arXiv ids only) into the OpenAlex works they belong to.

`sets.Store.replay` rebuilds every derived set from the stored query sets by
running the recorded operations again; the tests check that it matches what
was stored.

## essaies

The client asks `/api/openalex/capabilities` once per run (essaies API 0.5,
zore/essaies#1) and uses what essaies offers; an essaies without that endpoint
is used as before.

| Capability | With it | Without it |
| --- | --- | --- |
| `search_cursor` | `cursor=*`, then each reply's `meta.next_cursor` | one page of at most 100 |
| `search_filters` | the plan's years go with the search (`year_from`, `year_to`) | ISBE drops hits outside the years |
| `optional_fields` | `include=abstract,keywords`: the paper record has both | abstract null, keywords empty |
| `batch_lookup` | `POST /api/openalex/batch`, 100 ids a request | `/api/openalex/lookup` per id |

Searches go through essaies to the OpenAlex API: a query ending in `?` comes
back as a 502 reporting OpenAlex's 400 (the planner strips that punctuation),
and when essaies cannot reach OpenAlex every search is a 502 after about 20 s,
retried twice, then a failed query.

## Recorded runs

Every query of each plan, 100 results a query, essaies 0.5 on 2026-10-23
(`tests/fixtures/question/<sample>/essaies.json`, checked by
`tests/py/question/test_search.py`). Asking for 200 a query (two pages) about
doubled the candidates and found one more must-find paper in two of the ten
runs, so a query asks for 100.

| Sample | Hand-written plan | Strata, before the #47 fix | Strata, after (recorded) | Strata, after (second run) |
| --- | --- | --- | --- | --- |
| `radar-nowcasting-dl` | 8/10 (422) | 5/10 (523) | 6/10 (608) | 6/10 |
| `ml-medium-range-weather` | 9/10 (452) | 0/10 (680) | 8/10 (545) | 9/10 |
| `diffusion-image-generation` | 7/10 (488) | 8/10 (605) | 5/10 (625) | 5/10 |
| `neural-pde-solvers` | 5/8 (492) | 2/8 (585) | 4/8 (579) | 5/8 |
| `green-space-mental-health` | 5/6 (495) | 3/6 (651) | 3/6 (618) | 2/6 |
| All | 34/44 | 18/44 | 26/44 | 27/44 |

Strata's plans are now composed from concepts in two tiers (see
[planning.md](planning.md#how-queries-are-written)). Their searches were recorded
on 2026-10-26, once essaies could reach OpenAlex again:

| Sample | Strata, composed plans (recorded 2026-10-26) |
| --- | --- |
| `radar-nowcasting-dl` | 8/10 (1164) |
| `ml-medium-range-weather` | 8/10 (1706) |
| `diffusion-image-generation` | 4/10 (910) |
| `neural-pde-solvers` | 5/8 (873) |
| `green-space-mental-health` | 1/6 (1077) |
| All | 26/44 |

That is no better than before the two tiers (26/44) and below the hand-written
plans (34/44), with 2.4 times their candidates (5,730 against 2,349). Checked
against the recorded titles and abstracts, the composed plans' queries could
match 37–38 of the 44 papers, which suggests the gap is in what OpenAlex ranks
into the results a query asks for (100, or 200 for the broad queries), not in
the query words; green-space, whose papers are journal articles with varied
wording, loses the most.

### One hop of citations

The expand stage (#50) adds the neighbours of the included papers. Recorded on
2026-10-26 (`essaies.citations.json`, `replay.py --citations`), with every search
result of the hand-written plan kept as a seed and the full lookups of the 20
most cited, the expansion adds 9 must-find papers the search missed: 34/44 to
43/44 among the candidates (`test_flow.EXPANSION_FINDS`).

| Sample | Search | Full lookups with no work | Must-find papers the expansion adds |
| --- | --- | --- | --- |
| `radar-nowcasting-dl` | 8/10 | 9 of 20 | MetNet |
| `ml-medium-range-weather` | 9/10 | 4 of 20 | A Foundation Model for the Earth System |
| `diffusion-image-generation` | 7/10 | 8 of 20 | Diffusion probabilistic models (2015); DDIM; DiT |
| `neural-pde-solvers` | 5/8 | 4 of 20 | Physics-informed machine learning; Neural Operator; gradient pathologies in PINNs |
| `green-space-mental-health` | 5/6 | 0 of 20 | View Through a Window May Influence Recovery from Surgery |

Each run picks the cap of 200. It is an upper bound: a model's screening keeps
fewer seeds, and a pick is screened again on its title, year and venue (lookups
have no abstracts), where these runs keep every pick. A full lookup with no work
is an arXiv id the full (`scope=all`) lookup does not resolve; those seeds add
only what the archive batches give.

### On essaies' arXiv index

While OpenAlex was out of quota (2026-10-23), the same Strata concept replies
were composed for essaies' local arXiv index (`compose.ARXIV`,
`Essaies(engine="arxiv")`)
and searched there on 2026-10-23 (`essaies.arxiv.json`, checked by
`test_search.py`). The index is a SQLite FTS5 table over the archive essaies
collects, about 250,000 papers, two thirds of them `cs.CV`, so it holds only
part of each sample's must-find list:

| Sample | Must-find papers in the index | Hand-written plan, as written | Strata, composed for the index |
| --- | --- | --- | --- |
| `radar-nowcasting-dl` | 6/10 | 4 (82) | 5 (535) |
| `ml-medium-range-weather` | 10/10 | 2 (60), 3 of 5 queries rejected | 9 (1,158) |
| `diffusion-image-generation` | 6/10 | 2 (200), 3 of 5 queries rejected | 4 (961) |
| `neural-pde-solvers` | 0/8 | 0 (194), 1 of 5 queries rejected | 0 (477) |
| `green-space-mental-health` | 0/6 (journal papers) | skipped | skipped |
| All four | 22/38 | 8 | 18 |

The hand-written plans' OpenAlex syntax is mostly rejected by FTS5 (a group
next to a word with no `AND`, a hyphenated word), which is why queries are
composed per engine. The composed plans find 18 of the 22 papers the index
holds; the four they miss are PredRNN (a video-prediction paper that never
says nowcasting), WeatherBench 2, DiT and ControlNet (both ranked past the
results of their queries among thousands of diffusion papers). None of the
neural PDE solver papers and none of the green-space journal papers are in
the index, so those samples need OpenAlex.

Must-find papers among the candidates when every query runs, with the
candidate count in brackets. Strata's first plans put most terms in quotes
(`"machine learning" "numerical weather prediction"`), which OpenAlex searches
as exact phrases; since the fix (see
[planning.md](planning.md#how-queries-are-written)) they use plain words and OR
groups. The recorded replies are the first run after the fix; the second run
of the same prompt was measured live and not kept. Diffusion is the one sample
that lost: the first plans searched the exact titles of two landmark papers.

## Tests

Offline (`tests/py/question/test_sources.py`, `test_sets.py`,
`test_search.py`): identity keys, author strings, retries, spacing, paging,
every set operation and its replay, failure isolation, and the search of every
sample question on the recorded essaies replies (OpenAlex, and the arXiv index
for the four samples it covers), checked against `paper_set@1`,
`set_op@1` and `question_event@1`, with the must-find papers the recordings
hold.

Live, against a real essaies:

```sh
ISBE_LIVE_ESSAIES_URL=http://<essaies> uv run pytest -m live tests/py/question/test_search_live.py -s
```

It searches each sample question with its hand-written plan and with Strata's
plan and prints the papers before and after deduplication, the queries run,
failed and skipped, and the must-find papers found.
