"""The screening stage of a question run: the model judges every candidate's title
and abstract against the plan's include and exclude criteria (`screen_verdict@1`),
and people override any decision (`screen_override@1`). Sole writer of
`screen_verdicts` and `screen_overrides`.

Screening is the filter after a search tuned for recall (1,500–2,500 candidates
a run), so every candidate goes to the model: no keyword pre-filter, which on the
recorded samples kept two thirds of the candidates and lost one must-find paper
in ten. Papers the plan's narrow queries (priority 1) found are judged first, best
search rank first, so the likeliest papers are decided early.

Batches are sized for a single-sequence local model (Strata): papers are added
while the batch stays within `batch_papers` papers and `batch_tokens` estimated
tokens of paper text (at most a quarter of the model's `window`), so a batch of
long abstracts is smaller. The reply is one JSON object with a verdict per paper,
checked strictly. Papers the reply leaves out are asked again on their own
batch; a reply with no usable verdict (broken JSON, cut off, refused) is split in
two and each half retried, down to one paper. A call that fails outright (the
model unreachable, a server error) leaves its papers `unscreened` and goes on;
after `STOP_AFTER` such calls in a row, or the first that could not connect at
all, the rest are left unscreened too, with one non-fatal `error` for all of them
(not one per batch).
Unscreened papers stay in the screened set like included ones, so a failure never
costs recall, and each failed batch is a non-fatal `error` event, which ends the
run `partial`.

A verdict is reused when a paper was judged under the same prompt, criteria and
model (`criteria_sha`), so a run screened again only asks about new papers or new
criteria. Unscreened papers are always asked again.

A paper's effective decision is its latest override that is not revoked, else its
latest verdict (`decisions`). The screened set (`s1.screened`, a `screen`
operation over the candidates) leaves out the excluded papers; overrides made
later change the decisions, not that set, so the graph and exports read
`decisions`. Every verdict and override is an event; the stage is framed by
`stage` begin and end events.

`check` is called before each batch; whatever it raises stops the screening
there (a pause or a cancel), with every verdict so far kept. `resume` continues
the run's latest screening: papers it already judged are not asked again and its
stage `begin` event is not repeated.
"""

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from isbe.core import assets, db, jsonfmt, upstreams
from isbe.core.errors import HttpError, describe
from isbe.core.llm import Llm, LlmError, Prompt, Tier
from isbe.question import plan as plans
from isbe.question import problems, runs, sets

VERDICT = "screen_verdict@1"
OVERRIDE = "screen_override@1"
DECISIONS = ("include", "exclude")
UNSCREENED = "unscreened"

# Papers per batch: more makes a small model lose track of the numbering.
BATCH_PAPERS = 20
# Estimated tokens of paper text per batch.
BATCH_TOKENS = 10000
# The model's context when none is given (Strata serves 256k).
WINDOW = 262144
# Abstract characters shown per paper: the mean recorded abstract is ~1,200.
ABSTRACT_CHARS = 1500
# Output per paper (a verdict of ~40 tokens, with room), and per batch.
OUT_PER_PAPER = 100
OUT_BASE = 400
REASON_CHARS = 300
# Calls failing outright in a row after which the rest is left unscreened.
STOP_AFTER = 3


@dataclass
class Result:
    screen: int
    plan_version: int
    candidates: str
    screened: str = ""  # the screened set's id
    included: int = 0
    excluded: int = 0
    unscreened: int = 0
    cached: int = 0
    batches: int = 0  # top-level batches
    calls: int = 0
    failed_batches: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    ms: int = 0
    # The first decided papers, in the order they were decided (keys), for checking
    # that the likeliest papers come first.
    order: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.failed_batches) or self.unscreened > 0


