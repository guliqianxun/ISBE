"""A research question → its search plan (`question_plan@1`). Sole writer of
`question_plans`.

`generate` asks the model (`prompts/<lang>/question_plan.md`) for the plan: an
English restatement, the question's concepts with their terms, a year range and
screening criteria; the queries are composed from the concepts for the search
engine the run searches (`compose`; the plan's `engine`), and the term fields and
keyword layers filled from them. The reply is
parsed leniently (a Markdown fence, `<think>` text or a reply cut off at its
token limit are tolerated), then checked. A reply with problems gets one repair
call that lists them; a plan still broken after that, a refusal or a failed
call falls back to the question itself as the only query, with the reason in
`rationale`. So does a missing model configuration (`load_model`). A model
that cannot be reached at all (no connection) is not a plan problem but an
outage: `generate` raises `ModelUnreachable` and the run fails with that reason
instead of searching with the bare question. So does a model that refuses the
call (401/403, `ModelRejected`): a wrong key does not mend itself.

`load_model` gives question runs a model that fails fast: at most
`QUESTION_TIMEOUT` seconds a call (less when `ISBE_LLM_TIMEOUT_S` is lower), and
a refused connection is not retried (docs/standards/backend.md B-U1, B-U4). The model
calls run as one `question_plan` job of the `runs` ledger and are logged in
`llm_calls` like every other call; the plan's `llm` field sums them.

Plans are versioned per question run: `generate` and `edit` add the next
version and never change an earlier one. `edit` takes a person's changes to a
version, checks them strictly and saves them as a `human` version whose
`parent_version` is the one edited; a rerun uses the version it is given.

Errors the user can act on are `core.errors.HttpError`. Question text, model
replies and edits are untrusted: they are stored and shown as text only.
"""

import dataclasses
import json
import re
import sqlite3
from collections.abc import Mapping
from typing import Any

from isbe.core import assets, db, http, jsonfmt, settings, upstreams
from isbe.core.errors import HttpError, describe
from isbe.core.llm import Llm, LlmError, Prompt, Refused, Tier, describe_failure
from isbe.question import compose

SCHEMA = "question_plan@1"
LAYERS = ("canonical", "mainstream", "emerging", "method_centric", "cross_domain")
# The layer of the fallback query, which is the question as asked.
ORIGINAL = "original"
TERM_FIELDS = ("subject_terms", "method_terms", "outcome_terms")
# What a person may change; the rest of a version is fixed by the run.
EDITABLE = (
    "reformulation", "concepts", *TERM_FIELDS, "keyword_layers", "queries", "year_from", "year_to",
    "criteria", "rationale",
)
LANGS = ("zh", "en")

MAX_QUESTION = 2000
MAX_QUERIES = 16
MAX_QUERY = 200
# The most results one query may ask for.
MAX_LIMIT = 500
MAX_TERMS = 6
MAX_TERM = 120
MAX_CONCEPTS = 10
# Fewer composed queries than this is worth one repair call.
MIN_COMPOSED = 4
MAX_CONCEPT_TERMS = 6
MAX_CRITERIA = 6
MAX_TEXT = 600
# The longest phrase a model's query keeps in quotes outside an OR group.
PHRASE_WORDS = 3
# The plan is ~1k tokens; the rest is room for a model that thinks before answering
# (Strata's Qwen spends 1–6k tokens on reasoning, which counts against this).
PLAN_TOKENS = 32768
# How much of a broken reply the repair call quotes back.
REPAIR_QUOTE = 12000
# A cut-off reply is closed after one of its last complete values; earlier cuts lose too much.
_CLOSE_TRIES = 200

_RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_LOCAL_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_CJK = re.compile(r"[⺀-鿿가-힯豈-﫿＀-￯]")
# Punctuation that breaks a full-text search (essaies passes a "?" on to OpenAlex,
# which rejects it); Chinese full stops and colons count too.
_BREAKS = re.compile(r"[?？!！。:：;；]")
_QUOTED = re.compile(r'"([^"]*)"')
_GROUP = re.compile(r"\([^()]*\)")
_OR = re.compile(r"\s+or\s+", re.IGNORECASE)
_PIECE = re.compile(r'"[^"]*"|\([^()]*\)|[^\s"()]+')
_ABBREVIATION = re.compile(r"[A-Z][A-Z0-9]{1,5}s?")
_TERM_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_THINK = re.compile(r"<think>.*?(</think>|$)", re.DOTALL)
_FENCE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_WORD = re.compile(r"[a-z0-9]{2,}")
# Words that carry no subject: a query sharing only these with the plan's terms
# is not anchored on the question.
_GENERIC = frozenset(
    "a an the of in on for with and or to by at from as is are via using based into over "
    "between its their this that which than such can may approach method methods model "
    "models study studies analysis review prediction estimation detection classification "
    "optimization evaluation assessment framework system systems learning network networks "
    "data driven novel new improved performance application applications technique "
    "techniques algorithm deep machine neural".split()
)

