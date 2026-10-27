"""Survey reports of question runs (`isbe.question.survey`): which papers are
surveyed, full text and cards through the enrich code, abstract-only papers,
citation checks, the rendered report and its place on the shelf."""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import replay

from isbe.core import db, http
from isbe.core.errors import HttpError
from isbe.core.llm import Llm, LlmError
from isbe.pipeline.enrich import arxiv_html
from isbe.pipeline.enrich.arxiv_html import Paper
from isbe.pipeline.enrich.calls import EnrichError
from isbe.question import runs, screen, sets, survey
from isbe.reports import library

TEXT = (
    "Ensemble wind gusts\nJane Roe, Example Weather Service\n\n"
    "We calibrate gust ensembles with EMOS post-processing.\n\n"
    "### Table 1: Verification.\n| CRPS | raw 2.10 | EMOS 1.64 |\n"
)
CARD = {
    "source": {"value": "Jane Roe, Example Weather Service", "quote": "Jane Roe, Example Weather Service"},
    "method": {"value": "EMOS 后处理校准阵风集合", "quote": "We calibrate gust ensembles with EMOS post-processing."},
    "results": {"value": "CRPS@gusts vs raw: 2.10→1.64", "quote": "| CRPS | raw 2.10 | EMOS 1.64 |"},
}
SURVEY = (
    "# 模型自拟的标题\n\n## TL;DR\n*   EMOS 校准降低了阵风集合的 CRPS [P1]\n- 机器学习方法在摘要中报告改进 [P2]\n"
    "- 尚缺对比研究 [P9]\n\n## 研究脉络\n统计后处理与机器学习两条线 [P1][P3]。\n"
)


def paper(n: int, *, arxiv: bool, cited: int) -> dict:
    keys = [f"doi:10.1/{n}"] + ([f"arxiv:2601.0000{n}"] if arxiv else [])
    return {
        "key": keys[0], "keys": keys, "title": f"Gust paper {n} | ensembles", "year": 2020 + n,
        "authors": [f"Author {n}"], "venue": "QJRMS", "type": "article", "cited_by_count": cited,
        "abstract": f"Abstract of paper {n} about ensemble wind gust forecasts.", "keywords": [],
        "url": f"https://doi.org/10.1/{n}",
    }


A, B, C, D = paper(1, arxiv=True, cited=50), paper(2, arxiv=False, cited=10), paper(3, arxiv=True, cited=5), \
    paper(4, arxiv=False, cited=99)


def verdict(conn: sqlite3.Connection, run_id: str, key: str, decision: str, score: float) -> None:
    conn.execute(
        "INSERT INTO screen_verdicts (run_id, screen, key, decision, batch_id, criteria_sha, cached, doc, created_at) "
        "VALUES (?, 1, ?, ?, 'b1', 'sha', 0, ?, ?)",
        (run_id, key, decision, json.dumps({"decision": decision, "score": score}), db.now()),
    )


@pytest.fixture
def run() -> tuple[sqlite3.Connection, str]:
    """A run with four candidates: A and B included by the model, C excluded but
    included by a person, D included by the model but excluded by a person."""
    conn = db.connect(":memory:")
    run_id = runs.create(conn, "大风集合预报的方法与检验", "zh", run_id="run-survey")
    members = [{"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": i}]}
               for i, p in enumerate((A, B, C, D), 1)]
    sets.Store(conn, run_id).add(sets.PaperSet("s1.candidates", "candidates", "Candidates", members))
    for p, decision, score in ((A, "include", 0.9), (B, "include", 0.9), (C, "exclude", 0.2), (D, "include", 1.0)):
        verdict(conn, run_id, p["key"], decision, score)
    screen.override(conn, run_id, C["key"], "include", "zhiheng")
    screen.override(conn, run_id, D["key"], "exclude", "zhiheng")
    runs.set_status(conn, run_id, "done")
    return conn, run_id


