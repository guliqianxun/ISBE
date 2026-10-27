"""The knowledge graph of a question run (`isbe.question.graph`): the algorithms on
small graphs with known answers; the graph of a made-up run of two topics joined by
one citation (nodes, edges of every kind, communities, bridges, enriched keywords)
against the contract; excluded papers left out; the fingerprint cache, staleness
after an override and the rebuild; a deleted run; and the build time of a
2,000-paper run."""

import json
import os
import random
import re
import time
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import httpx
import pytest
import question_flow as qf
from question_schemas import errors

from isbe.core import db, http
from isbe.question import citations, graph, runs, screen, sets, sources


@pytest.fixture
def conn():
    with closing(db.connect(":memory:")) as c:
        yield c


def paper(n: int, keywords: list[str]) -> dict:
    ident = f"2101.{n:05}"
    return {"key": f"arxiv:{ident}", "keys": [f"arxiv:{ident}"], "title": f"Paper {n}", "year": 2020 + n % 5,
            "cited_by_count": 10 * n, "keywords": keywords}


RADAR = ["Radar", "nowcasting"]
WIND = ["Ensemble  forecasting", "wind"]
# Papers 1–3 are about radar nowcasting, 4–6 about wind ensembles.
PAPERS = [paper(1, RADAR), paper(2, RADAR), paper(3, [*RADAR, "deep learning"]),
          paper(4, WIND), paper(5, WIND), paper(6, [*WIND, "extreme events"])]


def key(n: int) -> str:
    return f"arxiv:2101.{n:05}"


def make_run(conn, papers=PAPERS) -> str:
    run_id = runs.create(conn, "Which methods forecast severe wind?", "en", run_id="run-graph")
    store = sets.Store(conn, run_id)
    members = [{"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": n}]}
               for n, p in enumerate(papers, 1)]
    store.add(sets.PaperSet("s1.q1", "query", "severe wind", members))
    store.run("union", ["s1.q1"], "s1.union", "candidates", "All query results", {"plan_version": 1})
    store.run("dedup", ["s1.union"], "s1.candidates", "candidates", "Candidates after deduplication")
    return run_id


def lookups(conn, run_id: str, archive: dict, full: dict | None = None) -> None:
    fake = qf.Essaies()
    fake.archive, fake.full = archive, full or {}
    source = sources.Essaies(http.Http(5, transport=httpx.MockTransport(fake)), "http://essaies.test",
                             interval=0, retries=0, sleep=lambda _: None)
    assert citations.run(conn, source, run_id, full_limit=10 if full else 0, events=False).errors == []


# A chain within each topic, 3 → 4 across them; outside works: 2201.00001 cites 1
# and 2 (co-citation), 1 and 2 both cite 2201.00002 (coupling).
ARCHIVE = {
    "2101.00001": (["2101.00002", "2201.00002"], ["2201.00001"]),
    "2101.00002": (["2101.00003", "2201.00002"], ["2201.00001"]),
    "2101.00003": (["2101.00004"], []),
    "2101.00004": (["2101.00005"], []),
    "2101.00005": (["2101.00006"], []),
}


def edges_of(doc: dict, kind: str) -> dict[tuple[str, str], float]:
    return {(e["source"], e["target"]): e["weight"] for e in doc["edges"] if e["kind"] == kind}


def nodes_of(doc: dict) -> dict[str, dict]:
    return {n["id"]: n for n in doc["nodes"]}


def assert_edge_order(doc: dict) -> None:
    """The contract's direction rule, which JSON Schema cannot check: `cites` is
    citing → cited, `has_keyword` paper → keyword, the rest in sorted order."""
    for e in doc["edges"]:
        if e["kind"] == "has_keyword":
            assert not e["source"].startswith("kw:") and e["target"].startswith("kw:"), e
        elif e["kind"] != "cites":
            assert e["source"] < e["target"], e


# --- the algorithms ---------------------------------------------------------------------


def undirected(pairs: list[tuple[str, str]], weight: float = 1.0) -> dict[str, dict[str, float]]:
    adj: dict[str, dict[str, float]] = {}
    for a, b in pairs:
        adj.setdefault(a, {})[b] = weight
        adj.setdefault(b, {})[a] = weight
    return adj