_REPAIR = """Your previous reply could not be used as the plan.

Problems:
{problems}

Your previous reply:
{reply}

Answer again with the complete corrected JSON plan only."""


# The longest one model call of a question run may take: a plan or a screening
# batch takes seconds to a minute on Strata; reports keep the long default.
QUESTION_TIMEOUT = 300
# Question work retries a failed model call once, after 5 s: a person is waiting.
QUESTION_WAITS = (5,)


class _Unusable(Exception):
    """The model gave no usable plan; the message says why."""


class ModelUnreachable(HttpError):
    """The model could not be reached at all, so no plan was written; `detail` is why,
    without the call's purpose or model id."""

    def __init__(self, detail: str, cause: str | None = None) -> None:
        super().__init__(503, (
            f"The model could not be reached ({detail}); check that it is running and that "
            "ISBE_LLM_BASE_URL points to it, then try again"))
        self.detail = f"The model could not be reached: {cause or detail}"


class ModelRejected(HttpError):
    """The model refused the call (401/403): the key or the model is wrong, and
    every further call would be refused too, so no plan was written."""

    def __init__(self, detail: str, cause: str | None = None) -> None:
        super().__init__(503, (
            f"The model refused the request ({detail}); check the API key and the model "
            "names in the service settings, then try again"))
        self.detail = f"The model refused the request: {cause or detail}"


def load_model() -> tuple[Llm | None, str]:
    """The configured model for question work, or None and why it cannot be used
    (for the fallback); see the module docstring for how it fails fast."""
    try:
        llm = Llm.from_env()
    except LlmError as error:
        return None, describe(error)
    configured = settings.get("ISBE_LLM_TIMEOUT_S")
    timeout = min(QUESTION_TIMEOUT, int(configured) if configured else settings.LLM_TIMEOUT)
    return dataclasses.replace(llm, http=http.Http(timeout), retry_connect=False, waits=QUESTION_WAITS), ""


def generate(
    conn: sqlite3.Connection,
    llm: Llm | None,
    run_id: str,
    question: str,
    lang: str,
    *,
    year_from: int | None = None,
    year_to: int | None = None,
    unavailable: str = "no model is configured",
    engine: compose.Engine = compose.OPENALEX,
) -> dict[str, Any]:
    """Plan `question` for question run `run_id`, its queries written for `engine`, and
    save it as the run's next version. Years the person set win over the model's.
    With no `llm`, the plan is the fallback, and `unavailable` says why in its rationale."""
    _check_run_id(run_id)
    question = _question(question)
    if lang not in LANGS:
        raise HttpError(400, "The question language must be zh or en")
    years = _years(year_from, year_to)
    if llm is None:
        return _save(conn, _fallback(run_id, question, lang, years, unavailable, engine), None)

    planned: dict[str, Any] = {}

    def work(ledger: str, stats: dict[str, Any]) -> None:
        planned["ledger"] = ledger
        try:
            planned["fields"] = _plan(conn, llm, ledger, question, lang, years, stats, engine)
        except _Unusable as why:
            stats["fallback"] = str(why)
            planned["why"] = str(why)

    db.job(conn, "question_plan", None, work)
    ledger = planned["ledger"]
    if "fields" not in planned:
        plan = _fallback(run_id, question, lang, years, planned["why"], engine)
    else:
        plan = _document(run_id, "model", question, lang, planned["fields"], engine)
    usage = _usage(conn, ledger)
    if usage is not None:
        plan["llm"] = usage
    return _save(conn, plan, ledger)


