"""The knowledge graph of a question run (#51): keywords and citations over the kept
papers, with communities and node metrics (`question_graph@1`). The last stage of a
run, after the expand stage. Sole writer of `question_graphs`. The method and the
choices behind it are in docs/question/graph.md.

Nodes are the kept papers (included, or never screened: recall first) and the
keywords at least two of them carry. Edges:

- `cites`: citing → cited, between kept papers (`citations.edges`).
- `has_keyword`: a paper and one of its keyword nodes.
- `co_citation`: two kept papers cited together, weight the works that cite both.
- `coupling`: two kept papers that share references, weight the shared works.
- `keyword_cooccur`: two keyword nodes carried by the same papers, weight those
  papers (at least two).

The derived kinds count every work the citation lookups name, not just candidates,
so a run whose papers rarely cite each other still has structure.

Communities are Louvain's on the undirected weighted graph of the paper–paper and
paper–keyword edges (`WEIGHTS`); a node with no such edge has none. PageRank runs on
the papers: `cites` edges plus the derived ones both ways. Betweenness is
over the paper–paper edges, from at most `SOURCES` sources. A paper is a bridge when
a paper–paper edge joins it to another community and its betweenness is among the
highest `BRIDGES` of the papers: in a set of related papers most have some edge
across, so the edge alone marks half of them. Each community's keywords are ranked
by enrichment against the whole set.

The graph is stored whole, one row per run, without the paper records (they are the
run's candidates, put back on read). Its fingerprint digests what it was built from:
the kept papers with their decisions and keywords, the citation lookups and the
method's parameters. `build` returns the stored graph when the fingerprint has not
changed; an override or a new lookup makes it `stale` until it is built again.
"""

import hashlib
import math
import sqlite3
import time
import zlib
from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from isbe.core import db, jsonfmt
from isbe.question import citations, runs, service

SCHEMA = "question_graph@1"
# Bumped when the method changes, so stored graphs read as stale.
VERSION = 3
# Analysis weights per edge kind: an edge counts `weight · its own weight` (shared
# works for the derived kinds, capped at `SHARED_CAP`).
WEIGHTS = {"cites": 1.0, "co_citation": 0.5, "coupling": 0.5, "has_keyword": 0.3}
SHARED_CAP = 4
# Keyword nodes: carried by at least this many kept papers, the most common first.
KEYWORD_MIN = 2
KEYWORDS = 150
# Edges kept per derived kind, the heaviest first.
EDGE_CAP = 5000
# A work cited by (or citing) more kept papers than this joins no pairs: a textbook
# every paper cites says nothing about which two belong together, and its pairs grow
# with the square of its count.
HUB = 100
# Betweenness sources (evenly spaced over the sorted papers) above this many papers.
SOURCES = 256
# Share of the papers, by betweenness, that can be bridges.
BRIDGES = 0.1
DAMPING = 0.85
TOP_KEYWORDS = 5
LABEL = 80  # characters of a title kept in a node label
# The topic lineage: keywords carried by at least this many kept papers with a year,
# the most common first; a keyword pair crosses when it shares this many papers.
LINEAGE_MIN = 5
LINEAGE_KEYWORDS = 32
CROSS_MIN = 3
CROSSINGS = 24
# A keyword grows out of another when its papers cite the other's at least this often.
FLOW_MIN = 2
# Citation links kept on the main paths, by search path count.
MAIN_PATHS = 90


@dataclass
class Built:
    doc: dict[str, Any]
    cached: bool  # the stored graph had the same fingerprint
    seconds: float


def build(conn: sqlite3.Connection, run_id: str) -> Built:
    """The run's graph, built and stored unless the stored one is still current."""
    started = time.monotonic()
    inputs = _inputs(conn, run_id)
    stored = _stored(conn, run_id)
    if stored is not None and stored["fingerprint"] == inputs.fingerprint:
        return Built(_with_papers(stored, inputs.members), True, time.monotonic() - started)
    doc = _build(run_id, inputs)
    slim = {**doc, "nodes": [{k: v for k, v in n.items() if k != "paper"} for n in doc["nodes"]]}
    with db.immediate(conn):
        runs.get(conn, run_id)
        conn.execute(
            "INSERT OR REPLACE INTO question_graphs (run_id, fingerprint, built_at, nodes, edges, communities, doc) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, doc["fingerprint"], doc["built_at"], len(doc["nodes"]), len(doc["edges"]),
             len(doc["communities"]), zlib.compress(jsonfmt.dumps(slim).encode("utf-8"), 6)),
        )
    return Built(doc, False, time.monotonic() - started)