def test_louvain_splits_two_triangles_joined_by_one_edge():
    adj = undirected([("a", "b"), ("b", "c"), ("a", "c"), ("d", "e"), ("e", "f"), ("d", "f"), ("c", "d")])
    groups = graph._louvain(sorted(adj), adj)
    assert groups["a"] == groups["b"] == groups["c"] != groups["d"] == groups["e"] == groups["f"]
    # Same input, same answer; a node with no edge stays alone.
    assert graph._louvain(sorted(adj), adj) == groups
    alone = graph._louvain([*sorted(adj), "z"], {**adj, "z": {}})
    assert alone["z"] not in {alone[n] for n in adj}


def test_louvain_merges_cliques_of_a_ring_level_by_level():
    # Eight 4-cliques in a ring: modularity is highest with one community per clique.
    pairs = []
    for c in range(8):
        nodes = [f"{c}.{i}" for i in range(4)]
        pairs += [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1 :]]
        pairs.append((f"{c}.3", f"{(c + 1) % 8}.0"))
    adj = undirected(pairs)
    groups = graph._louvain(sorted(adj), adj)
    assert len(set(groups.values())) == 8
    assert all(len({groups[f"{c}.{i}"] for i in range(4)}) == 1 for c in range(8))


def test_louvain_merges_communities_again_on_the_next_level(monkeypatch):
    # A ring of 30 five-cliques (the resolution limit's example): single moves find
    # the cliques, then the aggregated level merges neighbouring cliques.
    pairs = []
    for c in range(30):
        nodes = [f"{c:02}.{i}" for i in range(5)]
        pairs += [(a, b) for i, a in enumerate(nodes) for b in nodes[i + 1 :]]
        pairs.append((f"{c:02}.4", f"{(c + 1) % 30:02}.0"))
    adj = undirected(pairs)
    levels = []
    level = graph._level

    def spy(g, loops, resolution):
        moved, comm = level(g, loops, resolution)
        levels.append((len(g), moved, len(set(comm.values()))))
        return moved, comm

    monkeypatch.setattr(graph, "_level", spy)
    groups = graph._louvain(sorted(adj), adj)
    assert levels[0] == (150, True, 30)
    # Level 2 ran on the 30 cliques (self-loops) and merged them.
    assert levels[1][0] == 30 and levels[1][1] and levels[1][2] < 30
    assert all(len({groups[f"{c:02}.{i}"] for i in range(5)}) == 1 for c in range(30))
    assert len(set(groups.values())) == levels[-1][2] < 30


def test_pagerank_matches_hand_computed_values():
    # A cycle: all equal. A star where every leaf points at the hub: the hub gets
    # (1-d)/n + d·(leaves' rank + its own spread as a dangling node).
    cycle = {"a": {"b": 1.0}, "b": {"c": 1.0}, "c": {"a": 1.0}}
    assert graph._pagerank(["a", "b", "c"], cycle) == pytest.approx({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})
    star = {"h": {}, "x": {"h": 1.0}, "y": {"h": 1.0}, "z": {"h": 1.0}}
    rank = graph._pagerank(["h", "x", "y", "z"], star)
    assert sum(rank.values()) == pytest.approx(1)
    # Solved by hand: leaf = (0.15 + 0.85·h)/4, h = 0.0375 + 0.85·(3·leaf + h/4).
    leaf = rank["x"]
    assert rank["x"] == rank["y"] == rank["z"]
    assert leaf == pytest.approx((0.15 + 0.85 * rank["h"]) / 4)
    assert rank["h"] == pytest.approx(0.0375 + 0.85 * (3 * leaf + rank["h"] / 4))
    assert rank["h"] > 0.45


def test_betweenness_of_a_path_and_a_star():
    path = {"a": {"b"}, "b": {"a", "c"}, "c": {"b"}}
    assert graph._betweenness(["a", "b", "c"], path) == {"a": 0, "b": 1, "c": 0}
    star = {"h": {"1", "2", "3", "4"}, **{s: {"h"} for s in "1234"}}
    got = graph._betweenness(["1", "2", "3", "4", "h"], star)
    assert got["h"] == pytest.approx(1) and got["1"] == 0