class Model:
    """Answers card prompts with `CARD` and the survey prompt with `SURVEY`; keeps the prompts."""

    def __init__(self, survey_reply: str = SURVEY) -> None:
        self.survey_reply = survey_reply
        self.cards: list[str] = []
        self.surveys: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        system, user = (m["content"] for m in json.loads(request.content)["messages"][:2])
        if "综述写作助手" in system or "survey writer" in system:
            self.surveys.append(user)
            text = self.survey_reply
        else:
            self.cards.append(user)
            text = json.dumps(CARD)
        return replay._completion(text, "stop", user)

    def llm(self) -> Llm:
        env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
               "ISBE_LLM_NO_AUTH": "true", "ISBE_LLM_MODEL_FAST": "fake", "ISBE_LLM_MODEL_SMART": "fake"}
        llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(self)))
        llm._sleep = lambda _: None
        return llm


class Arxiv:
    """arXiv HTML: `TEXT` for paper 1, unavailable for the rest; records the ids asked."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    def __call__(self, item_id: str) -> Paper:
        self.asked.append(item_id)
        if item_id == "arxiv:2601.00001":
            return Paper(TEXT, [], [])
        raise EnrichError("arXiv has no HTML for this paper")


def survey_job(conn, model: Model, data: Path, run_id: str, **options) -> dict:
    options.setdefault("today", "2026-10-26")
    options.setdefault("sleep", lambda _: None)
    return db.job(conn, "survey", None, lambda ledger, stats: survey.run(
        conn, model.llm(), ledger, data, run_id, stats, **options))


def test_included_papers_follow_overrides_most_relevant_first(run) -> None:
    conn, run_id = run
    # C, included by a person, comes first; A and B tie on the score and A is cited more.
    assert [p["key"] for p in survey.included(conn, run_id)] == [C["key"], A["key"], B["key"]]
    conn.execute("UPDATE screen_verdicts SET doc = ? WHERE key = ?",
                 (json.dumps({"decision": "include", "score": 0.95}), B["key"]))
    assert [p["key"] for p in survey.included(conn, run_id)] == [C["key"], B["key"], A["key"]]
    assert survey.item_id(A) == "arxiv:2601.00001" and survey.item_id(B) == "doi:10.1/2"


def test_a_survey_reads_full_text_where_it_can_and_says_where_it_could_not(run, data: Path) -> None:
    conn, run_id = run
    model, arxiv = Model(), Arxiv()
    stats = survey_job(conn, model, data, run_id, load=arxiv)

    # Only the arXiv papers are fetched; the DOI-only one is abstract-only from the start.
    assert arxiv.asked == ["arxiv:2601.00003", "arxiv:2601.00001"]
    assert stats["abstract_fallback"] == {"arxiv:2601.00003": "arXiv has no HTML for this paper"}
    assert len(model.cards) == 1 and model.cards[0] == TEXT
    assert stats | {"llm_call": None} == stats | {
        "report": "reports/run-survey/2026-10-26.md", "included": 3, "reviewed": 3, "fulltext": 1,
        "abstract_only": 2, "cards": 1, "cited": 3, "bad_citations": 1, "llm_call": None,
    }

    # The prompt numbers the papers, marks the depth and carries the verified facts and tables.
    (user,) = model.surveys
    assert "研究问题：大风集合预报的方法与检验" in user
    assert user.index("[P1] Gust paper 3") < user.index("[P2] Gust paper 1") < user.index("[P3] Gust paper 2")
    assert "Gust paper 4" not in user
    p2, p3 = user.split("[P2]")[1].split("[P3]")[0], user.split("[P3]")[1]
    assert "阅读深度: 全文" in p2 and "全文事实（已核验）: 来源=Jane Roe" in p2 and "| CRPS | raw 2.10 | EMOS 1.64 |" in p2
    assert "阅读深度: 仅摘要" in p3 and "全文事实" not in p3 and "Abstract of paper 2" in p3

    md = (data / stats["report"]).read_text()
    assert md.startswith("# 综述：大风集合预报的方法与检验\n")
    assert "模型自拟的标题" not in md
    # Citations of abstract-only papers (P1 = paper 3, P3 = paper 2) carry a dagger.
    assert "尚缺对比研究 [P?]" in md and "[P1]†[P3]†" in md and "改进 [P2]\n" in md
    assert "全文 1 篇，仅摘要 2 篇" in md
    assert "### [P2] Gust paper 1 \\| ensembles" in md and "### [P1] Gust paper 3" in md
    assert "**全文**" in md and md.count("**仅摘要**") == 2
    assert "> We calibrate gust ensembles with EMOS post-processing." in md
    assert "Gust paper 4" not in md

    # Registered as items no topic shows; on the shelf as a survey of the run.
    rows = conn.execute("SELECT id, source FROM items ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        ("arxiv:2601.00001", "question"), ("arxiv:2601.00003", "question"), ("doi:10.1/2", "question"),
    ]
    assert conn.execute("SELECT count(*) FROM topic_items").fetchone()[0] == 0
    (cover,) = library.library(conn, data)
    assert (cover["kind"], cover["topic_id"], cover["period"], cover["papers"]) == (
        "survey", "run-survey", "2026-10-26", 3)
    assert cover["label"] == "综述：大风集合预报的方法与检验"
    assert cover["tldr"][0] == "EMOS 校准降低了阵风集合的 CRPS [P1]†"
    # Home's newest reports name the survey by its title, not the run id.
    assert library.recent(conn, data)[0]["label"] == "综述：大风集合预报的方法与检验"
    assert "注意" not in md


def test_a_rerun_replaces_its_report_and_reuses_stored_text_and_cards(run, data: Path) -> None:
    conn, run_id = run
    conn.execute(
        "INSERT INTO items (id, kind, source, title, url, lang, published_at) "
        "VALUES ('arxiv:2601.00001', 'paper', 'essaies', 'Collected', 'https://arxiv.org/abs/2601.00001', 'en', "
        "'2026-01-01')"
    )
    survey_job(conn, Model(), data, run_id, load=Arxiv())
    model, arxiv = Model("## TL;DR\n- 第二版 [P1]\n"), Arxiv()
    stats = survey_job(conn, model, data, run_id, load=arxiv)
    assert arxiv.asked == ["arxiv:2601.00003"] and model.cards == []
    assert stats["fulltext"] == 1 and stats["cards"] == 1
    assert "第二版" in (data / stats["report"]).read_text()
    assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 1
    # A collected paper keeps its own row.
    assert conn.execute("SELECT source FROM items WHERE id = 'arxiv:2601.00001'").fetchone()[0] == "essaies"


def test_only_the_most_relevant_are_surveyed_and_the_rest_are_listed(run, data: Path) -> None:
    conn, run_id = run
    model = Model()
    stats = survey_job(conn, model, data, run_id, load=Arxiv(), max_papers=1, fulltext=False)
    assert stats["reviewed"] == 1 and stats["included"] == 3 and stats["bad_citations"] == 3
    assert "[P1] Gust paper 3" in model.surveys[0] and "[P2]" not in model.surveys[0]
    md = (data / stats["report"]).read_text()
    assert "## 未综述的纳入论文（2 篇）" in md and "- Gust paper 2 \\| ensembles (2022)" in md


def test_without_full_text_fetching_every_paper_is_abstract_only(run, data: Path, monkeypatch) -> None:
    conn, run_id = run
    monkeypatch.setenv("ISBE_FULLTEXT_EXTRACTOR", "off")
    arxiv = Arxiv()
    stats = survey_job(conn, Model(), data, run_id, load=arxiv)
    assert arxiv.asked == [] and stats["abstract_only"] == 3 and stats["cards"] == 0


def test_a_failed_card_is_named_and_the_report_still_written(run, data: Path) -> None:
    conn, run_id = run

    class Broken(Model):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            system = json.loads(request.content)["messages"][0]["content"]
            if "综述写作助手" not in system:
                return httpx.Response(400, json={"error": {"message": "context too long"}})
            return super().__call__(request)

    stats = survey_job(conn, Broken(), data, run_id, load=Arxiv())
    assert stats["failed"]["arxiv:2601.00001"].startswith("card: ")
    assert stats["fulltext"] == 1 and stats["cards"] == 0 and (data / stats["report"]).is_file()


def test_runs_without_included_papers_unknown_or_unfinished_runs_are_refused(run, data: Path) -> None:
    conn, run_id = run
    runs.set_status(conn, run_id, "paused")
    with pytest.raises(survey.SurveyError, match="the run is paused"):
        survey_job(conn, Model(), data, run_id)
    runs.set_status(conn, run_id, "done")
    for p in (A, B, C):
        screen.override(conn, run_id, p["key"], "exclude", "zhiheng")
    with pytest.raises(survey.SurveyError, match="no included papers"):
        survey_job(conn, Model(), data, run_id)
    with pytest.raises(HttpError, match="No such question run"):
        survey_job(conn, Model(), data, "run-missing")
    with pytest.raises(survey.SurveyError, match="at least 1"):
        survey_job(conn, Model(), data, run_id, max_papers=0)


def test_another_kind_of_report_on_the_path_is_not_overwritten(run, data: Path) -> None:
    conn, run_id = run
    path = data / "reports" / run_id / "2026-10-26.md"
    path.parent.mkdir(parents=True)
    path.write_text("someone else's file")
    model = Model()
    with pytest.raises(Exception, match="no report owns it"):
        survey_job(conn, model, data, run_id, load=Arxiv())
    assert model.surveys == [] and path.read_text() == "someone else's file"


def test_citations_keep_known_numbers_and_mark_the_rest() -> None:
    body, cited, bad = survey.citations(
        "# T\n## A\nx [P1][P2] y [P0] z [P12]\n*   a\n  + b\n**bold**\n### [P2] heading\nw [P1, P2]、[P2，P5]\n", 2, {2})
    assert body == (
        "## A\nx [P1][P2]† y [P?] z [P?]\n- a\n  - b\n**bold**\n### \\[P2]† heading\nw [P1][P2]†、[P2]†[P?]"
    )
    assert cited == {1, 2} and bad == 3


def test_a_paper_collected_after_a_survey_gets_its_real_date(run, data: Path) -> None:
    from isbe.collect.items import Item, store

    conn, run_id = run
    survey_job(conn, Model(), data, run_id, load=Arxiv())
    assert conn.execute("SELECT published_at FROM items WHERE id = 'arxiv:2601.00001'").fetchone()[0] == (
        "2021-01-01T00:00:00Z")
    collected = Item("arxiv:2601.00001", "paper", "arxiv", "Gust paper 1", "abstract",
                     "https://arxiv.org/abs/2601.00001", ["Author 1"], "en", "2026-10-22T08:00:00Z")
    store(conn, "gusts", [collected])
    row = conn.execute("SELECT source, published_at FROM items WHERE id = 'arxiv:2601.00001'").fetchone()
    assert tuple(row) == ("arxiv", "2026-10-22T08:00:00Z")
    # A collected paper's first date stays, as before.
    store(conn, "gusts", [Item(**{**collected.__dict__, "published_at": "2026-10-25T00:00:00Z"})])
    assert conn.execute("SELECT published_at FROM items WHERE id = 'arxiv:2601.00001'").fetchone()[0] == (
        "2026-10-22T08:00:00Z")
    # Its stored full text and card serve the topic too.
    assert conn.execute("SELECT count(*) FROM cards WHERE item_id = 'arxiv:2601.00001'").fetchone()[0] == 1


def test_an_unreachable_model_stops_at_the_first_card(run, data: Path) -> None:
    conn, run_id = run
    calls: list[str] = []

    def refuse(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("connection refused")

    env = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://127.0.0.1:9/v1",
           "ISBE_LLM_NO_AUTH": "true", "ISBE_LLM_MODEL_FAST": "fake", "ISBE_LLM_MODEL_SMART": "fake"}
    llm = Llm.from_vars(env.get, client=http.Http(5, transport=httpx.MockTransport(refuse)))
    waits: list[float] = []
    llm._sleep = waits.append
    with pytest.raises(survey.SurveyError, match="model call failed"):
        db.job(conn, "survey", None, lambda ledger, stats: survey.run(
            conn, llm, ledger, data, run_id, stats, load=Arxiv(), sleep=lambda _: None, today="2026-10-26"))
    # One card's call and its retries; no other card, no survey call, no report.
    assert len(calls) <= 3 and sum(waits) <= 35
    assert not (data / "reports" / run_id).exists()
    status, stats = conn.execute("SELECT status, stats FROM runs WHERE job = 'survey'").fetchone()
    assert status == "failed" and "arxiv:2601.00001" in json.loads(stats)["failed"]


def test_a_failed_survey_call_fails_the_job_and_writes_nothing(run, data: Path) -> None:
    conn, run_id = run

    class Down(Model):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            system = json.loads(request.content)["messages"][0]["content"]
            if "综述写作助手" in system:
                return httpx.Response(503, json={"error": {"message": "overloaded"}})
            return super().__call__(request)

    with pytest.raises(LlmError):
        survey_job(conn, Down(), data, run_id, load=Arxiv())
    assert conn.execute("SELECT status FROM runs WHERE job = 'survey'").fetchone()[0] == "failed"
    assert conn.execute("SELECT count(*) FROM reports").fetchone()[0] == 0


def _more_arxiv_papers(conn: sqlite3.Connection, run_id: str) -> None:
    """Three more included arXiv papers (5, 6, 7), in a rerun's candidate set."""
    more = [paper(n, arxiv=True, cited=1) for n in (5, 6, 7)]
    sets.Store(conn, run_id).add(sets.PaperSet("s2.candidates", "candidates", "Candidates", [
        {"paper": p, "provenance": [{"via": "search", "query_id": "q1", "rank": i}]}
        for i, p in enumerate((A, B, C, D, *more), 1)
    ]))
    for p in more:
        verdict(conn, run_id, p["key"], "include", 0.5)