def get(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    """The stored graph with its paper records, or None before one is built
    (`read` also says whether it is stale)."""
    runs.get(conn, run_id)
    stored = _stored(conn, run_id)
    return None if stored is None else _with_papers(stored, service.papers(conn, run_id))


def read(conn: sqlite3.Connection, run_id: str) -> tuple[dict[str, Any] | None, bool]:
    """The stored graph with its paper records (None before one is built) and
    whether it is stale: what it is built from has changed since."""
    runs.get(conn, run_id)
    stored = _stored(conn, run_id)
    if stored is None:
        return None, False
    inputs = _inputs(conn, run_id)
    return _with_papers(stored, inputs.members), stored["fingerprint"] != inputs.fingerprint


def summary(doc: dict[str, Any]) -> dict[str, Any]:
    """The `graph` event's data."""
    return {"fingerprint": doc["fingerprint"], "nodes": len(doc["nodes"]), "edges": len(doc["edges"]),
            "communities": len(doc["communities"])}


# --- inputs ----------------------------------------------------------------------------


@dataclass
class _Inputs:
    members: list[dict[str, Any]]  # service.papers
    kept: list[dict[str, Any]]  # the members the graph is built over
    cites: list[tuple[str, str]]  # between kept papers
    refs: dict[str, set[str]]  # kept key → the works it cites (canonical ids)
    citers: dict[str, set[str]]  # kept key → the works that cite it
    full: set[str]  # kept keys whose full citations were looked up (scope `all`)
    fingerprint: str


def _decision(member: dict[str, Any]) -> str:
    return member["decision"] if member["decision"] in ("include", "exclude") else "unscreened"


def _inputs(conn: sqlite3.Connection, run_id: str) -> _Inputs:
    with db.snapshot(conn):
        members = service.papers(conn, run_id)
        rows = conn.execute(
            "SELECT key, scope, cites, cited_by FROM question_citations WHERE run_id = ? ORDER BY key, scope",
            (run_id,),
        ).fetchall()
        edges = citations.edges(conn, run_id, members)["citations"]
    kept = [m for m in members if _decision(m) != "exclude"]
    keys = {m["paper"]["key"] for m in kept}
    lookups = [(key, scope, jsonfmt.loads(c), jsonfmt.loads(b)) for key, scope, c, b in rows]
    # Every identity key of a work → one id, whatever order the lookups name it in:
    # keys named together are one work (union-find), known as the candidate's
    # preferred key when it is a candidate, else as its smallest key.
    parent: dict[str, str] = {}

    def root(k: str) -> str:
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def join(names: list[str]) -> None:
        for k in names[1:]:
            a, b = root(names[0]), root(k)
            if a != b:
                parent[max(a, b)] = min(a, b)

    for m in members:
        join(m["paper"]["keys"])
    for _, _, cites_list, cited_by_list in lookups:
        for work in (*cites_list, *cited_by_list):
            join(work["keys"])
    preferred = {root(m["paper"]["key"]): m["paper"]["key"] for m in reversed(members)}

    def ident(work: dict[str, Any]) -> str:
        r = root(work["keys"][0])
        return preferred.get(r, r)

    canon = {k: m["paper"]["key"] for m in members for k in m["paper"]["keys"]}

    refs: dict[str, set[str]] = defaultdict(set)
    citers: dict[str, set[str]] = defaultdict(set)
    full: set[str] = set()
    digest = hashlib.sha256()
    for key, scope, cites_list, cited_by_list in lookups:
        digest.update(f"{key}\x1f{scope}\x1e".encode())
        a = canon.get(key)
        if a not in keys:
            continue
        if scope == "all":
            full.add(a)
        refs[a].update(w for w in (ident(x) for x in cites_list) if w != a)
        citers[a].update(w for w in (ident(x) for x in cited_by_list) if w != a)
    cites = [(a, b) for a, b in edges if a in keys and b in keys]
    for a, b in cites:
        refs[a].add(b)
        citers[b].add(a)
    papers = sorted(
        (m["paper"]["key"], _decision(m), _origin(m), _label(m["paper"]), sorted(_keywords(m["paper"]))) for m in kept
    )
    fingerprint = hashlib.sha256(jsonfmt.dumps({
        "version": VERSION,
        "params": [WEIGHTS, SHARED_CAP, KEYWORD_MIN, KEYWORDS, EDGE_CAP, HUB, SOURCES, BRIDGES, DAMPING, TOP_KEYWORDS,
                   LABEL, LINEAGE_MIN, LINEAGE_KEYWORDS, CROSS_MIN, CROSSINGS, FLOW_MIN, MAIN_PATHS],
        "papers": papers,
        "lookups": digest.hexdigest(),
    }).encode("utf-8")).hexdigest()[:32]
    return _Inputs(members, kept, cites, dict(refs), dict(citers), full, fingerprint)


def _origin(member: dict[str, Any]) -> str:
    return "seed" if any(p.get("via") == "search" for p in member["provenance"]) else "expansion"


def _keywords(paper: dict[str, Any]) -> set[str]:
    return {" ".join(k.split()).casefold() for k in paper.get("keywords") or [] if k.strip()}


def _stored(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT doc FROM question_graphs WHERE run_id = ?", (run_id,)).fetchone()
    return None if row is None else jsonfmt.loads(zlib.decompress(row[0]).decode("utf-8"))


def _with_papers(doc: dict[str, Any], members: list[dict[str, Any]]) -> dict[str, Any]:
    records = {m["paper"]["key"]: m["paper"] for m in members}

    def record(node: dict[str, Any]) -> dict[str, Any]:
        # A paper no longer a candidate under this key (a graph built before the
        # dedup merged it) keeps the little the graph knows; the graph reads stale.
        return records.get(node["id"]) or {"key": node["id"], "keys": [node["id"]], "title": node["label"]}

    return {**doc, "nodes": [{**n, "paper": record(n)} if n["kind"] == "paper" else n for n in doc["nodes"]]}


# --- the graph -------------------------------------------------------------------------


def _build(run_id: str, inputs: _Inputs) -> dict[str, Any]:
    kept = {m["paper"]["key"]: m for m in inputs.kept}
    papers = sorted(kept)
    edges: list[tuple[str, str, str, float]] = [(a, b, "cites", 1.0) for a, b in inputs.cites]
    edges += _pairs(inputs.citers, "co_citation")
    edges += _pairs(inputs.refs, "coupling")

    carried = {key: _keywords(kept[key]["paper"]) for key in papers}
    count: dict[str, int] = defaultdict(int)
    for words in carried.values():
        for w in words:
            count[w] += 1
    common = sorted((w for w, n in count.items() if n >= KEYWORD_MIN), key=lambda w: (-count[w], w))[:KEYWORDS]
    chosen = set(common)
    holders: dict[str, list[str]] = defaultdict(list)
    for key in papers:
        for w in sorted(carried[key] & chosen):
            edges.append((key, "kw:" + w, "has_keyword", 1.0))
            holders[key].append("kw:" + w)
    together: dict[tuple[str, str], int] = defaultdict(int)
    for words in holders.values():
        for i, a in enumerate(words):
            for b in words[i + 1 :]:
                together[(a, b)] += 1
    edges += [(a, b, "keyword_cooccur", float(n))
              for (a, b), n in _strongest({p: n for p, n in together.items() if n >= KEYWORD_MIN})]

    nodes = papers + ["kw:" + w for w in common]
    # The analysis graph: undirected, weighted, keyword co-occurrence left out (the
    # has_keyword edges already carry it).
    adj: dict[str, dict[str, float]] = {n: {} for n in nodes}
    for a, b, kind, w in edges:
        if kind in WEIGHTS and a != b:
            add = WEIGHTS[kind] * min(w, SHARED_CAP)
            adj[a][b] = adj[a].get(b, 0.0) + add
            adj[b][a] = adj[b].get(a, 0.0) + add
    groups = _louvain(nodes, adj)
    # Communities numbered by papers, most first; one without a paper, or a node
    # with no edge, has none.
    members_of: dict[int, list[str]] = defaultdict(list)
    for n in papers:
        if adj[n]:
            members_of[groups[n]].append(n)
    order = sorted(members_of, key=lambda c: (-len(members_of[c]), min(members_of[c])))
    number = {c: i for i, c in enumerate(order)}
    community = {n: number.get(groups[n]) if adj[n] else None for n in nodes}

    paper_adj: dict[str, set[str]] = {p: set() for p in papers}
    out: dict[str, dict[str, float]] = {p: {} for p in papers}
    for a, b, kind, w in edges:
        if kind == "cites":
            out[a][b] = out[a].get(b, 0.0) + WEIGHTS["cites"]
        elif kind in ("co_citation", "coupling"):
            add = WEIGHTS[kind] * min(w, SHARED_CAP)
            out[a][b] = out[a].get(b, 0.0) + add
            out[b][a] = out[b].get(a, 0.0) + add
        else:
            continue
        if a != b:
            paper_adj[a].add(b)
            paper_adj[b].add(a)
    rank = _pagerank(papers, out)
    between = _betweenness(papers, paper_adj)
    degree: dict[str, set[str]] = {n: set() for n in nodes}
    for a, b, _, _ in edges:
        if a != b:
            degree[a].add(b)
            degree[b].add(a)

    ranked = sorted(between.values(), reverse=True)
    least = ranked[max(0, math.ceil(BRIDGES * len(ranked)) - 1)] if ranked else 0.0
    node_docs: list[dict[str, Any]] = []
    for key in papers:
        m, p = kept[key], kept[key]["paper"]
        bridge = (between[key] > 0 and between[key] >= least and community[key] is not None
                  and any(community[o] != community[key] for o in paper_adj[key]))
        node_docs.append({
            "id": key, "kind": "paper", "label": _label(p), "community": community[key],
            "metrics": {"degree": len(degree[key]), "pagerank": round(rank[key], 8),
                        "betweenness": round(between[key], 8), "bridge": bridge},
            "paper": p, "decision": _decision(m), "origin": _origin(m),
        })
    for w in common:
        n = "kw:" + w
        node_docs.append({"id": n, "kind": "keyword", "label": w, "community": community[n],
                          "metrics": {"degree": len(degree[n])}})
    communities = []
    for c in order:
        top = _enriched(members_of[c], carried, count, len(papers))
        communities.append({"id": number[c], "label": top[0] if top else None, "size": len(members_of[c]),
                            "top_keywords": top})
    edge_docs = [{"source": a, "target": b, "kind": kind, "weight": w} for a, b, kind, w in edges]
    edge_docs.sort(key=lambda e: (e["kind"], e["source"], e["target"]))
    years = {k: kept[k]["paper"]["year"] for k in papers if isinstance(kept[k]["paper"].get("year"), int)}
    return {"schema": SCHEMA, "run_id": run_id, "fingerprint": inputs.fingerprint, "built_at": db.now(),
            "nodes": node_docs, "edges": edge_docs, "communities": communities,
            "lineage": _lineage(carried, count, years, community, inputs.cites),
            "main_paths": _main_paths(years, inputs.cites),
            "coverage": {"papers": len(papers), "dated": len(years),
                         "full_citations": sum(1 for k in papers if k in inputs.full)}}


def _lineage(carried: dict[str, set[str]], count: dict[str, int], years: dict[str, int],
             community: dict[str, int | None], cites: list[tuple[str, str]]) -> dict[str, Any]:
    """How the run's main keywords grow out of and cross each other, over time.

    Keywords: the `LINEAGE_KEYWORDS` most common that at least `LINEAGE_MIN` dated
    papers carry, each with its onset (the second-earliest year, so one stray old
    paper does not move it), its last year, its papers per year and the community
    most of its papers are in. `grows_out_of` (child → parent): of the keywords
    that start in an earlier year, the one whose papers the child's papers cite most, by
    citations / sqrt(papers · papers), at least `FLOW_MIN` citations; one parent
    each, so the lineage is a forest; keywords that start the same year are siblings, never parents. `crosses`: the other pairs that share at least
    `CROSS_MIN` papers, the `CROSSINGS` heaviest."""
    holders: dict[str, list[str]] = defaultdict(list)
    for key in sorted(years):
        for w in carried[key]:
            holders[w].append(key)
    words = sorted((w for w, ks in holders.items() if len(ks) >= LINEAGE_MIN and count[w] >= KEYWORD_MIN),
                   key=lambda w: (-len(holders[w]), w))[:LINEAGE_KEYWORDS]
    kw: dict[str, dict[str, Any]] = {}
    for w in words:
        ys = sorted(years[k] for k in holders[w])
        per: dict[int, int] = defaultdict(int)
        for y in ys:
            per[y] += 1
        votes: dict[int, int] = defaultdict(int)
        for k in holders[w]:
            if community.get(k) is not None:
                votes[community[k]] += 1  # type: ignore[index]
        kw[w] = {"id": "kw:" + w, "label": w, "papers": len(ys), "onset": ys[1], "last": ys[-1],
                 "per_year": [[y, per[y]] for y in sorted(per)],
                 "community": min(votes, key=lambda c: (-votes[c], c)) if votes else None}
    on = {w: set(holders[w]) for w in words}
    of: dict[str, set[str]] = defaultdict(set)
    for w in words:
        for k in holders[w]:
            of[k].add(w)
    flow: dict[tuple[str, str], int] = defaultdict(int)
    for a, b in cites:
        # A paper citing work on a keyword it carries itself is not growing out of it.
        for c in of.get(a, ()):
            for p in of.get(b, set()) - of[a]:
                flow[(c, p)] += 1
    edges: list[dict[str, Any]] = []
    parent: dict[str, str] = {}
    for c in words:
        scored = [(-flow[(c, p)] / math.sqrt(len(on[c]) * len(on[p])), p) for p in words
                  if kw[p]["onset"] < kw[c]["onset"] and flow[(c, p)] >= FLOW_MIN]
        if scored:
            parent[c] = min(scored)[1]
            edges.append({"source": "kw:" + c, "target": "kw:" + parent[c], "kind": "grows_out_of",
                          "weight": float(flow[(c, parent[c])])})
    crossing = []
    for i, a in enumerate(words):
        for b in words[i + 1:]:
            n = len(on[a] & on[b])
            if n >= CROSS_MIN and parent.get(a) != b and parent.get(b) != a:
                crossing.append((-n, *sorted(("kw:" + a, "kw:" + b))))
    edges += [{"source": a, "target": b, "kind": "crosses", "weight": float(-n)}
              for n, a, b in sorted(crossing)[:CROSSINGS]]
    return {"keywords": [kw[w] for w in words], "edges": edges}


def _main_paths(years: dict[str, int], cites: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """The backbone of the citations: the `MAIN_PATHS` links that the most
    source-to-sink paths run through (search path count, Hummon and Doreian's
    main-path analysis). Knowledge flows from the cited paper to the citing one;
    papers are ordered by year then key, and a link against that order (a paper
    citing a later one, or a cycle) is left out so the flow has no loop. Each
    link: `source` cited, `target` citing, `weight` its count over the largest, 0–1
    (not rounded: a link far below the top may come out 0.0)."""
    order = {k: i for i, k in enumerate(sorted(years, key=lambda k: (years[k], k)))}
    links = sorted({(b, a) for a, b in cites if a in order and b in order and order[b] < order[a]},
                   key=lambda e: (order[e[0]], order[e[1]]))
    ins: dict[str, list[str]] = defaultdict(list)
    outs: dict[str, list[str]] = defaultdict(list)
    for u, v in links:
        outs[u].append(v)
        ins[v].append(u)
    keys = sorted(order, key=order.__getitem__)
    before: dict[str, int] = {}
    for k in keys:
        before[k] = sum(before[u] for u in ins[k]) if ins[k] else 1
    after: dict[str, int] = {}
    for k in reversed(keys):
        after[k] = sum(after[v] for v in outs[k]) if outs[k] else 1
    # Path counts grow fast; they stay exact ints here and leave as shares.
    ranked = sorted(((before[u] * after[v], u, v) for u, v in links), key=lambda t: (-t[0], t[1], t[2]))[:MAIN_PATHS]
    top = ranked[0][0] if ranked else 1
    return [{"source": u, "target": v, "weight": n / top} for n, u, v in ranked]


def _pairs(works_of: dict[str, set[str]], kind: str) -> list[tuple[str, str, str, float]]:
    """Kept papers that share works (citers or references), weight how many, in
    sorted key order; the strongest (`_strongest`)."""
    by_work: dict[str, list[str]] = defaultdict(list)
    for key in sorted(works_of):
        for w in works_of[key]:
            by_work[w].append(key)
    shared: dict[tuple[str, str], int] = defaultdict(int)
    for keys in by_work.values():
        if len(keys) > HUB:
            continue
        for i, a in enumerate(keys):
            for b in keys[i + 1 :]:
                shared[(a, b)] += 1
    return [(a, b, kind, float(n)) for (a, b), n in _strongest(shared)]


def _strongest(weights: dict[tuple[str, str], int]) -> list[tuple[tuple[str, str], int]]:
    """The pairs, heaviest first, about `EDGE_CAP` of them: when the cap falls among
    pairs of one weight, all of that weight are kept (up to twice the cap) or none,
    so which pairs stay never depends on how their keys sort."""
    pairs = sorted(weights.items(), key=lambda kv: (-kv[1], kv[0]))
    if len(pairs) > EDGE_CAP:
        cut = pairs[EDGE_CAP][1]
        # The tied weight is kept whole while the kind stays under twice the cap,
        # so a kind of mostly one weight (sparse lookups: weight 1) is not emptied.
        tied = sum(1 for p in pairs if p[1] >= cut)
        pairs = pairs[:tied] if tied <= 2 * EDGE_CAP else [p for p in pairs[:EDGE_CAP] if p[1] > cut]
    return pairs


def _label(paper: dict[str, Any]) -> str:
    title = " ".join((paper.get("title") or paper["key"]).split())
    if len(title) > LABEL:
        title = title[: LABEL - 1].rstrip() + "…"
    return f"{title} ({paper['year']})" if paper.get("year") else title


def _enriched(members: list[str], carried: dict[str, set[str]], count: dict[str, int], total: int) -> list[str]:
    """A community's keywords that its papers carry more often than the whole set
    does: by `n · log(share in the community / share overall)`, at least two papers
    (one in a community of fewer than four), and only keywords that at least
    `KEYWORD_MIN` papers of the whole set carry."""
    here: dict[str, int] = defaultdict(int)
    for key in members:
        for w in carried[key]:
            here[w] += 1
    least = 1 if len(members) < 4 else 2
    scored = []
    for w, n in here.items():
        lift = (n / len(members)) / (count[w] / total)
        # A keyword no other paper carries says what one paper is about, not a community.
        if n >= least and count[w] >= KEYWORD_MIN and lift > 1:
            scored.append((-n * math.log(lift), -n, w))
    return [w for _, _, w in sorted(scored)[:TOP_KEYWORDS]]


# --- algorithms ------------------------------------------------------------------------


def _louvain(nodes: list[str], adj: dict[str, dict[str, float]], resolution: float = 1.0) -> dict[str, int]:
    """Louvain modularity communities of an undirected weighted graph, visiting nodes
    in the given order so the result is the same every time: node → community."""
    index = {n: i for i, n in enumerate(nodes)}
    graph: dict[int, dict[int, float]] = {index[n]: {index[m]: w for m, w in adj[n].items()} for n in nodes}
    loops: dict[int, float] = defaultdict(float)
    of = {n: index[n] for n in nodes}
    while True:
        moved, comm = _level(graph, loops, resolution)
        labels = {c: i for i, c in enumerate(sorted(set(comm.values())))}
        comm = {v: labels[c] for v, c in comm.items()}
        of = {n: comm[c] for n, c in of.items()}
        if not moved:
            return of
        merged: dict[int, dict[int, float]] = {c: {} for c in labels.values()}
        merged_loops: dict[int, float] = defaultdict(float)
        for v, w in loops.items():
            merged_loops[comm[v]] += w
        for v, nbrs in graph.items():
            for u, w in nbrs.items():
                if v < u:
                    a, b = comm[v], comm[u]
                    if a == b:
                        merged_loops[a] += w
                    else:
                        merged[a][b] = merged[a].get(b, 0.0) + w
                        merged[b][a] = merged[b].get(a, 0.0) + w
        graph, loops = merged, merged_loops


def _level(
    graph: dict[int, dict[int, float]], loops: dict[int, float], resolution: float
) -> tuple[bool, dict[int, int]]:
    """One Louvain pass: move single nodes to the neighbouring community with the
    best modularity gain until none moves; whether any moved, and node → community."""
    degree = {v: sum(nbrs.values()) + 2 * loops.get(v, 0.0) for v, nbrs in graph.items()}
    two_m = sum(degree.values())
    comm = {v: v for v in graph}
    if two_m == 0:
        return False, comm
    total = dict(degree)
    improved, moved = False, True
    while moved:
        moved = False
        for v in sorted(graph):
            here, k = comm[v], degree[v]
            links: dict[int, float] = defaultdict(float)
            for u, w in graph[v].items():
                links[comm[u]] += w
            total[here] -= k
            best, gain = here, links.get(here, 0.0) - resolution * total[here] * k / two_m
            for c in sorted(links):
                g = links[c] - resolution * total[c] * k / two_m
                if g > gain + 1e-12:
                    best, gain = c, g
            total[best] += k
            if best != here:
                comm[v] = best
                moved = improved = True
    return improved, comm


def _pagerank(nodes: list[str], out: dict[str, dict[str, float]], tol: float = 1e-10) -> dict[str, float]:
    """Weighted PageRank by power iteration; a node with no out-edge spreads its rank
    evenly. Sums to 1."""
    n = len(nodes)
    if n == 0:
        return {}
    rank = dict.fromkeys(nodes, 1.0 / n)
    weight = {v: sum(out[v].values()) for v in nodes}
    for _ in range(200):
        spread = DAMPING * sum(rank[v] for v in nodes if not weight[v]) / n
        new = dict.fromkeys(nodes, (1 - DAMPING) / n + spread)
        for v in nodes:
            if weight[v]:
                share = DAMPING * rank[v] / weight[v]
                for u, w in out[v].items():
                    new[u] += share * w
        done = sum(abs(new[v] - rank[v]) for v in nodes) < n * tol
        rank = new
        if done:
            break
    return rank


def _betweenness(nodes: list[str], adj: dict[str, set[str]]) -> dict[str, float]:
    """Normalized betweenness of an undirected unweighted graph (Brandes), from every
    node, or from `SOURCES` evenly spaced ones and scaled up."""
    n = len(nodes)
    out = dict.fromkeys(nodes, 0.0)
    if n < 3:
        return out
    sources: Iterable[str] = nodes if n <= SOURCES else [nodes[i * n // SOURCES] for i in range(SOURCES)]
    used = 0
    for s in sources:
        used += 1
        stack: list[str] = []
        before: dict[str, list[str]] = defaultdict(list)
        paths = {s: 1}
        dist = {s: 0}
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj[v]:
                if w not in dist:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    paths[w] = paths.get(w, 0) + paths[v]
                    before[w].append(v)
        delta: dict[str, float] = defaultdict(float)
        while stack:
            w = stack.pop()
            for v in before[w]:
                delta[v] += paths[v] / paths[w] * (1 + delta[w])
            if w != s:
                out[w] += delta[w]
    # Each pair is counted from both ends when every node is a source.
    scale = n / used / ((n - 1) * (n - 2))
    return {v: b * scale for v, b in out.items()}