def test_betweenness_from_sampled_sources_stays_close(monkeypatch):
    rng = random.Random(7)
    nodes = [f"n{i:03}" for i in range(120)]
    adj: dict[str, set[str]] = {n: set() for n in nodes}
    for i, n in enumerate(nodes[1:], 1):
        m = nodes[rng.randrange(i)]
        adj[n].add(m)
        adj[m].add(n)
    exact = graph._betweenness(nodes, adj)
    monkeypatch.setattr(graph, "SOURCES", 40)
    sampled = graph._betweenness(nodes, adj)
    top = max(exact, key=exact.get)
    assert max(sampled, key=sampled.get) == top
    assert sampled[top] == pytest.approx(exact[top], rel=0.35)


# --- a run's graph ----------------------------------------------------------------------


def test_the_graph_of_two_topics_joined_by_one_citation(conn):
    run_id = make_run(conn)
    lookups(conn, run_id, ARCHIVE)
    built = graph.build(conn, run_id)
    doc = built.doc
    assert not built.cached
    assert errors("question_graph", doc) == []
    assert_edge_order(doc)
    nodes = nodes_of(doc)
    assert sorted(nodes) == [*(key(n) for n in range(1, 7)), "kw:ensemble forecasting", "kw:nowcasting",
                             "kw:radar", "kw:wind"]
    # One-off keywords ("deep learning") are no node; keywords are normalized.
    assert nodes["kw:radar"]["label"] == "radar" and nodes["kw:radar"]["kind"] == "keyword"
    assert edges_of(doc, "cites") == {(key(1), key(2)): 1, (key(2), key(3)): 1, (key(3), key(4)): 1,
                                      (key(4), key(5)): 1, (key(5), key(6)): 1}
    assert edges_of(doc, "co_citation") == {(key(1), key(2)): 1}
    # 1 and 2 share 2201.00002; 2201.00001 cites both (it is a co-citation, not coupling).
    assert edges_of(doc, "coupling") == {(key(1), key(2)): 1}
    assert len(edges_of(doc, "has_keyword")) == 12
    assert edges_of(doc, "keyword_cooccur") == {("kw:nowcasting", "kw:radar"): 3,
                                                ("kw:ensemble forecasting", "kw:wind"): 3}

    radar, wind = nodes[key(1)]["community"], nodes[key(6)]["community"]
    assert {nodes[key(n)]["community"] for n in (1, 2, 3)} == {radar} and nodes["kw:radar"]["community"] == radar
    assert {nodes[key(n)]["community"] for n in (4, 5, 6)} == {wind} != {radar}
    assert [n for n in nodes if nodes[n].get("metrics", {}).get("bridge")] == [key(3), key(4)]
    assert sorted(c["size"] for c in doc["communities"]) == [3, 3]
    labels = {c["id"]: c["top_keywords"] for c in doc["communities"]}
    assert set(labels[radar]) == {"radar", "nowcasting"}  # "deep learning": one paper only
    assert set(labels[wind][:2]) == {"ensemble forecasting", "wind"}

    ranks = {n: nodes[key(n)]["metrics"]["pagerank"] for n in range(1, 7)}
    # Rank flows down the chain to 6, the end of every citation path.
    assert max(ranks, key=ranks.get) == 6 and sum(ranks.values()) == pytest.approx(1, abs=1e-6)
    between = {n: nodes[key(n)]["metrics"]["betweenness"] for n in range(1, 7)}
    assert between[3] == between[4] == max(between.values()) and between[1] == between[6] == 0
    assert nodes[key(1)]["metrics"]["degree"] == 3  # paper 2 and two keywords
    assert nodes[key(1)]["label"] == "Paper 1 (2021)" and nodes[key(1)]["paper"]["title"] == "Paper 1"
    assert {nodes[key(n)]["decision"] for n in range(1, 7)} == {"unscreened"}
    assert {nodes[key(n)]["origin"] for n in range(1, 7)} == {"seed"}