def run(
    conn: sqlite3.Connection,
    llm: Llm | None,
    run_id: str,
    candidates: str | None = None,
    plan_version: int | None = None,
    *,
    batch_papers: int = BATCH_PAPERS,
    batch_tokens: int = BATCH_TOKENS,
    window: int = WINDOW,
    clock: Callable[[], float] = time.monotonic,
    unavailable: str = "no model is configured",
    check: Callable[[], None] | None = None,
    resume: bool = False,
    frame: bool = True,
) -> Result:
    """Screen the candidate set `candidates` (the latest search's when None) with the
    criteria of plan version `plan_version` (the latest when None). With no `llm`,
    every paper is left unscreened and `unavailable` says why. Without `frame` there
    are no `stage` screen events (the expand stage screening the papers it added)."""
    runs.get(conn, run_id)
    plan = plans.get(conn, run_id, plan_version)
    store = sets.Store(conn, run_id)
    candidates = candidates or _latest_candidates(conn, run_id)
    members = store.get(candidates).members
    started = clock()
    latest = conn.execute("SELECT max(screen) FROM screen_verdicts WHERE run_id = ?", (run_id,)).fetchone()[0]
    resume = resume and latest is not None
    number = latest if resume else (latest or 0) + 1
    s = _Screen(conn, llm, run_id, plan, number, candidates, batch_papers, batch_tokens, window, clock)
    s.check = check
    if frame and not resume:
        runs.emit(conn, run_id, [("stage", {"stage": "screen", "state": "begin"})])

    papers = [m["paper"] for m in order(members, _priorities(conn, run_id, candidates, plan))]
    if resume:
        judged = {k for k, in conn.execute(
            "SELECT key FROM screen_verdicts WHERE run_id = ? AND screen = ?", (run_id, number)
        )}
        papers = [p for p in papers if p["key"] not in judged]
    todo = s.reuse(papers)
    if llm is None:
        s.leave(todo, "none", f"Not screened: {unavailable}")
        if todo:
            runs.emit(conn, run_id, [("error", {
                "stage": "screen", "fatal": False, "code": problems.MODEL_NOT_CONFIGURED,
                "message": f"No model to screen with ({unavailable}): {len(todo)} papers were left unscreened",
                "detail": f"{problems.model_detail(unavailable)}; {len(todo)} unscreened",
            })])
    else:
        stopped: list[BaseException] = []

        def work(ledger: str, stats: dict[str, Any]) -> None:
            s.ledger = ledger
            try:
                s.judge_all(todo)
            except _Stopped as stop:
                stats["stopped"] = describe(stop.cause)
                stopped.append(stop.cause)
            stats.update(s.stats())

        db.job(conn, "question_screen", None, work)
        if stopped:
            raise stopped[0]

    tag = candidates.split(".", 1)[0] if "." in candidates else "s1"
    excluded = {k for k, d in decisions(conn, run_id).items() if d["decision"] == "exclude"}
    screened, _, _ = store.run(
        "screen", [candidates], store.free_id(f"{tag}.screened"), "screened",
        "Candidates the screening kept", {"plan_version": plan["version"], "screen": number}, excluded,
    )
    if frame:
        runs.emit(conn, run_id, [("stage", {"stage": "screen", "state": "end"})])
    result = s.result
    result.screened = screened.set_id
    if s.ledger:
        usage = _usage(conn, s.ledger)
        result.input_tokens, result.output_tokens = usage
    result.ms = int((clock() - started) * 1000)
    return result


def order(members: list[sets.Member], priorities: dict[str, int]) -> list[sets.Member]:
    """Members by the best priority among the queries that found them, then their
    best rank in those queries; set order breaks ties. A member no search found
    (an expansion) comes last."""
    def place(item: tuple[int, sets.Member]) -> tuple[int, int, int]:
        position, member = item
        found = [(priorities.get(p["query_id"], 99), p.get("rank", 10**6))
                 for p in member["provenance"] if p.get("via") == "search" and "query_id" in p]
        best = min(found, default=(100, 10**6))
        return best[0], best[1], position

    return [m for _, m in sorted(enumerate(members), key=place)]


def system_prompt(lang: str) -> str:
    return assets.text("prompts", lang, "question_screen.md")


def criteria_text(plan: dict[str, Any]) -> str:
    """The part of every batch's user message before the papers: the question, its
    restatement and the criteria. With no criteria (a fallback plan), the
    restatement is the include criterion."""
    criteria = plan.get("criteria") or {}
    include = criteria.get("include") or [f"Is about: {plan.get('reformulation') or plan['question']}"]
    exclude = criteria.get("exclude") or []
    lines = [f"Question: {plan['question']}"]
    if plan.get("reformulation") and plan["reformulation"] != plan["question"]:
        lines.append(f"Restated: {plan['reformulation']}")
    lines.append("Include a paper that:")
    lines += [f"- {c}" for c in include]
    if exclude:
        lines.append("Exclude a paper that:")
        lines += [f"- {c}" for c in exclude]
    return "\n".join(lines)


