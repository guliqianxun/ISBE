# The knowledge graph of a question run

The last stage of a question run builds a graph of the kept papers: their
keywords and citations, the communities they form and how central each paper
is (#51). The document is `question_graph@1`
([contract](../contracts/question/question_graph.schema.json)); the route and
events are in [api.md](api.md), and the star map that draws it is
[star-map.md](star-map.md) (#55). The code is `isbe/question/graph.py`.

## What is in it

**Papers.** The kept candidates: included, and never screened (recall first,
as in screening.md). Excluded papers are left out; an override changes that
on the next build. Each paper node carries its record, its effective decision
and its origin (`seed`: found by a query; `expansion`: added by the citation
expansion).

**Keywords.** The OpenAlex keywords of the kept papers, folded to lower case
with spaces collapsed, that at least two papers carry; the 150 most common.
Papers added by the expansion have none (essaies' lookups carry no keywords),
so a community made mostly of them can have no keyword label.

**Edges.**

| Kind | Between | Weight |
| --- | --- | --- |
| `cites` | citing → cited paper | 1 |
| `has_keyword` | paper → keyword | 1 |
| `co_citation` | two papers cited by the same works | works citing both |
| `coupling` | two papers with references in common | shared references |
| `keyword_cooccur` | two keywords on the same papers | papers carrying both (at least 2) |

`co_citation` and `coupling` count every work the citation lookups name, not
only candidates: a run whose papers seldom cite each other still has
structure through the literature around it. A work that more than 100 kept
papers cite (or are cited by) joins no pairs: it says little about which two
belong together, and its pairs grow with the square of that count. Each
derived kind keeps about its 5,000 heaviest edges. When the cut falls among
edges of one weight, that weight is kept whole while the kind stays within
10,000 edges, else none of it is kept, so which pairs stay never depends on
how their keys sort. A kind can therefore come out much smaller than the cap:
the 2,000-paper test keeps 845 of its co-citations (the weight-1 class is too
large), while the 217-paper real run hits no cap.

## Metrics

| Metric | How |
| --- | --- |
| `community` | Louvain modularity communities on the undirected graph of the paper–paper and paper–keyword edges, each edge weighing `cites` 1, `co_citation` and `coupling` 0.5 per shared work (at most 4), `has_keyword` 0.3. Numbered by paper count, largest first. A node with no such edge has none (null) |
| `pagerank` | PageRank (damping 0.85) over the papers: `cites` edges citing → cited, plus the `co_citation` and `coupling` edges both ways at their analysis weight. Sums to 1 over the papers; keywords have none |
| `betweenness` | Normalized betweenness over the paper–paper edges, unweighted (Brandes); above 256 papers from 256 source papers spread evenly over the sorted keys, scaled up |
| `bridge` | A paper with a paper–paper edge into another community whose betweenness is in the top 10 % of the papers |
| `degree` | Distinct neighbours over every edge kind |

Each community lists up to five `top_keywords`, ranked by enrichment against
the whole set (`n · log(share in the community / share overall)`, carried by
at least two of its papers, one in a community of fewer than four, and only
keywords at least two papers of the set carry); its `label` is the first.

Every step visits nodes in a fixed order, so the same inputs give the same
graph.

## Reading it: topic lineage and citation main paths

The whole graph drawn at once is a ball: on the demo run 6,254 of its 8,285
edges are undirected similarity links, so a force layout pulls everything
together and shows neither direction nor how topics grow (#113). The document
therefore also carries two views that put time on the x axis, and the run
page draws them (`frontend/src/question/graph/`):

- **`lineage`**: the `LINEAGE_KEYWORDS` (32) most common keywords that at least
  `LINEAGE_MIN` (5) dated papers carry, each with its onset (the second-earliest
  year of its papers, so one stray old paper does not move it), last year,
  papers per year and the community most of its papers are in. Its edges:
  `grows_out_of`, from a keyword to the one it grows out of: of the keywords
  that start in an earlier year, the one whose papers its papers cite most, by
  citations / √(papers · papers), at least `FLOW_MIN` (2) citations, and a
  citation from a paper that carries both keywords does not count. One parent
  each, so the lineage is a forest; keywords that start the same year are never
  each other's parent. `crosses`: the other pairs that share at least
  `CROSS_MIN` (3) papers, the `CROSSINGS` (24) heaviest.
- **`main_paths`**: the `MAIN_PATHS` (90) citation links that the most
  source-to-sink paths run through (search path count, the main-path analysis
  of Hummon and Doreian, 1989). Knowledge flows from the cited paper to the
  citing one; papers are ordered by year then key, and a citation against that
  order (citing a later paper, or a cycle) is left out. `weight` is a link's
  count over the largest, unrounded: path counts grow exponentially with the
  depth of the citations, so a link far below the top can come out as 0.
- **`coverage`**: the kept papers, those with a year, and those whose full
  citations were looked up (scope `all`). Papers without a full lookup can
  only have citations the archive knows, so they mostly have no arrows; the
  page says how many.

On the demo run (217 kept papers) the lineage has 32 keywords, 18 of which grow
out of another, and the main paths run from ensemble spread (Molteni, Toth,
Buizza, 1996–2000) into calibration and post-processing (Hamill, Gneiting,
Raftery, Wilks, 2001–2007). Only 82 papers had full citations, which is why
the wind-gust community (82 papers, mostly 2012 and later) has no paths yet.

**Filters.** The run page recomputes both views in the browser over the papers
a filter keeps: words to exclude, words to focus on (both match a paper's title
or keywords at the start of a word, so "statistic" matches "statistical" and
"ai" does not match "rain"), communities and a year range, with ready-made
"AI / machine learning" and "statistical methods" word sets. The lineage reads
every keyword of the kept papers, as Python does, not only the 150 keyword
nodes, so a focused set can show keywords too rare to be nodes. The TypeScript
port (`model.ts`) counts paths in BigInts and divides them correctly rounded,
so its weights equal Python's to the last bit (above 2.2e-308; a subnormal
share may differ in its last bit, far below anything drawn). The two must agree:
`frontend/src/question/graph/graph-views.json` is a graph built by
`tests/py/question/test_graph.py` (`ISBE_WRITE_FIXTURES=1` rewrites it) with
Python's views, unfiltered and without its machine-learning papers, and
`model.test.ts` checks the client gives the same ones; it also pins a
3,000-paper chain whose path counts pass 1e300 to Python's ranking. Folding
differs in one place: Python's `casefold` maps "ß" to "ss", the browser's
`toLowerCase` does not.

## Building, storing and staleness

The graph is built at the end of every run (`stage` graph begin, `graph`, end)
and by `isbe question-graph <run>` for a run that has ended, which also prints
the communities and the most central papers. One row per run in `question_graphs` (migration 018)
holds the latest build: the document, zlib-compressed, without the paper
records, which are the run's candidates and are put back on read: 57 KB
for the 217-paper run below, 123 KB for the 2,000-paper test (`GET /graph`
answers 1.3 MB of JSON for the first, 156 KB gzipped).

The `fingerprint` digests what the graph is built from: the kept papers with
their decision, origin and keywords, the citation lookups (paper and scope),
and the method's version and parameters. A build whose fingerprint matches
the stored one returns the stored graph. `GET /graph` answers `stale: true`
when the fingerprint no longer matches, after an override or a new lookup;
building again (`isbe question-graph <run>`, or Rebuild on the run page,
`POST /graph`) replaces it. A rebuild adds no event, so another page open on
the run keeps its stale banner until it reads the graph again.

## Reading and downloading it

The run page reads `GET /graph?form=compact` (`isbe/question/graph_io.py`,
#57): every edge as `[kind, source, target, weight]` in numbers and the paper
records without abstracts. On the demo run that is 321 KB instead of 1.25 MB
(71 KB instead of 170 KB as served gzipped); the edges were 884 KB of the full form, the
abstracts 218 KB. The result card downloads the graph whole (`graph.json`) and
as GraphML (`graph.graphml`, for Gephi or Cytoscape), next to the paper set as
CSV and JSON.

## Decisions

- **No new dependency.** The issue asked whether to take `networkx` and
  `python-louvain` or write the algorithms. `networkx`'s PageRank needs SciPy
  and NumPy, and the CI runner installs from a mirror with no Docker Hub or
  PyPI access; the three algorithms (Louvain, power-iteration PageRank,
  Brandes betweenness) are about 120 lines here, deterministic, and tested
  against graphs with known answers.
- **One stored document, not node and edge tables.** The issue named
  `graph_nodes`, `graph_edges` and `graph_builds`. Every reader wants the
  whole graph, and nothing queries single nodes; one row per run keeps the
  write a single statement and the delete with the run's other rows. A
  history of builds is not kept: a rebuild replaces it.
- **Keywords, not LLM entities**, as the issue said. Topics (OpenAlex
  `topics`) are left for later.

## Performance

`tests/py/question/test_graph.py::test_a_2000_paper_run_builds_in_seconds`
builds a made-up run of 2,000 papers in 20 topics, each citing 5 earlier
papers and 20 outside works: 2,150 nodes, 18,587 edges, 20 communities in
1.3–1.6 s on the development laptop (2026-10-26). The test fails above 60 s.

The real run of the M3 demo question 大风集合预报 (2026-10-26; OpenAlex
through essaies, Strata screening): 872 candidates, 217 kept. With the full
citations of 19 kept papers (OpenAlex answered 502 after that) the graph had
367 nodes and 4,778 edges in 5 communities, built in 0.2 s; after `isbe
question-citations` brought 82 of them, `/graph` read stale and the rebuild
gave 8,285 edges in 0.3 s.
