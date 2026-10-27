# Question runs: survey reports

A survey report answers a run's question from the papers its screening included
(#106). It reuses the topic pipeline's full-text reading, fact extraction and
report storage; only the input (a run's included set) and the writing prompt
are new. The code is `isbe/question/survey.py`; the prompts are
`prompts/{zh,en}/survey.md`, chosen by the language of the question.

```bash
isbe --data "$DATA" question-survey run-20261026-1a2b3c4d            # the 20 most relevant included papers
isbe --data "$DATA" question-survey run-20261026-1a2b3c4d --max-papers 30
isbe --data "$DATA" question-survey run-20261026-1a2b3c4d --no-fulltext  # stored text and abstracts only
```

It needs the model settings (`ISBE_LLM_*`), and arxiv.org for full text. The
run must have ended (done, failed or cancelled); a run that is queued, running
or paused is refused. A run that did not finish, or that left candidates
unscreened (a failed screening batch), is surveyed from what it has, and the
report's scope line says so.

## What it does

1. **Papers.** Every candidate whose effective decision is include, a person's
   override winning over the model's verdict (`screen.decisions`, the run page's
   count). They are ranked by relevance: papers a person included first, then
   the screening score, then the citation count. The first `--max-papers`
   (default 20) are surveyed; the others are listed at the end of the report
   without being discussed. (Ranking by citations alone picked the field's
   general classics, such as textbooks on data assimilation, over the papers on
   the question.)
2. **Items.** Each surveyed paper gets an `items` row (`source='question'`)
   unless it has one: its arXiv id (`arxiv:<id>`) when it has one, so a paper a
   topic already collected brings its stored full text and card, else its
   preferred key (`openalex:W…`, `doi:…`). The rows have no `topic_items` or
   `triage` row, so no topic page lists them.
3. **Full text.** arXiv HTML through `enrich.stage.fetch_html`, spaced 3 s
   apart as the topic stage does; method figures are not fetched. A paper
   without an arXiv id, or whose HTML arXiv does not have, is read from its
   abstract only (`abstract_fallback` in the stats names the latter). Two
   failures in a row that are not an HTTP answer (arXiv unreachable or timing
   out) skip the remaining fetches (`arxiv_down`), so an outage costs two
   timeouts rather than one per paper. `ISBE_FULLTEXT_EXTRACTOR=off` or
   `--no-fulltext` fetches nothing new; `llm` and `metrail` both read arXiv HTML
   here (question papers have no essaies PDF path for metrail), and any other
   value is refused as in `isbe enrich`.
4. **Facts.** `enrich.cards.refresh` on each paper with full text: the card
   prompt, and a fact is kept only when its quote is in the text and its numbers
   and URLs are in its quote. A card that fails because of its paper (a bad
   reply, a 400 for its prompt) is named in `failed` and the report is still
   written. A card whose model call fails (network, auth, rate limit, 5xx, after
   the client's retries) stops the survey at once with "the model call failed":
   the survey call would fail the same way, and no report is written.
5. **Writing.** One Smart call. The input numbers the papers `[P1]`…, with
   metadata, reading depth (full text or abstract only), abstract, verified
   facts and the tables of the full text (`digest.run.tables`). The model
   writes six sections (TL;DR, lines of research, methods, data and evaluation,
   findings, limitations and gaps) and cites `[P<n>]`; abstract-only details
   are to be marked "(per the abstract)".
6. **Checks and rendering.** A level-1 heading from the model is dropped and a
   citation of a number not in the input becomes `[P?]` (`bad_citations`).
   `[P1, P3]` is split into `[P1][P3]`, a `### [` heading of the model's is
   escaped (the shelf counts those as papers), and a citation of an
   abstract-only paper is marked `[P3]†`, whether or not the model wrote
   "(per the abstract)". The title, the scope line (included, surveyed, full
   text, abstract only, and the run's status or unscreened candidates when they
   matter), and one `### [P<n>]` block per surveyed paper with its reading depth
   and verified facts (each with its quote) are written by the program.

## Where it goes

`reports/<run id>/<YYYY-MM-DD>.md`, a `reports` row of kind `survey` (migration
019) with the run id as its topic. The shelf (`/reports`) shows it with the
question as its label, the TL;DR and the number of surveyed papers. A second
survey of the run on the same day replaces the first; a file on that path that
another report owns, or that no report owns, is refused before any fetch or
model call. Home and the overview list it under its title.

The report outlives the run: deleting the question run (`runs.delete`) leaves
the report file, its `reports` row and the `items` rows (with their full text
and cards, which topics may share) in place, like any published report.

The `items` rows are the one case where something other than collection writes
`items` (`collect/items.py`). Their date is only the publication year, so when
a topic later collects the same arXiv paper, collection replaces the whole row,
date included; otherwise the paper would sit outside every topic's window. A
row a topic collected first is never touched. `items.lang` is `en` for every
question paper: the records carry no language, and the field only labels the
item.

## Limits

- Journal papers have no full text here yet: OpenAlex runs are mostly
  abstract-only. PDF full text (open-access links through metrail) is a
  follow-up.
- The survey is written by the model from these materials; only the citation
  numbers and the quoted facts are checked by the program, not the claims of the
  prose.