def paper_text(n: int, paper: dict[str, Any]) -> str:
    """One numbered paper as the model reads it."""
    head = " · ".join(str(x) for x in (paper.get("year"), paper.get("venue")) if x)
    abstract = " ".join((paper.get("abstract") or "").split())
    if len(abstract) > ABSTRACT_CHARS:
        abstract = abstract[: ABSTRACT_CHARS - 1].rstrip() + "…"
    lines = [f"[{n}] {' '.join(paper['title'].split())}"]
    if head:
        lines.append(head)
    lines.append(f"Abstract: {abstract}" if abstract else "Abstract: (none)")
    return "\n".join(lines)


def estimate(text: str) -> int:
    """Tokens of `text`, on the high side (English runs ~4 characters a token)."""
    return len(text) // 3 + 1


def batches(papers: list[dict[str, Any]], batch_papers: int, budget: int) -> list[list[dict[str, Any]]]:
    """`papers` in order, cut where a batch would pass `batch_papers` papers or
    `budget` estimated tokens of paper text; a paper longer than the budget is a
    batch of its own."""
    out: list[list[dict[str, Any]]] = []
    batch: list[dict[str, Any]] = []
    tokens = 0
    for paper in papers:
        size = estimate(paper_text(len(batch) + 1, paper))
        if batch and (len(batch) >= batch_papers or tokens + size > budget):
            out.append(batch)
            batch, tokens = [], 0
        batch.append(paper)
        tokens += size
    if batch:
        out.append(batch)
    return out


