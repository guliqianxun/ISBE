"""A plan's concepts → its search queries, written for one search engine.

The model names concepts (`question_plan@1` `concepts`): each a role and the
interchangeable terms papers use for it. The queries are composed here from
fixed patterns, so their syntax follows the engine rather than the model. They
come in two tiers:

- **Narrow** (priority 1), for precision: all the subject concepts together;
  with each method or context concept; the landmarks, a few at a time, with
  the first subject concept.
- **Broad** (priority 2), for recall, deeper (`Engine.broad_limit` results):
  each subject concept alone when there are several; the landmarks on their
  own; each method with each subject concept separately; all the subjects
  with each outcome concept.

A query that must contain every subject concept misses every paper that words
one of them differently; the broad tier is what reaches those. Screening
(`question/screen`) sorts out the noise it brings.

An `Engine` says how a concept's terms are written. OpenAlex (through essaies)
takes upper-case `OR` inside parentheses and nested groups, and folds plurals
and other endings in plain words but not in a quoted phrase, so a term of
several words is written as a group of its words, `(physics informed neural
network)`, never quoted. essaies' arXiv index (`/api/search`, SQLite FTS5 with
Porter stemming) reads the same OR groups, but needs an explicit `AND` between
two concepts when one is a group, and reads a word with a hyphen or other
symbol in it as syntax, so such a word is quoted on its own:
`("physics-informed" neural network) AND PDE`. An engine without boolean search gets
each concept's first term as plain words.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ROLES = ("subject", "method", "context", "outcome", "landmark")
# Landmarks per query: a longer OR group spreads one query's results thin.
LANDMARKS_PER_QUERY = 3

# Characters a search engine may read as syntax; terms are plain words.
_SYNTAX = re.compile(r"[\"()?？!！。:：;；,，]")
_OR = re.compile(r"\b(?:OR|AND|NOT)\b")
# A word FTS5 reads as one plain token; any other is quoted for it.
_BAREWORD = re.compile(r"\w+")


@dataclass(frozen=True)
class Engine:
    name: str
    # OR groups in parentheses, nested groups allowed.
    boolean: bool
    # How a term of several words is written inside an OR group: "words" as a
    # group of its words (each matched with its endings folded), "quote" as an
    # exact phrase.
    phrase: str = "words"
    # Words with a symbol in them (`physics-informed`, `U-Net`) quoted one by one.
    quote_symbols: bool = False
    # What joins the concepts of a query.
    joiner: str = " "
    max_query: int = 200
    # Alternatives kept per concept before the query is shortened further.
    max_alternatives: int = 6
    # Results asked of a broad query; narrow ones take the search stage's default.
    broad_limit: int = 200


OPENALEX = Engine("openalex", boolean=True)
ARXIV = Engine("arxiv", boolean=True, quote_symbols=True, joiner=" AND ", max_query=300)
PLAIN = Engine("plain", boolean=False)
ENGINES = {engine.name: engine for engine in (OPENALEX, ARXIV, PLAIN)}
# The engine a question run searches when none is asked for. It was essaies' arXiv
# index while essaies could not reach the OpenAlex API (2026-10-23 to 2026-10-26).
DEFAULT = OPENALEX.name
# The engines a question run may be asked to search, as the console offers them.
QUESTION_ENGINES = (OPENALEX.name, ARXIV.name)


def term(text: str) -> str:
    """A concept term as plain words: no quotes, brackets, boolean words or
    punctuation that an engine reads as syntax; spaces collapsed."""
    return " ".join(_OR.sub(" ", _SYNTAX.sub(" ", text)).split())


def render(terms: Iterable[str], engine: Engine, limit: int | None = None) -> str:
    """One concept: its single term as plain words, or its terms as an OR group."""
    kept: list[str] = []
    seen: set[str] = set()
    for text in terms:
        clean = term(text)
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            kept.append(clean)
    kept = kept[: limit or engine.max_alternatives]
    if not kept:
        return ""
    if engine.quote_symbols:
        kept = [" ".join(w if _BAREWORD.fullmatch(w) else f'"{w}"' for w in t.split()) for t in kept]
    if not engine.boolean or len(kept) == 1:
        return kept[0]
    parts = [
        t if " " not in t else (f"({t})" if engine.phrase == "words" else f'"{t}"') for t in kept
    ]
    return "(" + " OR ".join(parts) + ")"


def compose(concepts: list[Mapping[str, Any]], engine: Engine = OPENALEX, limit: int = 16) -> list[dict[str, Any]]:
    """The queries for `concepts` on `engine`, at most `limit`, narrow tier first;
    none when there is no subject concept."""
    by_role: dict[str, list[list[str]]] = {role: [] for role in ROLES}
    for concept in concepts:
        by_role[concept["role"]].append(list(concept["terms"]))
    subjects = by_role["subject"]
    if not subjects:
        return []
    landmarks = [t for terms in by_role["landmark"] for t in terms]
    chunks = [landmarks[i : i + LANDMARKS_PER_QUERY] for i in range(0, len(landmarks), LANDMARKS_PER_QUERY)]
    several = len(subjects) > 1
    # (concept groups, layer, priority, broad), in the order queries are kept.
    plans: list[tuple[list[list[str]], str, int, bool]] = [(subjects, "canonical", 1, False)]
    plans += [([*subjects, other], "mainstream", 1, False) for other in by_role["method"] + by_role["context"]]
    plans += [([chunk, subjects[0]], "method_centric", 1, False) for chunk in chunks]
    plans += [([subject], "canonical", 2, True) for subject in subjects if several]
    plans += [([chunk], "method_centric", 2, True) for chunk in chunks]
    plans += [
        ([subject, method], "mainstream", 2, True)
        for method in by_role["method"] for subject in subjects if several
    ]
    plans += [([*subjects, outcome], "mainstream", 2, True) for outcome in by_role["outcome"]]

    queries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for groups, layer, priority, broad in plans:
        text = _fit(groups, engine)
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        query = {"id": f"q{len(queries) + 1}", "query": text, "layer": layer, "priority": priority}
        if broad:
            query["limit"] = engine.broad_limit
        queries.append(query)
        if len(queries) == limit:
            break
    return queries


def _fit(groups: list[list[str]], engine: Engine) -> str:
    """The groups joined, with fewer alternatives per concept until the query fits."""
    for alternatives in range(engine.max_alternatives, 0, -1):
        text = engine.joiner.join(filter(None, (render(g, engine, alternatives) for g in groups)))
        if len(text) <= engine.max_query:
            return text
    return ""
