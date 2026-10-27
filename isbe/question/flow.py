"""A question run from question to result: the steps the worker takes, in order.

`work_one` takes the oldest ready step of the queue (`queue`) and does it in the
owner's workspace; the caller holds the execution lock. The steps and their
events (`question_event@1`, see docs/question/hsi-flow.md):

- `plan`: `state` running, `stage` plan begin, a quick check that the paper source
  answers (`check`: the run fails at once, before any model time, when
  it does not), the model's plan (`plan.generate`) and its `plan` event; a
  fallback plan (no model, or no usable plan) is also a non-fatal `error`
  (`plan_fallback`), so the run cannot end `completed` on the question alone. A
  model that cannot be reached fails the run. The run is then let go for the
  plan review: the search starts when a person starts it or runs an edited plan
  (`queue`), or at once when the run was asked without review.
- `search`: `stage` plan end, then the search stage (`search.run`). A rerun's
  carried overrides are applied once its candidates are known.
- `screen`: the screening stage (`screen.run`).
- `finish`: the expand stage: the citations of the candidates (`citations.run`),
  then, unless the run was asked with `expand: false`, one hop of them added as
  candidates and screened (`expand.run`); then the graph stage: `stage` graph
  begin, the knowledge graph (`graph.build`) and its `graph` event, `stage` graph
  end; then `state` done and `done` (`partial` when any non-fatal `error` was
  emitted). A graph that cannot be built is a non-fatal `error`: the papers and
  citations are kept, and `isbe question-graph` builds it later.

Between queries, batches and lookups the step reads what people asked: a pause stops it
(`state` paused; resuming continues the stage where it stopped), a cancel ends
the run (`state` cancelled, `done` cancelled). A failed search (every query
failed) has already ended the run `failed`; any other exception is a fatal
`error` and ends it `failed` too, its message saying what failed and what to do
and its `code` which upstream it was (`problems`). Interrupting the process
(Ctrl-C in the CLI) pauses the run, so it can be resumed.

Every step logs its start, its end with the time it took and the run's counts,
and its failure (WARNING) with the run id and stage.
"""

import logging
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from isbe.core import db, paths
from isbe.core.errors import HttpError, describe
from isbe.core.llm import Llm
from isbe.question import (
    citations,
    compose,
    expand,
    graph,
    problems,
    queue,
    runs,
    screen,
    search,
    service,
    sources,
)
from isbe.question import plan as plans

log = logging.getLogger(__name__)


@dataclass
class Services:
    """What a run uses beyond its database; tests replace them."""

    # The model, or None and why it cannot be used.
    model: Callable[[], tuple[Llm | None, str]] = plans.load_model
    # The paper source of the settings, searching with the given engine.
    source: Callable[[str], sources.Source] = sources.from_settings
    # Seconds a reviewed plan waits before the search starts by itself; None (the
    # product) waits for a person. Tests set one to walk the review without a click.
    countdown: float | None = None
    search: dict[str, Any] = field(default_factory=dict)  # extra search.run arguments
    screen: dict[str, Any] = field(default_factory=dict)  # extra screen.run arguments
    citations: dict[str, Any] = field(default_factory=dict)  # extra citations.run arguments
    expand: dict[str, Any] = field(default_factory=dict)  # extra expand.run arguments


class Stop(Exception):
    """A person asked the step in hand to stop: `kind` is pause or cancel."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def work_one(root: Path, services: Services) -> bool:
    """Do the oldest ready step; false when nothing was ready. Hold the execution lock."""
    for task in queue.stale(root):
        log.warning("question run %s was left running; marked paused", task.run_id)
        try:
            with closing(db.connect(paths.workspace(root, task.owner) / "isbe.db")) as conn:
                runs.set_status(conn, task.run_id, "paused")
        except Exception as error:
            log.error("question run %s: could not record the pause: %s", task.run_id, describe(error))
    task = queue.claim(root)
    if task is None:
        return False
    with closing(db.connect(paths.workspace(root, task.owner) / "isbe.db")) as conn:
        Step(root, conn, task, services).run()
    return True


def run_through(root: Path, run_id: str, services: Services, wait: Callable[[float], None] = time.sleep) -> str:
    """Take every step of run `run_id` now, waiting for its plan review: what the CLI
    does. Hold the execution lock. The run's final queue state (finished or paused)."""
    while True:
        task = queue.get(root, run_id)
        if task.state in ("finished", "paused"):
            return task.state
        claimed = queue.claim(root, run_id)
        if claimed is None:
            wait(0.5)  # a plan under review, until it is started in the console
            continue
        with closing(db.connect(paths.workspace(root, claimed.owner) / "isbe.db")) as conn:
            Step(root, conn, claimed, services).run()


