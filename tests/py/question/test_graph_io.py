"""The graph's other shapes (#57): the compact form and GraphML."""

import xml.etree.ElementTree as ET

from isbe.question import graph_io

DOC = {
    "schema": "question_graph@1",
    "run_id": "run-1",
    "fingerprint": "f",
    "nodes": [
        {"id": "doi:10.1/a", "kind": "paper", "label": "A", "community": 0,
         "metrics": {"degree": 2, "pagerank": 0.6, "betweenness": 0.0, "bridge": False},
         "paper": {"key": "doi:10.1/a", "keys": ["doi:10.1/a"], "title": "Wind \x0bgusts & <ensembles>",
                   "abstract": "long text", "year": 2001},
         "decision": "include", "origin": "seed"},
        {"id": "arxiv:2", "kind": "paper", "label": "B", "community": 0,
         "metrics": {"degree": 2, "pagerank": 0.4, "betweenness": 0.0, "bridge": True},
         "paper": {"key": "arxiv:2", "keys": ["arxiv:2"], "title": "B"}, "decision": "unscreened",
         "origin": "expansion"},
        {"id": "kw:wind", "kind": "keyword", "label": "wind", "community": 0, "metrics": {"degree": 2}},
    ],
    "edges": [
        {"source": "arxiv:2", "target": "doi:10.1/a", "kind": "cites", "weight": 1.0},
        {"source": "arxiv:2", "target": "doi:10.1/a", "kind": "coupling", "weight": 2.5},
        {"source": "doi:10.1/a", "target": "kw:wind", "kind": "has_keyword", "weight": 1.0},
    ],
    "communities": [{"id": 0, "label": "wind", "size": 2, "top_keywords": ["wind"]}],
}


def test_compact_is_the_graph_in_numbers_without_abstracts():
    wire = graph_io.compact(DOC)
    assert wire["edge_kinds"] == ["cites", "coupling", "has_keyword"]
    assert wire["edge_rows"] == [[0, 1, 0, 1], [1, 1, 0, 2.5], [2, 0, 2, 1]]
    assert "abstract" not in wire["nodes"][0]["paper"] and wire["nodes"][2] == DOC["nodes"][2]
    back = graph_io.expand(wire)
    assert back["edges"] == DOC["edges"]
    assert back["communities"] == DOC["communities"] and back["fingerprint"] == "f"


def test_map_form_is_the_kept_papers_communities_and_main_paths():
    doc = {**DOC, "main_paths": [{"source": "arxiv:2", "target": "doi:10.1/a", "weight": 1.0}]}
    m = graph_io.map_form(doc)
    assert set(m) == {"schema", "run_id", "fingerprint", "papers", "communities", "main_paths"}
    assert m["schema"] == "question_graph_map@1" and m["fingerprint"] == DOC["fingerprint"]
    papers = [n for n in DOC["nodes"] if n["kind"] == "paper"]
    assert [row[0] for row in m["papers"]] == [n["id"] for n in papers]
    first = papers[0]
    assert m["papers"][0] == [first["id"], first["community"], first["paper"]["year"], first["paper"]["title"],
                              first["paper"].get("cited_by_count") or 0, first["metrics"]["pagerank"]]
    assert m["main_paths"] == doc["main_paths"] and m["communities"] == DOC["communities"]
    # Excluded papers are not drawn, so they are not sent.
    gone = {**DOC, "nodes": [{**papers[0], "decision": "exclude"}, *DOC["nodes"][1:]]}
    assert papers[0]["id"] not in [row[0] for row in graph_io.map_form(gone)["papers"]]


def test_graphml_carries_metrics_and_marks_similarity_symmetric():
    ns = {"g": graph_io.NS}
    root = ET.fromstring(graph_io.graphml(DOC))
    g = root.find("g:graph", ns)
    assert g is not None and g.get("edgedefault") == "directed"
    a = g.find("g:node[@id='doi:10.1/a']", ns)
    data = {d.get("key"): d.text for d in a.findall("g:data", ns)}
    # Escaped, and the vertical tab XML cannot carry is gone.
    assert data["title"] == "Wind gusts & <ensembles>"
    assert data["doi"] == "10.1/a" and data["pagerank"] == "0.6" and data["bridge"] == "false"
    assert data["community_label"] == "wind" and data["year"] == "2001"
    edges = g.findall("g:edge", ns)
    # No per-edge `directed`: networkx refuses a directed graph with undirected edges.
    assert [e.get("directed") for e in edges] == [None, None, None]
    sym = [next(d.text for d in e.findall("g:data", ns) if d.get("key") == "symmetric") for e in edges]
    assert sym == ["false", "true", "false"]
    kw = {d.get("key"): d.text for d in g.find("g:node[@id='kw:wind']", ns).findall("g:data", ns)}
    assert kw == {"kind": "keyword", "label": "wind", "community": "0", "community_label": "wind", "degree": "2"}