def edit(
    conn: sqlite3.Connection, run_id: str, parent_version: int, changes: Mapping[str, Any]
) -> dict[str, Any]:
    """Save a person's `changes` (any of `EDITABLE`) to version `parent_version` as the
    run's next version, or `HttpError(400)` naming every problem."""
    parent = get(conn, run_id, parent_version)
    fixed = sorted(set(changes) - set(EDITABLE))
    if fixed:
        raise HttpError(400, f"These plan fields cannot be edited: {', '.join(fixed)}")
    merged = {name: parent[name] for name in EDITABLE if name in parent} | dict(changes)
    if "concepts" in changes and "queries" not in changes:
        # New concepts, the queries composed again from them.
        merged.pop("queries", None)
    engine = compose.ENGINES[parent.get("engine", compose.OPENALEX.name)]
    fields, problems, _ = _fields(merged, strict=True, engine=engine)
    if problems:
        raise HttpError(400, "The plan cannot be saved: " + "; ".join(problems))
    plan = _document(run_id, "human", parent["question"], parent["lang"], fields, engine)
    plan["parent_version"] = parent["version"]
    return _save(conn, plan, None)


def adopt(
    conn: sqlite3.Connection, run_id: str, parent: Mapping[str, Any], changes: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Another run's plan `parent`, with a person's `changes`, as the first version of
    run `run_id` (a rerun); `HttpError(400)` naming every problem."""
    _check_run_id(run_id)
    changes = dict(changes or {})
    fixed = sorted(set(changes) - set(EDITABLE))
    if fixed:
        raise HttpError(400, f"These plan fields cannot be edited: {', '.join(fixed)}")
    merged = {name: parent[name] for name in EDITABLE if name in parent} | changes
    if "concepts" in changes and "queries" not in changes:
        merged.pop("queries", None)
    engine = compose.ENGINES[parent.get("engine", compose.OPENALEX.name)]
    fields, problems, _ = _fields(merged, strict=True, engine=engine)
    if problems:
        raise HttpError(400, "The plan cannot be saved: " + "; ".join(problems))
    source = "human" if changes else parent["source"]
    return _save(conn, _document(run_id, source, parent["question"], parent["lang"], fields, engine), None)


def get(conn: sqlite3.Connection, run_id: str, version: int | None = None) -> dict[str, Any]:
    """Version `version` of the run's plan, the latest when None, or `HttpError(404)`."""
    _check_run_id(run_id)
    if version is None:
        row = conn.execute(
            "SELECT plan FROM question_plans WHERE run_id = ? ORDER BY version DESC LIMIT 1",
            (run_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT plan FROM question_plans WHERE run_id = ? AND version = ?", (run_id, version)
        ).fetchone()
    if row is None:
        raise HttpError(404, "No such plan version" if version is not None else "No plan for this run")
    return jsonfmt.loads(row[0])


def versions(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Every version of the run's plan, oldest first, without the plan itself."""
    _check_run_id(run_id)
    rows = conn.execute(
        "SELECT version, parent_version, source, created_at FROM question_plans "
        "WHERE run_id = ? ORDER BY version",
        (run_id,),
    ).fetchall()
    return [
        {"version": v, "parent_version": p, "source": s, "created_at": c} for v, p, s, c in rows
    ]


# --- the model ---------------------------------------------------------------------------


def user_message(question: str, years: tuple[int | None, int | None]) -> str:
    year_from, year_to = years
    if year_from is None and year_to is None:
        span = "not set"
    else:
        span = f"{year_from or 'any'} to {year_to or 'any'}"
    return f"Question: {question}\nPublication years set by the user: {span}"


def _plan(
    conn: sqlite3.Connection,
    llm: Llm,
    ledger: str,
    question: str,
    lang: str,
    years: tuple[int | None, int | None],
    stats: dict[str, Any],
    engine: compose.Engine,
) -> dict[str, Any]:
    """The checked plan fields, after at most one repair call, or `_Unusable`."""
    system = assets.text("prompts", lang, "question_plan.md")
    user = user_message(question, years)
    text, cut = _call(conn, llm, ledger, Prompt("question_plan", Tier.SMART, system, user, PLAN_TOKENS))
    fields, problems, loose = _parse(text, cut, engine)
    if problems or loose:
        stats["repaired"] = True
        listed = "\n".join(f"- {p}" for p in problems + loose)
        repair = user + "\n\n" + _REPAIR.format(problems=listed, reply=text[:REPAIR_QUOTE])
        try:
            text, cut = _call(
                conn, llm, ledger, Prompt("question_plan", Tier.SMART, system, repair, PLAN_TOKENS)
            )
        except _Unusable:
            if problems:
                raise
        else:
            fixed, fixed_problems, fixed_loose = _parse(text, cut, engine)
            if not fixed_problems:
                fields, problems, loose = fixed, [], fixed_loose
            elif problems:
                raise _Unusable(f"the model's plan is unusable after a repair: {fixed_problems[0]}")
    # Queries that still miss the subject are dropped, unless that leaves none.
    vocabulary = _vocabulary(fields)
    anchored = [q for q in fields["queries"] if _anchored(q["query"], vocabulary)]
    if anchored and len(anchored) < len(fields["queries"]):
        stats["unanchored_dropped"] = len(fields["queries"]) - len(anchored)
        fields["queries"] = anchored
    year_from, year_to = years
    if year_from is not None or year_to is not None:
        fields["year_from"], fields["year_to"] = year_from, year_to
    return fields


def _call(conn: sqlite3.Connection, llm: Llm, ledger: str, prompt: Prompt) -> tuple[str, bool]:
    """The reply text and whether it was cut off at its token limit (the parser
    closes what it can); a refusal or a failed call is `_Unusable`."""
    logged = _calls(conn, ledger)
    try:
        return llm.complete(conn, ledger, prompt).text, False
    except Refused as error:
        raise _Unusable(describe(error)) from None
    except LlmError as error:
        if upstreams.unreachable(error):
            raise ModelUnreachable(describe(error), describe_failure(upstreams.root(error))) from None
        if upstreams.rejected(error):
            raise ModelRejected(describe(error), describe_failure(upstreams.root(error))) from None
        if _calls(conn, ledger) > logged:
            # Logged but rejected, i.e. cut off: the partial text is in llm_calls.
            row = conn.execute(
                "SELECT response FROM llm_calls WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
                (ledger,),
            ).fetchone()
            return row[0], True
        raise _Unusable(describe(error)) from None


def _calls(conn: sqlite3.Connection, ledger: str) -> int:
    return conn.execute("SELECT count(*) FROM llm_calls WHERE run_id = ?", (ledger,)).fetchone()[0]


def _usage(conn: sqlite3.Connection, ledger: str) -> dict[str, Any] | None:
    """The planning job's model calls summed (`llm_usage`), or None when there were none."""
    rows = conn.execute(
        "SELECT model, tokens_in, tokens_out, ms FROM llm_calls WHERE run_id = ? ORDER BY rowid",
        (ledger,),
    ).fetchall()
    if not rows:
        return None
    return {
        "model": rows[-1][0],
        "input_tokens": sum(r[1] or 0 for r in rows),
        "output_tokens": sum(r[2] or 0 for r in rows),
        "duration_ms": sum(r[3] or 0 for r in rows),
        "calls": len(rows),
    }


def _parse(
    text: str, cut: bool = False, engine: compose.Engine = compose.OPENALEX
) -> tuple[dict[str, Any], list[str], list[str]]:
    try:
        raw = extract_json(text)
    except ValueError:
        problems = ["the reply is not a JSON object"]
        if cut:
            problems.append("the reply was cut off at the output limit; write a shorter plan")
        return {}, problems, []
    fields, problems, loose = _fields(raw, strict=False, engine=engine)
    if cut:
        loose.append("the reply was cut off at the output limit; write a shorter plan")
    return fields, problems, loose


def extract_json(text: str) -> Any:
    """The JSON object in a model reply: as is, inside a Markdown fence, between the
    first `{` and the last `}`, or a cut-off object closed again. `ValueError` if none."""
    clean = _THINK.sub("", text).strip() or text.strip()
    candidates = [clean]
    candidates += [m[1] for m in _FENCE.finditer(clean)]
    start, end = clean.find("{"), clean.rfind("}")
    if start != -1 and end > start:
        candidates.append(clean[start : end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    if start != -1:
        closed = close_truncated(clean[start:])
        if closed is not None:
            return closed
    raise ValueError("no JSON object in the reply")


def close_truncated(fragment: str) -> dict[str, Any] | None:
    """A JSON object cut off mid-way, closed after its last complete value, or None."""
    stack: list[str] = []
    in_string = escape = False
    # Positions right after a complete value inside a container, with the open
    # brackets at that point: the places the fragment can be cut and closed.
    cuts: list[tuple[int, str]] = []
    for i, c in enumerate(fragment):
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
                cuts.append((i + 1, "".join(stack)))
            continue
        if c == '"':
            in_string = True
        elif c in "{[":
            stack.append("}" if c == "{" else "]")
        elif c in "}]":
            if not stack:
                return None
            stack.pop()
            cuts.append((i + 1, "".join(stack)))
            if not stack:
                break
        elif c not in ",:" and not c.isspace():
            cuts.append((i + 1, "".join(stack)))
    for end, open_ in reversed(cuts[-_CLOSE_TRIES:]):
        try:
            value = json.loads(fragment[:end].rstrip().rstrip(",") + open_[::-1])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


# --- checking ----------------------------------------------------------------------------


def _fields(
    raw: Any, *, strict: bool, engine: compose.Engine = compose.OPENALEX
) -> tuple[dict[str, Any], list[str], list[str]]:
    """The editable fields cleaned, the problems that make them unusable, and (for a
    model reply) the looser problems worth one repair. `strict` is for a person's
    edit: limits are errors rather than truncations, and unknown fields are refused.
    With concepts, the queries are composed from them for `engine` (unless a
    person's edit gives its own); without, the queries are taken as written."""
    problems: list[str] = []
    loose: list[str] = []
    if not isinstance(raw, dict):
        return {}, ["the plan is not a JSON object"], []
    if strict:
        unknown = sorted(set(raw) - set(EDITABLE))
        if unknown:
            problems.append(f"unknown fields {', '.join(unknown)}")
    out: dict[str, Any] = {}

    reformulation = raw.get("reformulation", "")
    if not isinstance(reformulation, str):
        problems.append("reformulation must be a string")
        reformulation = ""
    reformulation = _squash(reformulation)
    if len(reformulation) > MAX_TEXT:
        problems.append(f"reformulation is longer than {MAX_TEXT} characters")
    elif not reformulation and not strict:
        problems.append("reformulation is missing")
    out["reformulation"] = reformulation

    out["concepts"] = _concepts(raw.get("concepts"), strict, problems)
    derived = _derived(out["concepts"])
    for name in TERM_FIELDS:
        out[name] = _terms(raw.get(name) or derived[name], name, MAX_TERMS, MAX_TERM, strict, problems)
    if not out["subject_terms"] and not strict:
        problems.append("subject_terms is empty")

    layers = raw.get("keyword_layers") or derived["keyword_layers"]
    if not isinstance(layers, dict):
        problems.append("keyword_layers must be an object with the five layers")
        layers = {}
    elif strict and set(layers) - set(LAYERS):
        problems.append(f"keyword_layers has only the layers {', '.join(LAYERS)}")
    out["keyword_layers"] = {
        layer: _terms(layers.get(layer), f"keyword_layers.{layer}", MAX_TERMS, MAX_TERM, strict, problems)
        for layer in LAYERS
    }

    composed = bool(out["concepts"]) and (not strict or raw.get("queries") is None)
    if composed:
        out["queries"] = compose.compose(out["concepts"], engine, MAX_QUERIES)
        if not out["queries"]:
            problems.append("concepts has no subject concept")
        elif len(out["queries"]) < MIN_COMPOSED and not strict:
            loose.append(
                f"the concepts give only {len(out['queries'])} queries; add method, landmark or outcome concepts"
            )
    else:
        out["queries"] = _queries(raw.get("queries"), strict, problems)
    if not strict and not composed:
        vocabulary = _vocabulary(out)
        forms = full_forms(vocabulary[1:] + vocabulary[:1])
        for q in out["queries"]:
            expanded = expand_abbreviations(q["query"], forms)
            if len(expanded) <= MAX_QUERY:
                q["query"] = expanded
        loose += [
            f"query {q['id']} shares no word with the plan's terms and keywords"
            for q in out["queries"]
            if not _anchored(q["query"], vocabulary)
        ]

    for name in ("year_from", "year_to"):
        value = raw.get(name)
        if value is not None and not (_is_int(value) and 1000 <= value <= 2999):
            problems.append(f"{name} must be a year or null")
            value = None
        out[name] = value
    if out["year_from"] is not None and out["year_to"] is not None and out["year_from"] > out["year_to"]:
        problems.append("year_from is after year_to")

    criteria = raw.get("criteria", {})
    if not isinstance(criteria, dict) or (strict and set(criteria) - {"include", "exclude"}):
        problems.append("criteria must be an object with include and exclude lists")
        criteria = {}
    out["criteria"] = {
        side: _terms(criteria.get(side), f"criteria.{side}", MAX_CRITERIA, MAX_TEXT, strict, problems)
        for side in ("include", "exclude")
    }

    if not strict and not out["criteria"]["include"]:
        loose.append("criteria.include is empty")

    rationale = raw.get("rationale", "")
    if not isinstance(rationale, str):
        problems.append("rationale must be a string")
        rationale = ""
    rationale = _squash(rationale)
    if len(rationale) > MAX_TEXT:
        if strict:
            problems.append(f"rationale is longer than {MAX_TEXT} characters")
        rationale = rationale[:MAX_TEXT]
    out["rationale"] = rationale
    return out, problems, loose


def _concepts(value: Any, strict: bool, problems: list[str]) -> list[dict[str, Any]]:
    """`[{role, terms}]` with each term reduced to plain words."""
    if value is None:
        return []
    if not isinstance(value, list):
        problems.append("concepts must be a list")
        return []
    out: list[dict[str, Any]] = []
    for position, item in enumerate(value, 1):
        if not isinstance(item, dict) or item.get("role") not in compose.ROLES:
            problems.append(f"concept {position} needs a role ({', '.join(compose.ROLES)})")
            continue
        if strict and set(item) - {"role", "terms"}:
            problems.append(f"concept {position} has fields other than role and terms")
        raw_terms = item.get("terms")
        if isinstance(raw_terms, list):
            raw_terms = [compose.term(t) if isinstance(t, str) else t for t in raw_terms]
        terms = _terms(raw_terms, f"concept {position} terms", MAX_CONCEPT_TERMS, MAX_TERM, strict, problems)
        if any(_CJK.search(t) for t in terms):
            problems.append(f"concept {position} has terms that are not in English")
            continue
        if terms:
            out.append({"role": item["role"], "terms": terms})
    if len(out) > MAX_CONCEPTS:
        if strict:
            problems.append(f"use at most {MAX_CONCEPTS} concepts")
        out = out[:MAX_CONCEPTS]
    return out


def _derived(concepts: list[dict[str, Any]]) -> dict[str, Any]:
    """The term fields and keyword layers a plan with concepts but without them shows:
    each concept's first term by role, the subject's alternatives as the canonical
    layer, the method terms as mainstream and the landmarks as method-centric."""
    def first(role: str) -> list[str]:
        return [c["terms"][0] for c in concepts if c["role"] == role]

    def every(role: str) -> list[str]:
        return [t for c in concepts if c["role"] == role for t in c["terms"]]

    return {
        "subject_terms": first("subject"),
        "method_terms": first("method"),
        "outcome_terms": first("outcome"),
        "keyword_layers": {
            "canonical": every("subject")[:MAX_TERMS], "mainstream": first("method")[:MAX_TERMS],
            "emerging": [], "method_centric": every("landmark")[:MAX_TERMS], "cross_domain": [],
        },
    }


def _terms(
    value: Any, name: str, limit: int, length: int, strict: bool, problems: list[str]
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{name} must be a list of strings")
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        term = _squash(item)
        if not term or term.casefold() in seen:
            continue
        if len(term) > length:
            if strict:
                problems.append(f"{name}: “{term[:40]}…” is longer than {length} characters")
                continue
            term = term[:length].rstrip()
        seen.add(term.casefold())
        out.append(term)
    if len(out) > limit:
        if strict:
            problems.append(f"{name} has more than {limit} entries")
        out = out[:limit]
    return out


def _queries(value: Any, strict: bool, problems: list[str]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        problems.append("queries must be a non-empty list")
        return []
    layers = (*LAYERS, ORIGINAL) if strict else LAYERS
    kept: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, item in enumerate(value, 1):
        if not isinstance(item, dict):
            problems.append(f"query {position} must be an object")
            continue
        if strict and set(item) - {"id", "query", "layer", "priority", "limit"}:
            problems.append(f"query {position} has fields other than id, query, layer, priority and limit")
        text = item.get("query")
        if not isinstance(text, str) or not clean_query(text):
            problems.append(f"query {position} has no search text")
            continue
        text = clean_query(text)
        layer = item.get("layer")
        if layer not in layers:
            problems.append(f"query {position} has no valid layer ({', '.join(layers)})")
            continue
        # Only the question as asked (the fallback's query) may be in another language.
        if layer != ORIGINAL and _CJK.search(text):
            problems.append(f"query {position} (“{text[:40]}”) is not in English")
            continue
        if len(text) > MAX_QUERY:
            problems.append(f"query {position} is longer than {MAX_QUERY} characters")
            continue
        if text.count('"') % 2:
            text = text.replace('"', "")
        if not strict:
            text = tidy_query(text)
        priority = item.get("priority", position)
        if not _is_int(priority) or priority < 1:
            problems.append(f"query {position} needs a priority of 1 or more")
            continue
        if text.casefold() in seen:
            continue
        seen.add(text.casefold())
        given = item.get("id")
        depth = item.get("limit")
        if depth is not None and not (_is_int(depth) and 1 <= depth <= MAX_LIMIT):
            problems.append(f"query {position} needs a limit of 1–{MAX_LIMIT} results, or none")
            continue
        kept.append({"id": given if isinstance(given, str) else None, "query": text,
                     "layer": layer, "priority": priority, **({"limit": depth} if depth else {})})
    if not kept and not any(p.startswith("queries") for p in problems):
        problems.append("queries has no usable query")
    kept.sort(key=lambda q: q["priority"])
    if len(kept) > MAX_QUERIES:
        if strict:
            problems.append(f"use at most {MAX_QUERIES} queries")
        kept = kept[:MAX_QUERIES]
    # Ids stay as given when valid and unique; the others get the next free qN.
    used = {q["id"] for q in kept if q["id"] and _LOCAL_ID.fullmatch(q["id"])}
    taken: set[str] = set()
    n = 0
    for q in kept:
        if q["id"] and _LOCAL_ID.fullmatch(q["id"]) and q["id"] not in taken:
            taken.add(q["id"])
            continue
        n += 1
        while f"q{n}" in used or f"q{n}" in taken:
            n += 1
        q["id"] = f"q{n}"
        taken.add(q["id"])
    return kept


def clean_query(text: str) -> str:
    """Search text without the punctuation that breaks a search, spaces collapsed."""
    return _squash(_BREAKS.sub(" ", text))


def tidy_query(text: str) -> str:
    """A model's query with its quoting reined in. OpenAlex searches a quoted phrase
    exactly (no plurals, no hyphenated spellings), so outside parentheses only the
    first quoted phrase of two or three words stays quoted; inside, an alternative
    of several words is quoted, so `(deep learning OR NWP)` reads as two
    alternatives. A lower-case `or` between alternatives becomes `OR`, and
    unbalanced parentheses are dropped."""
    depth = 0
    for c in _QUOTED.sub("", text):
        depth += {"(": 1, ")": -1}.get(c, 0)
        if depth < 0:
            break
    if depth:
        text = text.replace("(", " ").replace(")", " ")
    kept = False

    def phrase(m: re.Match[str]) -> str:
        nonlocal kept
        words = m[1].split()
        inside = text[: m.start()].count("(") > text[: m.start()].count(")")
        if inside:
            return m[0] if len(words) > 1 else m[1]
        if 2 <= len(words) <= PHRASE_WORDS and not kept:
            kept = True
            return m[0]
        return m[1]

    text = _QUOTED.sub(phrase, text)
    text = _GROUP.sub(_alternatives, text)
    return _squash(text.replace("( ", "(").replace(" )", ")"))


def full_forms(texts: list[str]) -> dict[str, str]:
    """Abbreviation → the phrase it stands for, from the plan's own terms (`PDE` →
    `partial differential equations`, `PINN` → `physics-informed neural network`);
    the first phrase whose initials match wins."""
    forms: dict[str, str] = {}
    for text in texts:
        words = _TERM_WORD.findall(text)
        for start in range(len(words)):
            for end in range(start + 2, min(start + 7, len(words) + 1)):
                parts = [p for w in words[start:end] for p in w.split("-")]
                initials = "".join(p[0] for p in parts).upper()
                if 2 <= len(initials) <= 6:
                    forms.setdefault(initials, " ".join(words[start:end]))
    return forms


def expand_abbreviations(text: str, forms: Mapping[str, str]) -> str:
    """A bare abbreviation in a query (outside quotes and OR groups) joined with its
    full form, `PDE` → `(PDE OR "partial differential equations")`: a paper that
    spells it out in its abstract is found too. One whose full form the query
    already has stays as it is."""

    def piece(m: re.Match[str]) -> str:
        word = m[0]
        if not _ABBREVIATION.fullmatch(word):
            return word
        full = forms.get(word.removesuffix("s"))
        if full is None or full.casefold() in text.casefold():
            return word
        return f'({word} OR "{full}")'

    return _PIECE.sub(piece, text)


def _alternatives(group: re.Match[str]) -> str:
    """`(a b OR "c d" or e)` → `("a b" OR "c d" OR e)`."""
    options = _OR.split(group[0][1:-1])
    if len(options) < 2:
        return group[0]
    quoted = [o if '"' in o or len(o.split()) < 2 else f'"{_squash(o)}"' for o in map(str.strip, options)]
    return "(" + " OR ".join(o for o in quoted if o) + ")"


def _anchored(query: str, vocabulary: list[str]) -> bool:
    """Whether the query shares a non-generic word (plural folded) with the plan's
    own vocabulary: its restatement, terms and keyword layers."""
    known = _words(" ".join(vocabulary))
    return not known or bool(known & _words(query))


def _words(text: str) -> set[str]:
    words = {w.removesuffix("s") if len(w) > 3 else w for w in _WORD.findall(text.lower())}
    return words - _GENERIC


def _vocabulary(fields: dict[str, Any]) -> list[str]:
    return [
        fields["reformulation"],
        *(t for name in TERM_FIELDS for t in fields[name]),
        *(t for layer in LAYERS for t in fields["keyword_layers"][layer]),
    ]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _squash(text: str) -> str:
    return " ".join(text.split())


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# --- documents ---------------------------------------------------------------------------


def _check_run_id(run_id: str) -> None:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise HttpError(400, "A run id is 1–64 letters, digits, - or _")


def _question(question: str) -> str:
    text = _squash(question) if isinstance(question, str) else ""
    if not text or len(text) > MAX_QUESTION:
        raise HttpError(400, f"Ask a question of 1–{MAX_QUESTION} characters")
    return text


def _years(year_from: int | None, year_to: int | None) -> tuple[int | None, int | None]:
    for value in (year_from, year_to):
        if value is not None and not (_is_int(value) and 1000 <= value <= 2999):
            raise HttpError(400, "A year must be a whole number from 1000 to 2999")
    if year_from is not None and year_to is not None and year_from > year_to:
        raise HttpError(400, "The first year must not be after the last")
    return year_from, year_to


_FALLBACK = re.compile(r"No model plan \((.*)\); the question itself is the only query\.", re.S)


def fallback_reason(plan: dict[str, Any]) -> str | None:
    """Why `plan` is the fallback (the text `_fallback` put in its rationale), or None."""
    found = _FALLBACK.fullmatch(plan.get("rationale", "")) if plan.get("source") == "fallback" else None
    return found.group(1) if found else None


def _fallback(
    run_id: str, question: str, lang: str, years: tuple[int | None, int | None], why: str,
    engine: compose.Engine = compose.OPENALEX,
) -> dict[str, Any]:
    query = clean_query(question)[:MAX_QUERY].rstrip() or question[:MAX_QUERY]
    if engine.quote_symbols:
        query = compose.render([query], engine) or query
    fields = {
        "reformulation": question,
        "concepts": [],
        **{name: [] for name in TERM_FIELDS},
        "keyword_layers": {layer: [] for layer in LAYERS},
        "queries": [
            {"id": "q1", "query": query, "layer": ORIGINAL, "priority": 1}
        ],
        "year_from": years[0],
        "year_to": years[1],
        "criteria": {"include": [], "exclude": []},
        "rationale": f"No model plan ({_clip(why, 400)}); the question itself is the only query.",
    }
    return _document(run_id, "fallback", question, lang, fields, engine)


def _document(
    run_id: str, source: str, question: str, lang: str, fields: dict[str, Any],
    engine: compose.Engine = compose.OPENALEX,
) -> dict[str, Any]:
    """A `question_plan@1` in the contract's field order; `version` is set on save."""
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "version": 0,
        "parent_version": None,
        "created_at": db.now(),
        "source": source,
        "question": question,
        "lang": lang,
        "engine": engine.name,
        **{name: fields[name] for name in EDITABLE},
    }


def _save(conn: sqlite3.Connection, plan: dict[str, Any], ledger: str | None) -> dict[str, Any]:
    with db.immediate(conn):
        latest = conn.execute(
            "SELECT max(version) FROM question_plans WHERE run_id = ?", (plan["run_id"],)
        ).fetchone()[0]
        plan["version"] = (latest or 0) + 1
        conn.execute(
            "INSERT INTO question_plans (run_id, version, parent_version, source, plan, "
            "ledger_run_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (plan["run_id"], plan["version"], plan["parent_version"], plan["source"],
             jsonfmt.dumps(plan, sort_keys=False), ledger, plan["created_at"]),
        )
    return plan
