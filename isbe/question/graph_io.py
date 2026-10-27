"""The knowledge graph of a run in other shapes (#57): the compact form the run page
reads, and the GraphML and JSON downloads. The graph itself is `graph.py`'s.

The run page draws every node and edge, so it reads the whole graph; spelled out as
`question_graph@1` that is about 1.3 MB for the 217-paper demo run, two thirds of it
edges that repeat two paper keys each and a third abstracts the page never shows.
`compact` writes each edge as numbers (`[kind, source, target, weight]`, indexes into
`edge_kinds` and `nodes`) and leaves the abstracts out; `expand` is its inverse, and
the client's `decode` in frontend/src/question/graph/model.ts reads it the same way.
"""

import xml.etree.ElementTree as ET
from typing import Any

# Paper record fields the run page has no use for.
DROPPED = ("abstract",)
# Edge kinds that join two things the same way round; the rest point from source to target.
UNDIRECTED = frozenset({"co_citation", "coupling", "keyword_cooccur"})


def compact(doc: dict[str, Any]) -> dict[str, Any]:
    """`doc` (`question_graph@1`) with its edges as numbers and its paper records
    without abstracts."""
    index = {n["id"]: i for i, n in enumerate(doc["nodes"])}
    kinds = sorted({e["kind"] for e in doc["edges"]})
    kind_of = {k: i for i, k in enumerate(kinds)}
    rows = [[kind_of[e["kind"]], index[e["source"]], index[e["target"]], _number(e["weight"])] for e in doc["edges"]]
    nodes = [{**n, "paper": {k: v for k, v in n["paper"].items() if k not in DROPPED}} if "paper" in n else n
             for n in doc["nodes"]]
    out = {k: v for k, v in doc.items() if k != "edges"}
    return {**out, "nodes": nodes, "edge_kinds": kinds, "edge_rows": rows}


# The map form is a shape of its own, not `question_graph@1`.
MAP_SCHEMA = "question_graph_map@1"


def map_form(doc: dict[str, Any]) -> dict[str, Any]:
    """What Home's map of a selected galaxy draws (#115): the kept papers as rows
    `[id, community, year, title, cited_by_count, pagerank]`, the communities and the
    citation main paths; no edges, keywords or other paper fields."""
    papers = []
    for n in doc["nodes"]:
        if n["kind"] != "paper" or n.get("decision") == "exclude":
            continue
        rec = n.get("paper") or {}
        papers.append([n["id"], n.get("community"), rec.get("year"), rec.get("title") or n["label"],
                       rec.get("cited_by_count") or 0, _number(n.get("metrics", {}).get("pagerank") or 0)])
    return {"schema": MAP_SCHEMA, "run_id": doc["run_id"], "fingerprint": doc["fingerprint"],
            "papers": papers, "communities": doc["communities"], "main_paths": doc.get("main_paths", [])}


def expand(wire: dict[str, Any]) -> dict[str, Any]:
    """The edges of a `compact` graph spelled out again (its paper records stay short)."""
    ids = [n["id"] for n in wire["nodes"]]
    kinds = wire["edge_kinds"]
    out = {k: v for k, v in wire.items() if k not in ("edge_kinds", "edge_rows")}
    out["edges"] = [{"source": ids[s], "target": ids[t], "kind": kinds[k], "weight": float(w)}
                    for k, s, t, w in wire["edge_rows"]]
    return out


def _number(w: float) -> float | int:
    # Most weights are whole counts: 1 reads shorter than 1.0.
    return int(w) if float(w).is_integer() else w


# --- GraphML -----------------------------------------------------------------------------

NS = "http://graphml.graphdrawing.org/xmlns"
# (id, for, attr.name, attr.type): what each node and edge carries, as Gephi and
# Cytoscape read it.
KEYS = [
    ("kind", "node", "kind", "string"),
    ("label", "node", "label", "string"),
    ("community", "node", "community", "int"),
    ("community_label", "node", "community_label", "string"),
    ("pagerank", "node", "pagerank", "double"),
    ("betweenness", "node", "betweenness", "double"),
    ("degree", "node", "degree", "int"),
    ("bridge", "node", "bridge", "boolean"),
    ("title", "node", "title", "string"),
    ("year", "node", "year", "int"),
    ("cited_by_count", "node", "cited_by_count", "int"),
    ("doi", "node", "doi", "string"),
    ("url", "node", "url", "string"),
    ("decision", "node", "decision", "string"),
    ("origin", "node", "origin", "string"),
    ("ekind", "edge", "kind", "string"),
    ("weight", "edge", "weight", "double"),
    ("symmetric", "edge", "symmetric", "boolean"),
]


ET.register_namespace("", NS)


def graphml(doc: dict[str, Any]) -> bytes:
    """`doc` as GraphML: one directed graph; nodes carry their metrics and the paper's
    main fields. The similarity edges (`UNDIRECTED`) carry `symmetric` true rather
    than `directed="false"`: networkx refuses a directed graph with undirected edges."""
    root = ET.Element(f"{{{NS}}}graphml")
    for key, for_, name, kind in KEYS:
        ET.SubElement(root, f"{{{NS}}}key", {"id": key, "for": for_, "attr.name": name, "attr.type": kind})
    g = ET.SubElement(root, f"{{{NS}}}graph", {"id": _clean(doc["run_id"]), "edgedefault": "directed"})
    labels = {c["id"]: c.get("label") for c in doc["communities"]}
    for n in doc["nodes"]:
        el = ET.SubElement(g, f"{{{NS}}}node", {"id": _clean(n["id"])})
        m, p = n.get("metrics", {}), n.get("paper") or {}
        dois = [k.split(":", 1)[1] for k in p.get("keys") or [] if k.startswith("doi:")]
        values = {
            "kind": n["kind"], "label": n["label"], "community": n.get("community"),
            "community_label": labels.get(n.get("community")), "pagerank": m.get("pagerank"),
            "betweenness": m.get("betweenness"), "degree": m.get("degree"), "bridge": m.get("bridge"),
            "title": p.get("title"), "year": p.get("year"), "cited_by_count": p.get("cited_by_count"),
            "doi": dois[0] if dois else None, "url": p.get("url"), "decision": n.get("decision"),
            "origin": n.get("origin"),
        }
        _data(el, values)
    for i, e in enumerate(doc["edges"]):
        attrs = {"id": f"e{i}", "source": _clean(e["source"]), "target": _clean(e["target"])}
        _data(ET.SubElement(g, f"{{{NS}}}edge", attrs),
              {"ekind": e["kind"], "weight": e["weight"], "symmetric": e["kind"] in UNDIRECTED})
    ET.indent(root, space=" ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True) + b"\n"


def _data(el: ET.Element, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            continue
        text = ("true" if value else "false") if isinstance(value, bool) else _clean(str(value))
        ET.SubElement(el, f"{{{NS}}}data", {"key": key}).text = text


def _clean(text: str) -> str:
    # XML 1.0 cannot carry most control characters, even escaped; titles from
    # OpenAlex occasionally hold one.
    return "".join(c for c in text if c in "\t\n\r" or (0x20 <= ord(c) < 0xD800) or (0xE000 <= ord(c) <= 0xFFFD)
                   or ord(c) >= 0x10000)