def _real_fetch(answer):
    """The real `arxiv_html.fetch` over a transport that `answer(arxiv id)` drives; the ids asked."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path.removeprefix("/html/"))
        return answer(asked[-1], request)

    client = http.Http(5, transport=httpx.MockTransport(handle))
    return (lambda item_id: arxiv_html.fetch(client, item_id)), asked


def test_papers_without_arxiv_html_do_not_stop_the_others(run, data: Path) -> None:
    conn, run_id = run
    _more_arxiv_papers(conn, run_id)
    page = "<article class='ltx_document'><p>Ensemble wind gusts, verified.</p></article>"

    def answer(id: str, request: httpx.Request) -> httpx.Response:
        if id in ("2601.00003", "2601.00001"):  # arXiv answers: no HTML for these
            return httpx.Response(404, text="not found")
        if id == "2601.00005":  # an answer that is not an HTML paper
            return httpx.Response(200, headers={"Content-Type": "application/pdf"}, text="%PDF-")
        return httpx.Response(200, headers={"Content-Type": "text/html; charset=utf-8"}, text=page)

    load, asked = _real_fetch(answer)
    stats = survey_job(conn, Model(), data, run_id, load=load)
    assert asked == ["2601.00003", "2601.00001", "2601.00005", "2601.00006", "2601.00007"]
    assert "arxiv_down" not in stats and stats["fulltext"] == 2
    assert stats["abstract_fallback"]["arxiv:2601.00003"] == "fetch arXiv HTML: http status: 404"


@pytest.mark.parametrize("outage", ["connect", "timeout", "503"])
def test_an_unreachable_arxiv_is_given_up_after_two_outages(run, data: Path, outage: str) -> None:
    conn, run_id = run
    _more_arxiv_papers(conn, run_id)

    def answer(id: str, request: httpx.Request) -> httpx.Response:
        if outage == "connect":
            raise httpx.ConnectError("connection refused", request=request)
        if outage == "timeout":
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(503, text="overloaded")

    load, asked = _real_fetch(answer)
    stats = survey_job(conn, Model(), data, run_id, load=load)
    assert len(asked) == 2 and stats["arxiv_down"] is True
    assert stats["abstract_only"] == 6 and len(stats["abstract_fallback"]) == 5
    assert list(stats["abstract_fallback"].values())[2:] == ["skipped: arXiv did not answer the papers before"] * 3


def test_an_unfinished_screening_is_named_in_the_report(run, data: Path) -> None:
    conn, run_id = run
    runs.set_status(conn, run_id, "failed")
    conn.execute("UPDATE screen_verdicts SET decision = 'unscreened', doc = ? WHERE key = ?",
                 (json.dumps({"decision": "unscreened"}), B["key"]))
    stats = survey_job(conn, Model(), data, run_id, load=Arxiv())
    md = (data / stats["report"]).read_text()
    assert stats["unscreened"] == 1 and stats["included"] == 2
    assert "> 注意：运行状态为 failed，纳入集可能不完整；另有 1 篇候选未完成筛选，未计入。" in md


def test_an_invalid_extractor_setting_is_refused(run, data: Path, monkeypatch) -> None:
    conn, run_id = run
    monkeypatch.setenv("ISBE_FULLTEXT_EXTRACTOR", "pdf")
    with pytest.raises(EnrichError, match="must be llm, metrail or off"):
        survey_job(conn, Model(), data, run_id, load=Arxiv())
