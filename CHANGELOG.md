# Changelog

## 0.2.0 (unreleased)

The first release with question research: ask a research question and ISBE
plans a search, screens the papers it finds, follows their citations and lays
the result out as a star map, a knowledge graph and a written survey.

### Question research

- **Ask a question** from the home page or `isbe question "<question>"`. A
  model writes a search plan (concepts, narrow and broad query tiers,
  inclusion and exclusion criteria) that you can edit before the search
  starts.
- **Search** runs the plan against the OpenAlex API directly, with nothing
  to host: cursor paging, year filters and deduplication across queries. Add
  a free OpenAlex API key in Settings; without one, every install on an IP
  shares a small daily budget (about 100 searches, and a run uses 30–80). A
  self-hosted essaies (OpenAlex proxy plus a local arXiv index) can be used
  instead with `ISBE_PAPER_SOURCE` and `ESSAIES_URL`.
- **Screening** reads each candidate's title and abstract against the
  criteria. You can include, exclude or revert any paper by hand, and export
  the set as CSV.
- **Citations and expansion** fetch the citation edges between the kept
  papers and add one hop of cited and citing papers, screened like the rest.
- **Knowledge graph**: communities, PageRank and topic lineage over the kept
  papers on a year axis, with citation main paths and filters. Export as JSON,
  GraphML or a compact edge list, and rebuild from the run page.
- **Star map**: every paper as a star, grouped by community, live while a run
  is in progress.
- **Survey report**: `isbe question-survey <run>` writes a cited Markdown
  survey from the most relevant papers, with arXiv full text where available.
- Runs can be cancelled, retried with the same options, rerun with an edited
  plan, and deleted. Upstream failures (model, essaies, OpenAlex) end a run
  with a named cause instead of hanging.

### Console

- A new React console: home with reports and a first-run guide, the question
  run page, Settings, and Help, under one navigation. Chinese and English UI.
- Settings can be imported and exported as JSON (`isbe settings import` /
  `export`); secrets are left out of exports unless `--secrets` is given.

### Operations

- The Docker image builds from a clean checkout. The PyPI mirror build
  arguments (`PYPI_MIRROR`, new `PYPI_FILES_MIRROR`) now apply to every
  download in the build.
- Question data is stored once per paper and shared between runs; GET
  responses are gzipped.
- Python 3.14 is required.

### Known limitations

- Without an OpenAlex API key, question research runs out of the shared
  keyless budget after one or two runs a day.
- One worker serves every account; runs queue behind each other.
- There is no per-account or global cap on model usage yet. Do not expose a
  shared instance to untrusted users.

## Initial public snapshot (2026-10-22, 0.2.0.dev0)

Paper topics, weekly digests and reports, research snapshots, the classic web
console and shared accounts.