class Step:
    def __init__(self, root: Path, conn: sqlite3.Connection, task: queue.Task, services: Services) -> None:
        self.root = root
        self.conn = conn
        self.task = task
        self.run_id = task.run_id
        self.services = services
        self.stage = "plan"
        self._entered: float | None = None  # when this step began the stage in hand

    def _enter(self, stage: str) -> None:
        """Move on to `stage`, logging how long the stage before it took in this step."""
        now = time.monotonic()
        if self._entered is not None and stage != self.stage:
            log.info("question run %s: %s ended after %.1f s", self.run_id, self.stage, now - self._entered)
        self.stage, self._entered = stage, now

    def check(self) -> None:
        kind = queue.control(self.root, self.run_id)
        if kind is not None:
            raise Stop(kind)

    def run(self) -> None:
        started = time.monotonic()
        log.info("question run %s: %s step started", self.run_id, self.task.step)
        try:
            self._steps()
        except Stop as stop:
            log.info("question run %s: %s at %s after %.1f s", self.run_id, stop.kind, self.stage,
                     time.monotonic() - started)
            self._stop(stop.kind)
        except search.SearchFailed as error:
            log.warning("question run %s failed at search after %.1f s: %s", self.run_id,
                        time.monotonic() - started, error.message)
            queue.stopped(self.root, self.run_id, "finished")  # the search ended the run already
        except Exception as error:
            log.warning("question run %s failed at %s after %.1f s: %s", self.run_id, self.stage,
                        time.monotonic() - started, describe(error))
            queue.stopped(self.root, self.run_id, "finished")
            data = {"stage": self.stage, "message": _reason(self.stage, error), "fatal": True,
                    "code": problems.code(error)}
            if detail := problems.detail(error):
                data["detail"] = detail
            runs.emit(self.conn, self.run_id, [("error", data)])
            runs.set_status(self.conn, self.run_id, "failed")
            runs.emit(self.conn, self.run_id, [("done", {"status": "failed"})])
        except BaseException:
            self._stop("pause")
            raise
        else:
            counts = service.counts(self.conn, self.run_id)
            # One step can run several stages (search through graph): say so, so the time
            # is not read as one stage's.
            through = "" if self.stage == self.task.step else f", through the {self.stage} stage,"
            log.info("question run %s: the %s step%s took %.1f s (%s)", self.run_id, self.task.step, through,
                     time.monotonic() - started, ", ".join(f"{k} {v}" for k, v in counts.items()))

    def _stop(self, kind: str) -> None:
        if kind == "pause":
            queue.stopped(self.root, self.run_id, "paused")
            runs.set_status(self.conn, self.run_id, "paused")
        else:
            queue.stopped(self.root, self.run_id, "finished")
            queue.cancelled(self.conn, self.run_id)

    def _moved(self, step: str) -> None:
        """Carry on with `step`, unless a person asked to stop meanwhile."""
        kind = queue.advance(self.root, self.run_id, step)
        if kind is not None:
            raise Stop(kind)

    def _steps(self) -> None:
        task, step, resume = self.task, self.task.step, self.task.started
        options = task.options
        if step == "plan":
            self._plan(options)
            return
        if step == "search":
            self._enter("search")
            if not resume:
                runs.emit(self.conn, self.run_id, [("stage", {"stage": "plan", "state": "end"})])
            if runs.get(self.conn, self.run_id)["status"] != "running":
                runs.set_status(self.conn, self.run_id, "running")
            plan = plans.get(self.conn, self.run_id)
            source = self.services.source(plan.get("engine", compose.OPENALEX.name))
            search.run(self.conn, source, self.run_id, check=self.check, resume=resume, **self.services.search)
            self._carry(options.get("overrides", []))
            self._moved("screen")
            step, resume = "screen", False
        if step == "screen":
            self._enter("screen")
            if runs.get(self.conn, self.run_id)["status"] != "running":
                runs.set_status(self.conn, self.run_id, "running")
            llm, why = self.services.model()
            screen.run(self.conn, llm, self.run_id, unavailable=why, check=self.check, resume=resume,
                       **self.services.screen)
            self._moved("finish")
            resume = False
        self._enter("expand")
        if runs.get(self.conn, self.run_id)["status"] != "running":
            runs.set_status(self.conn, self.run_id, "running")
        plan = plans.get(self.conn, self.run_id)
        source = self.services.source(plan.get("engine", compose.OPENALEX.name))
        if not resume:
            runs.emit(self.conn, self.run_id, [("stage", {"stage": "expand", "state": "begin"})])
        chosen = service.expansion_options(options)
        cited = citations.run(self.conn, source, self.run_id, check=self.check,
                              **{"full_limit": chosen["citations_full"], **self.services.citations})
        if chosen["expand"]:
            llm, why = self.services.model()
            added = expand.run(
                self.conn, llm, self.run_id, unavailable=why, check=self.check,
                **{"per_paper": chosen["expand_per_paper"], "total": chosen["expand_total"], **self.services.expand},
            )
            if added.added:
                # The overrides a rerun carried for papers only the expansion found.
                self._carry(options.get("overrides", []))
                citations.progress(self.conn, self.run_id, "all", cited.looked_up)
        runs.emit(self.conn, self.run_id, [("stage", {"stage": "expand", "state": "end"})])
        self._enter("graph")
        self._finish()

    def _plan(self, options: dict[str, Any]) -> None:
        run = runs.get(self.conn, self.run_id)
        runs.set_status(self.conn, self.run_id, "running")
        runs.emit(self.conn, self.run_id, [("stage", {"stage": "plan", "state": "begin"})])
        engine = compose.ENGINES[options.get("engine", compose.DEFAULT)]
        # Before any model time: a run that cannot search fails now, with the reason.
        self.services.source(engine.name).check()
        llm, why = self.services.model()
        plan = plans.generate(
            self.conn, llm, self.run_id, run["question"], run["lang"], year_from=options.get("year_from"),
            year_to=options.get("year_to"), unavailable=why, engine=engine,
        )
        events: list[tuple[str, dict[str, Any]]] = [("plan", {"plan": plan})]
        if plan["source"] == "fallback":
            events.append(("error", {
                "stage": "plan", "fatal": False,
                "code": problems.MODEL_NOT_CONFIGURED if llm is None else problems.PLAN_FALLBACK,
                "message": (f"{plan['rationale']} The search finds far fewer papers this way: fix the model "
                            "settings, then run the question again"),
                "detail": problems.model_detail(plans.fallback_reason(plan) or why),
            }))
        runs.emit(self.conn, self.run_id, events)
        if not options.get("review", True):
            ready_at: str | None = queue.at()
        else:
            ready_at = None if self.services.countdown is None else queue.at(self.services.countdown)
        kind = queue.advance(self.root, self.run_id, "search", ready_at=ready_at, release=True)
        if kind is not None:
            # Asked while the plan was being written.
            raise Stop(kind)

    def _carry(self, carried: list[dict[str, Any]]) -> None:
        """A rerun's overrides, for the papers this run also found and that have no
        override yet (carried after the search, then again after the expansion)."""
        overridden = {k for k, d in screen.decisions(self.conn, self.run_id).items() if d["source"] == "override"}
        for o in carried:
            if o["key"] in overridden:
                continue
            try:
                screen.override(self.conn, self.run_id, o["key"], o["decision"], o["actor"], o.get("note", ""))
            except HttpError:
                continue  # not found this time

    def _finish(self) -> None:
        runs.emit(self.conn, self.run_id, [("stage", {"stage": "graph", "state": "begin"})])
        try:
            built = graph.build(self.conn, self.run_id)
        except Exception as error:
            # No upstream here: a failure is a bug in the code or the data, so keep the trace.
            log.warning("question run %s: the graph could not be built: %s", self.run_id, describe(error),
                        exc_info=True)
            runs.emit(self.conn, self.run_id, [("error", {
                "stage": "graph", "fatal": False, "code": problems.STAGE_FAILED,
                "message": (f"The knowledge graph could not be built ({describe(error)}); the papers and "
                            "citations are kept. Build it again with isbe question-graph"),
            })])
        else:
            log.info("question run %s: graph %s in %.1f s (%s)", self.run_id,
                     "unchanged" if built.cached else "built", built.seconds,
                     ", ".join(f"{k} {v}" for k, v in graph.summary(built.doc).items() if k != "fingerprint"))
            runs.emit(self.conn, self.run_id, [("graph", graph.summary(built.doc))])
        runs.emit(self.conn, self.run_id, [("stage", {"stage": "graph", "state": "end"})])
        errors = self.conn.execute(
            "SELECT count(*) FROM question_events WHERE run_id = ? AND type = 'error'", (self.run_id,)
        ).fetchone()[0]
        queue.stopped(self.root, self.run_id, "finished")
        runs.set_status(self.conn, self.run_id, "done")
        runs.emit(self.conn, self.run_id, [("done", {"status": "partial" if errors else "completed"})])


def _reason(stage: str, error: Exception) -> str:
    """What a fatal error says: what failed, why, and what to do about it."""
    if isinstance(error, sources.SourceError):
        advice = problems.advice(error)
        return f"{str(error).rstrip('.')}. {advice[0].upper()}{advice[1:]}, then run the question again"
    if isinstance(error, HttpError):
        return error.message
    return f"The {stage} stage failed: {describe(error)}"