def parse(text: str, n: int) -> dict[int, tuple[str, float | None, str]]:
    """A reply → {0-based paper index: (decision, score, reason)} for the entries
    that check out: a number from 1 to `n` not seen before, a decision of include
    or exclude, a score from 0 to 1 or none, a reason. The rest are left out."""
    value: Any
    try:
        value = json.loads(plans._THINK.sub("", text).strip())
    except ValueError:
        try:
            value = plans.extract_json(text)
        except ValueError:
            return {}
    items = value.get("verdicts") if isinstance(value, dict) else value
    if not isinstance(items, list):
        return {}
    out: dict[int, tuple[str, float | None, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        number = item.get("n", item.get("id"))
        if isinstance(number, str) and number.strip().strip("[]").isdigit():
            number = int(number.strip().strip("[]"))
        if not plans._is_int(number) or not 1 <= number <= n or number - 1 in out:
            continue
        decision = item.get("decision")
        decision = decision.strip().lower() if isinstance(decision, str) else None
        if decision not in DECISIONS:
            continue
        score = item.get("score")
        if isinstance(score, bool) or not isinstance(score, int | float) or not 0 <= score <= 1:
            score = None
        reason = item.get("reason")
        reason = " ".join(reason.split()) if isinstance(reason, str) else ""
        if len(reason) > REASON_CHARS:
            reason = reason[: REASON_CHARS - 1].rstrip() + "…"
        out[number - 1] = (decision, None if score is None else round(float(score), 3), reason)
    return out


class _Stopped(Exception):
    """`check` stopped the screening between batches; `cause` is what it raised."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__(str(cause))
        self.cause = cause


class _CallFailed(Exception):
    """The model call failed without a reply to read."""


class _Screen:
    def __init__(
        self, conn: sqlite3.Connection, llm: Llm | None, run_id: str, plan: dict[str, Any], number: int,
        candidates: str, batch_papers: int, batch_tokens: int, window: int, clock: Callable[[], float],
    ) -> None:
        self.conn = conn
        self.llm = llm
        self.run_id = run_id
        self.plan = plan
        self.number = number
        self.clock = clock
        self.system = system_prompt(plan["lang"])
        self.header = criteria_text(plan)
        self.model = llm.fast if llm else "none"
        self.sha = db.sha256(jsonfmt.dumps([self.system, self.header, self.model]))
        self.batch_papers = max(1, batch_papers)
        self.budget = max(1, min(batch_tokens, window // 4))
        self.ledger = ""
        self.check: Callable[[], None] | None = None
        # A resumed screening numbers its batches on from where it stopped.
        self.first_batch = 1 + max(
            (int(b.split(".")[1][1:]) for b, in conn.execute(
                "SELECT DISTINCT batch_id FROM screen_verdicts WHERE run_id = ? AND screen = ? AND batch_id LIKE ?",
                (run_id, number, f"c{number}.b%"),
            ) if b.split(".")[1][1:].isdigit()),
            default=0,
        )
        self.failures_in_row = 0
        self.gone: str | None = None  # why the model counts as unreachable, once it does
        self.gone_code = problems.MODEL_UNREACHABLE  # or MODEL_REJECTED: a wrong key
        self.last_error: str | None = None  # the last failed call, for the error that stops screening
        self.result = Result(number, plan["version"], candidates)

    # --- verdicts ---

    def reuse(self, papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Save the verdicts earlier screenings gave under the same hash; the papers left."""
        rows = self.conn.execute(
            "SELECT key, doc FROM screen_verdicts WHERE run_id = ? AND criteria_sha = ? AND decision != ? "
            "ORDER BY screen",
            (self.run_id, self.sha, UNSCREENED),
        ).fetchall()
        known = {key: jsonfmt.loads(doc) for key, doc in rows}
        todo, reused = [], []
        for paper in papers:
            doc = known.get(paper["key"])
            if doc is None:
                todo.append(paper)
            else:
                reused.append({**doc, "plan_version": self.plan["version"], "created_at": db.now()})
        if reused:
            self._save(reused, cached=True)
            self.result.cached = len(reused)
        return todo

    def leave(self, papers: list[dict[str, Any]], batch_id: str, why: str) -> None:
        self._save([self._doc(p, UNSCREENED, None, why, batch_id) for p in papers])

    def _doc(
        self, paper: dict[str, Any], decision: str, score: float | None, reason: str, batch_id: str
    ) -> dict[str, Any]:
        return {
            "schema": VERDICT,
            "run_id": self.run_id,
            "key": paper["key"],
            "decision": decision,
            "reason": reason or ("in scope" if decision == "include" else "out of scope"),
            "score": score if decision != UNSCREENED else None,
            "model": self.model,
            "batch_id": batch_id,
            "plan_version": self.plan["version"],
            "created_at": db.now(),
        }

    def _save(self, docs: list[dict[str, Any]], *, cached: bool = False) -> None:
        with db.immediate(self.conn):
            self.conn.executemany(
                "INSERT INTO screen_verdicts (run_id, screen, key, decision, batch_id, criteria_sha, cached, "
                "doc, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(self.run_id, self.number, d["key"], d["decision"], d["batch_id"], self.sha, int(cached),
                  jsonfmt.dumps(d, sort_keys=False), d["created_at"]) for d in docs],
            )
        for d in docs:
            setattr(self.result, _COUNTER[d["decision"]], getattr(self.result, _COUNTER[d["decision"]]) + 1)
            self.result.order.append(d["key"])
        runs.emit(self.conn, self.run_id, [("verdict", {"verdict": d}) for d in docs])

    # --- the model ---

    def judge_all(self, papers: list[dict[str, Any]]) -> None:
        skipped = left = 0
        for n, batch in enumerate(batches(papers, self.batch_papers, self.budget), self.first_batch):
            if self.check is not None:
                try:
                    self.check()
                except Exception as cause:
                    raise _Stopped(cause) from None
            self.result.batches += 1
            batch_id = f"c{self.number}.b{n}"
            if self.gone is not None or self.failures_in_row >= STOP_AFTER:
                why = self.gone or f"{STOP_AFTER} model calls failed in a row"
                self.leave(batch, batch_id, f"Not screened: screening stopped ({why})")
                self.result.failed_batches.append(batch_id)
                skipped, left = skipped + 1, left + len(batch)
                continue
            self.judge(batch, batch_id)
        if skipped:
            why = self.gone or f"{STOP_AFTER} model calls failed in a row"
            runs.emit(self.conn, self.run_id, [("error", {
                "stage": "screen", "fatal": False,
                "code": self.gone_code if self.gone else problems.MODEL_FAILED,
                "message": (f"Screening stopped: {why}. {left} more papers in {skipped} batches were left "
                            "unscreened; they are kept, and judged when the run is screened again"),
                "detail": f"{problems.model_detail(self.last_error or why)}; {left} unscreened",
            })])

    def judge(self, papers: list[dict[str, Any]], batch_id: str) -> None:
        """One batch, split and retried as the module docstring says."""
        try:
            text = self._call(papers)
        except _CallFailed as why:
            self.leave(papers, batch_id, f"Not screened: the model call failed ({why})")
            self._failed(batch_id, str(why))
            return
        found = parse(text, len(papers)) if text is not None else {}
        docs = [self._doc(papers[i], *found[i], batch_id) for i in sorted(found)]
        if docs:
            self._save(docs)
        missing = [p for i, p in enumerate(papers) if i not in found]
        if not missing:
            return
        if found:
            self.judge(missing, f"{batch_id}.r")
        elif len(papers) > 1:
            half = (len(papers) + 1) // 2
            self.judge(papers[:half], f"{batch_id}.1")
            self.judge(papers[half:], f"{batch_id}.2")
        else:
            why = "the model's reply had no usable verdict" if text is not None else "the model gave no reply"
            self.leave(papers, batch_id, f"Not screened: {why}")
            self._failed(batch_id, why)

    def _call(self, papers: list[dict[str, Any]]) -> str | None:
        """The reply text; None when the model answered without a usable reply (cut off,
        refused); `_CallFailed` when the call failed outright."""
        assert self.llm is not None
        user = self.header + "\n\nPapers:\n\n" + "\n\n".join(paper_text(i, p) for i, p in enumerate(papers, 1))
        prompt = Prompt("question_screen", Tier.FAST, self.system, user, OUT_BASE + OUT_PER_PAPER * len(papers))
        logged = self._logged()
        self.result.calls += 1
        try:
            reply = self.llm.complete(self.conn, self.ledger, prompt)
        except LlmError as error:
            if self._logged() > logged:
                # Logged but unusable: cut off at its limit, or refused.
                self.failures_in_row = 0
                return None
            self.failures_in_row += 1
            if upstreams.unreachable(error):
                self.gone = f"the model could not be reached: {describe(error)}"
            elif upstreams.rejected(error):
                self.gone = f"the model refused the request: {describe(error)}"
                self.gone_code = problems.MODEL_REJECTED
            self.last_error = describe(error)
            raise _CallFailed(self.last_error) from None
        self.failures_in_row = 0
        return reply.text

    def _logged(self) -> int:
        return self.conn.execute(
            "SELECT count(*) FROM llm_calls WHERE run_id = ?", (self.ledger,)
        ).fetchone()[0]

    def _failed(self, batch_id: str, why: str) -> None:
        self.result.failed_batches.append(batch_id)
        runs.emit(self.conn, self.run_id, [("error", {
            "stage": "screen", "message": f"Screening batch {batch_id} failed: {why}", "fatal": False,
            "batch_id": batch_id, "code": self.gone_code if self.gone else problems.MODEL_FAILED,
            "detail": problems.model_detail(why),
        })])

    def stats(self) -> dict[str, Any]:
        r = self.result
        return {"screen": r.screen, "included": r.included, "excluded": r.excluded,
                "unscreened": r.unscreened, "cached": r.cached, "batches": r.batches, "calls": r.calls,
                "failed_batches": len(r.failed_batches)}