def test_the_event_summary_fits_the_contract(conn):
    run_id = make_run(conn)
    doc = graph.build(conn, run_id).doc
    event = {"schema": "question_event@1", "run_id": run_id, "seq": 1, "at": db.now(), "type": "graph",
             "data": graph.summary(doc)}
    assert errors("question_event", event) == []
    assert graph.summary(doc)["communities"] == len(doc["communities"]) == 2  # keywords alone join them


def test_excluded_papers_are_left_out_and_an_override_makes_the_graph_stale(conn):
    run_id = make_run(conn)
    lookups(conn, run_id, ARCHIVE)
    first = graph.build(conn, run_id)
    again = graph.build(conn, run_id)
    assert again.cached and again.doc == first.doc
    assert graph.get(conn, run_id) == first.doc
    assert not graph.read(conn, run_id)[1]

    screen.override(conn, run_id, key(6), "exclude", "dana", "off topic")
    assert graph.read(conn, run_id)[1]
    rebuilt = graph.build(conn, run_id)
    assert not rebuilt.cached and rebuilt.doc["fingerprint"] != first.doc["fingerprint"]
    assert key(6) not in nodes_of(rebuilt.doc) and (key(5), key(6)) not in edges_of(rebuilt.doc, "cites")
    assert nodes_of(rebuilt.doc)[key(1)]["decision"] == "unscreened"
    screen.override(conn, run_id, key(1), "include", "dana")
    assert nodes_of(graph.build(conn, run_id).doc)[key(1)]["decision"] == "include"
    assert errors("question_graph", graph.get(conn, run_id)) == []


def test_a_new_lookup_makes_the_graph_stale(conn):
    run_id = make_run(conn)
    lookups(conn, run_id, {"2101.00001": (["2101.00002"], [])})
    graph.build(conn, run_id)
    assert not graph.read(conn, run_id)[1]
    # The archive pass has looked every paper up; the full pass adds 4 → 5.
    lookups(conn, run_id, {}, full={"2101.00004": (["2101.00005"], [])})
    assert graph.read(conn, run_id)[1]
    assert (key(4), key(5)) in edges_of(graph.build(conn, run_id).doc, "cites")


def test_a_run_without_citations_or_keywords_still_has_a_graph(conn):
    run_id = make_run(conn, [paper(1, []), paper(2, [])])
    doc = graph.build(conn, run_id).doc
    assert errors("question_graph", doc) == []
    assert doc["edges"] == [] and doc["communities"] == []
    assert [n["community"] for n in doc["nodes"]] == [None, None]
    assert [n["metrics"]["pagerank"] for n in doc["nodes"]] == [0.5, 0.5]


def test_a_shared_work_cited_by_too_many_papers_joins_no_pairs(conn, monkeypatch):
    monkeypatch.setattr(graph, "HUB", 2)
    run_id = make_run(conn)
    # 2201.00002 is a reference of 1, 2 and 3: above the hub limit.
    lookups(conn, run_id, {f"2101.{n:05}": (["2201.00002"], []) for n in (1, 2, 3)})
    assert edges_of(graph.build(conn, run_id).doc, "coupling") == {}


def test_deleting_the_run_deletes_its_graph(conn):
    run_id = make_run(conn)
    graph.build(conn, run_id)
    runs.delete(conn, run_id)
    assert conn.execute("SELECT count(*) FROM question_graphs").fetchone()[0] == 0


def test_a_2000_paper_run_builds_in_seconds(conn):
    """The build time of the issue's size: 2,000 papers in 20 topics of 30 keywords,
    each paper citing 5 earlier papers (mostly in its topic) and 20 outside works."""
    rng = random.Random(51)
    topics = [[f"topic {t} term {k}" for k in range(30)] for t in range(20)]
    papers = [paper(n, rng.sample(topics[n % 20], 4)) for n in range(1, 2001)]
    run_id = make_run(conn, papers)
    archive = {}
    for n in range(1, 2001):
        earlier = [m for m in range(max(1, n - 400), n) if m % 20 == n % 20 or rng.random() < 0.05]
        cited = rng.sample(earlier, min(5, len(earlier)))
        outside = [f"2201.{rng.randrange(n % 20 * 500, n % 20 * 500 + 500):05}" for _ in range(20)]
        archive[f"2101.{n:05}"] = ([f"2101.{m:05}" for m in cited] + outside, [])
    lookups(conn, run_id, archive)
    started = time.monotonic()
    built = graph.build(conn, run_id)
    seconds = time.monotonic() - started
    doc = built.doc
    print(f"2,000 papers: {len(doc['nodes'])} nodes, {len(doc['edges'])} edges, "
          f"{len(doc['communities'])} communities in {seconds:.1f} s")
    assert errors("question_graph", doc) == []
    assert_edge_order(doc)
    assert 10 <= len(doc["communities"]) <= 40
    assert seconds < 60


