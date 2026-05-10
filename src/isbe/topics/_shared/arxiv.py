"""Generic arxiv collector — driven by topic.yaml config.

Reads `topic.yaml` of the given topic_id, builds an arxiv API search
(category AND keyword filter), upserts results into the shared `papers` table.

Schema expected in topic.yaml:

    arxiv:
      categories: [cs.LG]              # one or more arxiv subject categories
      include_keywords: [nowcasting]   # OR-joined; matched against title+abstract
      max_results: 50                  # default per fetch
"""

from datetime import UTC, datetime

import feedparser
import httpx
from prefect import flow, task

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.nowcasting.facts import Paper  # shared papers table
from isbe.topics.registry import default_topics_root, load_topic_config


def _build_query(categories: list[str], keywords: list[str]) -> str:
    cat_clause = "+OR+".join(f"cat:{c}" for c in categories)
    kw_clause = "+OR+".join(f"abs:{k.replace(' ', '+')}" for k in keywords)
    return f"({cat_clause})+AND+({kw_clause})"


def _arxiv_url(categories: list[str], keywords: list[str], max_results: int) -> str:
    return (
        "https://export.arxiv.org/api/query"
        f"?search_query={_build_query(categories, keywords)}"
        f"&sortBy=submittedDate&sortOrder=descending&max_results={max_results}"
    )


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def parse_atom_entry(entry: dict) -> Paper:
    raw_id = entry["id"]
    arxiv_id = raw_id.rsplit("/", 1)[-1].split("v")[0]
    authors = [a["name"] for a in entry.get("authors", [])]
    tags = entry.get("tags", [])
    primary = tags[0]["term"] if tags else "unknown"
    alt = next(
        (link["href"] for link in entry.get("links", []) if link.get("rel") == "alternate"),
        "",
    )
    return Paper(
        arxiv_id=arxiv_id,
        title=entry["title"].strip(),
        authors=authors,
        abstract=entry.get("summary", "").strip(),
        primary_category=primary,
        submitted_at=_parse_iso(entry["published"]),
        updated_at=_parse_iso(entry.get("updated", entry["published"])),
        pdf_uri=None,
        source_url=alt or raw_id,
    )


def upsert_papers(session, papers: list[Paper]) -> int:
    n = 0
    for p in papers:
        if session.get(Paper, p.arxiv_id) is None:
            session.add(p)
            n += 1
    session.commit()
    return n


@task
def fetch_arxiv_atom(categories: list[str], keywords: list[str], max_results: int) -> list[dict]:
    url = _arxiv_url(categories, keywords, max_results)
    resp = httpx.get(url, timeout=30.0)
    resp.raise_for_status()
    return feedparser.parse(resp.text).entries


@flow(name="arxiv-collector")
def arxiv_collector(topic_id: str, max_results: int | None = None) -> int:
    """Fetch arxiv listing for one topic per its topic.yaml `arxiv:` block.

    Returns count of newly-inserted papers.
    """
    cfg = load_topic_config(default_topics_root(), topic_id)
    arxiv_cfg = cfg.get("arxiv")
    if not arxiv_cfg:
        raise ValueError(f"topic {topic_id} has no `arxiv:` block in topic.yaml")
    categories = arxiv_cfg["categories"]
    keywords = arxiv_cfg["include_keywords"]
    n_results = max_results if max_results is not None else int(arxiv_cfg.get("max_results", 50))

    with topic_run(topic_id, "arxiv-collector") as run:
        entries = fetch_arxiv_atom(categories, keywords, n_results)
        papers = [parse_atom_entry(e) for e in entries]
        Session = make_session_factory()
        with Session() as s:
            n = upsert_papers(s, papers)
        run.payload["fetched"] = len(entries)
        run.payload["new_papers"] = n
        run.payload["categories"] = categories
        run.payload["keywords"] = keywords
        return n