_COUNTER = {"include": "included", "exclude": "excluded", UNSCREENED: "unscreened"}


def _latest_candidates(conn: sqlite3.Connection, run_id: str) -> str:
    row = conn.execute(
        "SELECT set_id FROM paper_sets WHERE run_id = ? AND kind = 'candidates' AND made_by IS NOT NULL "
        "AND set_id LIKE '%.candidates%' ORDER BY created_at DESC, rowid DESC LIMIT 1",
        (run_id,),
    ).fetchone()
    if row is None:
        raise HttpError(409, "The run has no candidates to screen yet")
    return row[0]


def _priorities(conn: sqlite3.Connection, run_id: str, candidates: str, plan: dict[str, Any]) -> dict[str, int]:
    """Query id → priority, from the plan the candidates' search ran (else `plan`)."""
    tag = candidates.split(".", 1)[0]
    searched = conn.execute(
        "SELECT plan_version FROM question_queries WHERE run_id = ? AND search = ? LIMIT 1",
        (run_id, int(tag[1:]) if tag[1:].isdigit() else 0),
    ).fetchone()
    source = plans.get(conn, run_id, searched[0]) if searched else plan
    return {q["id"]: q["priority"] for q in source["queries"]}


def _usage(conn: sqlite3.Connection, ledger: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT coalesce(sum(tokens_in), 0), coalesce(sum(tokens_out), 0) FROM llm_calls WHERE run_id = ?",
        (ledger,),
    ).fetchone()
    return row[0], row[1]


# --- decisions and overrides -----------------------------------------------------------


def verdicts(conn: sqlite3.Connection, run_id: str, screen: int | None = None) -> dict[str, dict[str, Any]]:
    """Each paper's latest verdict (of screening `screen` and earlier when given): key → document."""
    rows = conn.execute(
        "SELECT key, doc FROM screen_verdicts WHERE run_id = ? AND screen <= ? ORDER BY screen",
        (run_id, screen if screen is not None else 2**62),
    ).fetchall()
    return {key: jsonfmt.loads(doc) for key, doc in rows}