def test_the_cli_builds_a_run_s_graph_without_events(data, capsys):
    from isbe import cli

    with closing(db.connect(data / "isbe.db")) as c:
        run_id = make_run(c)
        lookups(c, run_id, ARCHIVE)
    # Not before the run has ended: its candidates still change.
    assert cli.main(["--data", str(data), "question-graph", run_id]) == 1
    assert "has not ended" in capsys.readouterr().err
    with closing(db.connect(data / "isbe.db")) as c:
        runs.set_status(c, run_id, "done")
        before = len(runs.events(c, run_id))
    assert cli.main(["--data", str(data), "question-graph", run_id, "--top", "1"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"graph [0-9a-f]{32} built in \d+\.\d s: 6 papers, 4 keywords; cites 5, co_citation 1, "
                        r"coupling 1, has_keyword 12, keyword_cooccur 2", out[0])
    assert out[1] == "communities (2):" and out[2].startswith("    0     3 papers  ")
    assert out[3] == "most central papers (PageRank):" and out[4].endswith("Paper 6 (2021)")
    with closing(db.connect(data / "isbe.db")) as c:
        fingerprint = graph.get(c, run_id)["fingerprint"]
        assert len(runs.events(c, run_id)) == before
    assert out[-1] == (f'question-graph {{"cached":false,"communities":2,"edges":21,"fingerprint":"{fingerprint}",'
                       f'"nodes":10,"run_id":"{run_id}"}}')
    assert cli.main(["--data", str(data), "question-graph", run_id]) == 0
    assert '"cached":true' in capsys.readouterr().out
    assert cli.main(["--data", str(data), "question-graph", "run-nope"]) == 1
    assert "No such question run" in capsys.readouterr().err


def test_the_cap_drops_a_tied_weight_rather_than_pick_by_key(monkeypatch):
    weights = {("a", "b"): 3, ("a", "c"): 2, ("b", "c"): 2, ("c", "d"): 1}
    monkeypatch.setattr(graph, "EDGE_CAP", 4)
    assert len(graph._strongest(weights)) == 4
    # The cut falls among the weight-2 pairs: kept whole while under twice the cap…
    monkeypatch.setattr(graph, "EDGE_CAP", 2)
    assert graph._strongest(weights) == [(("a", "b"), 3), (("a", "c"), 2), (("b", "c"), 2)]
    # …else none of them.
    monkeypatch.setattr(graph, "EDGE_CAP", 1)
    assert graph._strongest(weights) == [(("a", "b"), 3)]
    # A kind of one weight only is not emptied while it fits.
    flat = {(f"p{i}", f"q{i}"): 1 for i in range(5)}
    monkeypatch.setattr(graph, "EDGE_CAP", 3)
    assert len(graph._strongest(flat)) == 5


def test_an_outside_work_named_by_different_keys_is_one_work(conn):
    run_id = make_run(conn, [paper(1, []), paper(2, []), paper(3, [])])
    # 1 names the work by its OpenAlex id, 2 by its DOI and OpenAlex id, 3 by its DOI.
    named = {1: [["openalex:W1"]], 2: [["doi:10.1/x", "openalex:W1"]], 3: [["doi:10.1/x"]]}
    for n, works in named.items():
        conn.execute(
            "INSERT INTO question_citations (run_id, key, scope, found, cites, cited_by, fetched_at) "
            "VALUES (?, ?, 'archive', 1, ?, '[]', ?)",
            (run_id, key(n), json.dumps([{"keys": k} for k in works]), db.now()),
        )
    coupling = edges_of(graph.build(conn, run_id).doc, "coupling")
    assert coupling == {(key(1), key(2)): 1, (key(1), key(3)): 1, (key(2), key(3)): 1}


@pytest.mark.parametrize("order", [[1, 2, 3], [1, 3, 2], [3, 1, 2]])
def test_a_work_is_one_work_whatever_order_its_names_come_in(conn, order):
    run_id = make_run(conn, [paper(1, []), paper(2, []), paper(3, [])])
    # The work that joins the two names (2) may come last.
    named = {1: ["doi:10.1/x"], 3: ["openalex:W1"], 2: ["doi:10.1/x", "openalex:W1"]}
    for n in order:
        conn.execute(
            "INSERT INTO question_citations (run_id, key, scope, found, cites, cited_by, fetched_at) "
            "VALUES (?, ?, 'archive', 1, ?, '[]', ?)",
            (run_id, key(n), json.dumps([{"keys": named[n]}]), db.now()),
        )
    coupling = edges_of(graph.build(conn, run_id).doc, "coupling")
    assert coupling == {(key(1), key(2)): 1, (key(1), key(3)): 1, (key(2), key(3)): 1}


def test_a_community_is_not_labelled_by_a_keyword_only_one_paper_carries():
    carried = {"p": {"one-off", "shared"}, "q": {"shared"}, "r": {"other"}, "s": {"other"}}
    count = {"one-off": 1, "shared": 2, "other": 2}
    assert graph._enriched(["p"], carried, count, 4) == ["shared"]


def test_a_graph_whose_paper_lost_its_key_still_reads(conn):
    run_id = make_run(conn)
    graph.build(conn, run_id)
    doc = graph.get(conn, run_id)
    gone = {**doc, "nodes": [{**n, "id": "arxiv:2101.09999"} if n["id"] == key(1) else n for n in doc["nodes"]]}
    node = next(n for n in graph._with_papers(gone, [])["nodes"] if n["id"] == "arxiv:2101.09999")
    assert node["paper"] == {"key": "arxiv:2101.09999", "keys": ["arxiv:2101.09999"], "title": "Paper 1 (2021)"}


def test_main_paths_keep_the_links_most_paths_run_through():
    # 0 and 1 (2000) are both cited by 2 (2001), which 3 cites (2002); 4 cites 1.
    # Paths: 0→2→3, 1→2→3, 1→4. The 2→3 link carries two of them, the rest one.
    years = {"p0": 2000, "p1": 2000, "p2": 2001, "p3": 2002, "p4": 2003}
    cites = [("p2", "p0"), ("p2", "p1"), ("p3", "p2"), ("p4", "p1"), ("p0", "p3")]  # the last cites forward
    paths = graph._main_paths(years, cites)
    assert paths[0] == {"source": "p2", "target": "p3", "weight": 1.0}
    assert {(e["source"], e["target"]): e["weight"] for e in paths[1:]} == {
        ("p0", "p2"): 0.5, ("p1", "p2"): 0.5, ("p1", "p4"): 0.5}
    assert graph._main_paths({}, []) == []


def test_a_link_far_below_the_top_keeps_its_share_and_fits_the_contract(conn):
    # 44 papers, each citing the two before it (paths grow like Fibonacci numbers),
    # and one pair on its own: the pair's single path is about 2e-9 of the top,
    # which rounding to six places made 0.0, against the contract.
    years = {f"p{i:02}": 1900 + i for i in range(44)} | {"x0": 2000, "x1": 2001}
    cites = [(f"p{i:02}", f"p{j:02}") for i in range(44) for j in range(max(0, i - 2), i)] + [("x1", "x0")]
    paths = graph._main_paths(years, cites)
    pair = next(p for p in paths if p["source"] == "x0")
    assert paths[0]["weight"] == 1.0 and 0 < pair["weight"] < 1e-6
    doc = graph.build(conn, make_run(conn)).doc
    for weight in (pair["weight"], 0.0):  # an even smaller share underflows to 0.0
        doc["main_paths"] = [{"source": key(1), "target": key(2), "weight": weight}]
        assert errors("question_graph", doc) == []


def test_main_paths_stop_at_the_cap(monkeypatch):
    monkeypatch.setattr(graph, "MAIN_PATHS", 2)
    years = {f"p{i}": 2000 + i for i in range(6)}
    cites = [(f"p{i + 1}", f"p{i}") for i in range(5)]
    assert len(graph._main_paths(years, cites)) == 2


def lineage_input():
    """Old papers a1–a5 on "spread" (2000–2004), new papers b1–b5 on "calibration"
    (2010–2014) citing them; c1–c5 on "verification" (2000–2004) share three papers'
    worth of "spread" and are never cited."""
    carried, years = {}, {}
    for i in range(1, 6):
        carried[f"a{i}"], years[f"a{i}"] = {"spread"}, 1999 + i
        carried[f"b{i}"], years[f"b{i}"] = {"calibration"}, 2009 + i
        carried[f"c{i}"], years[f"c{i}"] = {"verification"} | ({"spread"} if i <= 3 else set()), 1999 + i
    count = defaultdict(int)
    for ws in carried.values():
        for w in ws:
            count[w] += 1
    cites = [(f"b{i}", f"a{i}") for i in range(1, 6)] + [("b1", "c4")]
    community = {k: (1 if k.startswith("b") else 0) for k in carried}
    return carried, count, years, community, cites


def test_a_later_keyword_grows_out_of_the_one_its_papers_cite():
    lineage = graph._lineage(*lineage_input())
    kws = {k["label"]: k for k in lineage["keywords"]}
    assert [k["label"] for k in lineage["keywords"]] == ["spread", "calibration", "verification"]  # 8, 5, 5 papers
    assert kws["calibration"] == {"id": "kw:calibration", "label": "calibration", "papers": 5, "onset": 2011,
                                  "last": 2014, "per_year": [[y, 1] for y in range(2010, 2015)], "community": 1}
    # The second-earliest year: a1 and c1 are both 2000.
    assert kws["spread"]["onset"] == 2000 and kws["spread"]["per_year"][0] == [2000, 2]
    grows = [e for e in lineage["edges"] if e["kind"] == "grows_out_of"]
    # Five citations to "spread" beat one to "verification"; verification starts the
    # same year as spread, so neither is the other's parent.
    assert grows == [{"source": "kw:calibration", "target": "kw:spread", "kind": "grows_out_of", "weight": 5.0}]
    assert [e for e in lineage["edges"] if e["kind"] == "crosses"] == [
        {"source": "kw:spread", "target": "kw:verification", "kind": "crosses", "weight": 3.0}]


def test_lineage_leaves_out_rare_or_undated_keywords(monkeypatch):
    carried, count, years, community, cites = lineage_input()
    del years["b5"]  # four dated papers left on "calibration"
    lineage = graph._lineage(carried, count, years, community, cites)
    assert [k["label"] for k in lineage["keywords"]] == ["spread", "verification"]
    monkeypatch.setattr(graph, "LINEAGE_KEYWORDS", 1)
    assert [k["label"] for k in graph._lineage(*lineage_input())["keywords"]] == ["spread"]


def test_the_graph_carries_lineage_main_paths_and_coverage(conn):
    run_id = make_run(conn)
    lookups(conn, run_id, ARCHIVE)
    assert graph.build(conn, run_id).doc["coverage"] == {"papers": 6, "dated": 6, "full_citations": 0}
    # A full lookup counts once made, whatever it found (the fake knows only paper 1).
    lookups(conn, run_id, ARCHIVE, full={"2101.00001": (["2101.00002"], [])})
    doc = graph.build(conn, run_id).doc
    assert errors("question_graph", doc) == []
    assert doc["coverage"] == {"papers": 6, "dated": 6, "full_citations": 6}
    # Six papers carry fewer than LINEAGE_MIN per keyword: no lineage rows.
    assert doc["lineage"] == {"keywords": [], "edges": []}
    # Papers 1–6 date from 2021, 2022, 2023, 2024, 2020, 2021, and each cites the
    # next: only 4 (2024) citing 5 (2020) runs back in time; the rest cite later
    # papers and are left out of the flow. Knowledge flows from 5 to 4.
    assert doc["main_paths"] == [{"source": key(5), "target": key(4), "weight": 1.0}]


# The frontend computes the same two views over a filtered graph
# (frontend/src/question/graph/model.ts); this run's graph, with the views Python
# gives it, is its fixture. ISBE_WRITE_FIXTURES=1 writes it again.
VIEWS_FIXTURE = Path(__file__).parents[3] / "frontend" / "src" / "question" / "graph" / "graph-views.json"


def views_run(conn) -> str:
    """Spread (1996–2001) → calibration (2005–2010, the last three also on
    verification) → machine learning (2018–2023); three more verification papers
    (2003–2006) cite spread. Paper 22 has no year; paper 23 links to nothing, so it
    has no community."""
    spec = {}
    for n in range(1, 7):
        spec[n] = (1995 + n, ["ensemble spread"])
        spec[6 + n] = (2004 + n, ["forecast calibration", *(["verification"] if n > 3 else [])])
        spec[12 + n] = (2017 + n, ["machine learning"])
    for n, y in zip(range(19, 22), (2003, 2004, 2006), strict=True):
        spec[n] = (y, ["verification"])
    spec[22] = (None, ["verification"])
    spec[23] = (2015, ["a topic of its own"])
    papers = [{**paper(n, kws), "year": y} for n, (y, kws) in spec.items()]
    run_id = make_run(conn, papers)
    cites = {n: [] for n in spec}
    for n in range(7, 13):
        cites[n] += [n - 6, n - 5 if n < 12 else 1]  # calibration cites spread
    for n in range(13, 19):
        cites[n] += [n - 6, n - 1 if n > 13 else 7]  # ML cites calibration, and the ML before it
    for n in range(19, 23):
        cites[n] += [2, 3]
    archive = {f"2101.{n:05}": ([f"2101.{m:05}" for m in cited], []) for n, cited in cites.items()}
    lookups(conn, run_id, archive)
    return run_id


def test_the_views_fixture_matches_the_python_views(conn):
    doc = graph.build(conn, views_run(conn)).doc
    lineage = {k["label"]: k for k in doc["lineage"]["keywords"]}
    assert set(lineage) == {"ensemble spread", "forecast calibration", "machine learning", "verification"}
    grows = {(e["source"][3:], e["target"][3:]) for e in doc["lineage"]["edges"] if e["kind"] == "grows_out_of"}
    assert grows == {("forecast calibration", "ensemble spread"), ("machine learning", "forecast calibration"),
                     ("verification", "ensemble spread")}
    assert doc["main_paths"] and doc["main_paths"][0]["weight"] == 1.0
    nodes = nodes_of(doc)
    assert nodes[key(23)]["community"] is None and nodes[key(22)]["paper"]["year"] is None
    assert doc["coverage"] == {"papers": 23, "dated": 22, "full_citations": 0}
    fixture = {k: doc[k] for k in ("nodes", "edges", "communities", "lineage", "main_paths", "coverage")}
    # The views of the same graph without its machine-learning papers, as the
    # page's "Exclude: machine learning" computes them.
    kept = [n for n in doc["nodes"] if n["kind"] == "paper" and "machine learning" not in n["paper"]["keywords"]]
    keys = {n["id"] for n in kept}
    carried = {n["id"]: graph._keywords(n["paper"]) for n in kept}
    count = defaultdict(int)
    for words in carried.values():
        for w in words:
            count[w] += 1
    years = {n["id"]: n["paper"]["year"] for n in kept if n["paper"]["year"] is not None}
    cites = [(e["source"], e["target"]) for e in doc["edges"]
             if e["kind"] == "cites" and e["source"] in keys and e["target"] in keys]
    fixture["without_machine_learning"] = {
        "lineage": graph._lineage(carried, count, years, {n["id"]: n["community"] for n in kept}, cites),
        "main_paths": graph._main_paths(years, cites),
    }
    if os.environ.get("ISBE_WRITE_FIXTURES"):
        VIEWS_FIXTURE.write_text(json.dumps(fixture, indent=1, ensure_ascii=False) + "\n")
    assert json.loads(VIEWS_FIXTURE.read_text()) == json.loads(json.dumps(fixture))