def overrides(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    """Every override record of the run, oldest first, revokes included."""
    rows = conn.execute(
        "SELECT doc FROM screen_overrides WHERE run_id = ? ORDER BY position", (run_id,)
    ).fetchall()
    return [jsonfmt.loads(r[0]) for r in rows]


def decisions(
    conn: sqlite3.Connection, run_id: str, *, screen: int | None = None, at: str | None = None
) -> dict[str, dict[str, Any]]:
    """Each paper's effective decision: key → {decision, source ("override" or
    "model"), verdict, override}. The latest override that is not revoked wins over
    the verdict; an unscreened paper counts as kept. `screen` and `at` (a
    timestamp) give the decisions as they stood then."""
    out: dict[str, dict[str, Any]] = {
        key: {"decision": doc["decision"], "source": "model", "verdict": doc, "override": None}
        for key, doc in verdicts(conn, run_id, screen).items()
    }
    records = [o for o in overrides(conn, run_id) if at is None or o["created_at"] <= at]
    revoked = {o["revokes"] for o in records if o["revokes"]}
    for o in records:
        if o["decision"] is None or o["override_id"] in revoked:
            continue
        entry = out.setdefault(o["key"], {"decision": None, "source": "model", "verdict": None, "override": None})
        entry.update(decision=o["decision"], source="override", override=o)
    return out


def override(
    conn: sqlite3.Connection, run_id: str, key: str, decision: str, actor: str, note: str = ""
) -> dict[str, Any]:
    """A person decides paper `key` (include or exclude); the `screen_override@1` saved."""
    runs.get(conn, run_id)
    if decision not in DECISIONS:
        raise HttpError(400, "A decision is include or exclude")
    if not _known(conn, run_id, key):
        raise HttpError(404, "No such paper in this run")
    return _record(conn, run_id, key, decision, actor, note, None)


def revoke(conn: sqlite3.Connection, run_id: str, override_id: str, actor: str, note: str = "") -> dict[str, Any]:
    """Undo override `override_id`: a new record with no decision that revokes it."""
    runs.get(conn, run_id)
    records = overrides(conn, run_id)
    target = next((o for o in records if o["override_id"] == override_id), None)
    if target is None or target["decision"] is None:
        raise HttpError(404, "No such override")
    if any(o["revokes"] == override_id for o in records):
        raise HttpError(409, "That override is already revoked")
    return _record(conn, run_id, target["key"], None, actor, note, override_id)


def _known(conn: sqlite3.Connection, run_id: str, key: str) -> bool:
    if conn.execute("SELECT 1 FROM screen_verdicts WHERE run_id = ? AND key = ? LIMIT 1", (run_id, key)).fetchone():
        return True
    for (doc,) in conn.execute("SELECT doc FROM paper_sets WHERE run_id = ? AND kind = 'candidates'", (run_id,)):
        if any(m["paper"]["key"] == key for m in sets.load(conn, doc)["members"]):
            return True
    return False


def _record(
    conn: sqlite3.Connection, run_id: str, key: str, decision: str | None, actor: str, note: str,
    revokes: str | None,
) -> dict[str, Any]:
    actor = " ".join(actor.split()) if isinstance(actor, str) else ""
    if not actor:
        raise HttpError(400, "An override needs the account that makes it")
    note = " ".join(note.split()) if isinstance(note, str) else ""
    with db.immediate(conn):
        position = conn.execute(
            "SELECT coalesce(max(position), 0) + 1 FROM screen_overrides WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        doc = {
            "schema": OVERRIDE,
            "run_id": run_id,
            "override_id": f"o{position}",
            "key": key,
            "decision": decision,
            "note": note[:REASON_CHARS * 2],
            "actor": actor[:64],
            "revokes": revokes,
            "created_at": db.now(),
        }
        conn.execute(
            "INSERT INTO screen_overrides (run_id, override_id, position, key, decision, revokes, doc, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, doc["override_id"], position, key, decision, revokes, jsonfmt.dumps(doc, sort_keys=False),
             doc["created_at"]),
        )
    runs.emit(conn, run_id, [("override", {"override": doc})])
    return doc


def replay(conn: sqlite3.Connection, run_id: str) -> dict[str, list[sets.Member]]:
    """Every set of the run rebuilt (`sets.Store.replay`), each screen operation with the
    decisions of its screening as they stood when it ran."""
    def excluded(op: dict[str, Any]) -> set[str]:
        found = decisions(conn, run_id, screen=op["params"].get("screen"), at=op["created_at"])
        return {key for key, d in found.items() if d["decision"] == "exclude"}

    return sets.Store(conn, run_id).replay(excluded)
