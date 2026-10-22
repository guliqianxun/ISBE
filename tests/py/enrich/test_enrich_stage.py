"""The enrich stage: paper selection, extractor modes, stats."""

import json
import sqlite3
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

import httpx
import pytest

from isbe import topics
from isbe.core import db, http, jsonfmt
from isbe.core.errors import describe
from isbe.pipeline.enrich import EnrichError, cards, run
from isbe.pipeline.enrich.arxiv_html import Paper, parse
from isbe.pipeline.enrich.stage import extractor, fetch_html
from isbe.topics import Lang

PAPER_TOPIC = "id: t\nlabel: T\ncadence: weekly\narxiv: {categories: [cs.CV], include_keywords: [x]}\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"pixels"
HTML = ("<article class='ltx_document'><h1>Test</h1><p>We train a simple model on public observations.</p>"
        "<figure class='ltx_figure'><img src='2401.00001v1/x1.png'><figcaption>Figure 1: Our framework.</figcaption></figure>"
        "</article>")


def test_selects_html_by_default_and_preserves_configured_metrail() -> None:
    assert extractor(None, False) == "llm"
    assert extractor(None, True) == "metrail"
    assert extractor(" llm ", True) == "llm"
    assert extractor("off", True) == "off"
    assert extractor("metrail", False) == "metrail"
    assert extractor(" ", True) == "metrail"
    with pytest.raises(EnrichError, match="ISBE_FULLTEXT_EXTRACTOR must be llm, metrail or off"):
        extractor("unknown", False)


def _paper(body: str) -> Paper:
    return Paper(body, [], [])


def test_stores_html_and_records_unavailable_papers_without_failing(
    conn: sqlite3.Connection, add_paper: Any
) -> None:
    for id in ["arxiv:2401.00001", "arxiv:2401.00002"]:
        add_paper(id)
    fallback: dict[str, Any] = {}
    figure_failures: dict[str, Any] = {}

    def load(id: str) -> Paper:
        if id.endswith("2"):
            raise EnrichError("HTML unavailable")
        return parse("<article class='ltx_document'><h1>Test paper</h1><p>We train on public observations.</p></article>")

    def broken(_: Any) -> dict[str, Any]:
        raise EnrichError("image unavailable")

    n = fetch_html(conn, [("arxiv:2401.00001", False), ("arxiv:2401.00002", False)], load, broken,
                   fallback, figure_failures)
    assert n == (1, 0)
    assert fallback["arxiv:2401.00002"] == "HTML unavailable"
    assert figure_failures["arxiv:2401.00001"] == "image unavailable"
    via, body, figure = conn.execute("SELECT via, body, figure FROM fulltext").fetchone()
    assert via == "arxiv-html" and "We train on public observations." in body and figure is None
    # Adding pictures to cached text must not replace its evidence or invalidate cards.
    n = fetch_html(conn, [("arxiv:2401.00001", True)], lambda _: _paper("new conversion"),
                   lambda _: {"files": ["figures/test.png"], "caption": "A framework"},
                   fallback, figure_failures)
    assert n == (0, 1)
    assert tuple(conn.execute("SELECT body, figure FROM fulltext").fetchone()) == (
        body, '{"caption":"A framework","files":["figures/test.png"]}')
    fallback, figure_failures = {}, {}

    def gone(_: str) -> Paper:
        raise EnrichError("HTML temporarily unavailable")

    def unreachable(_: Any) -> dict[str, Any]:
        raise AssertionError

    fetch_html(conn, [("arxiv:2401.00001", True)], gone, unreachable, fallback, figure_failures)
    assert fallback == {} and "arxiv:2401.00001" in figure_failures
    # A failed write must not be mislabeled as an unavailable paper.
    with pytest.raises(sqlite3.IntegrityError):
        fetch_html(conn, [("missing-item", False)], lambda _: _paper("text"), lambda _: {},
                   fallback, figure_failures)
    assert "missing-item" not in fallback
    oversized = ("<article class='ltx_document'><p>Evidence stays available.</p><figure class='ltx_figure'>"
                 + "<img src='panel.png'>" * 13 + "<figcaption>Figure 1: Framework</figcaption></figure></article>")

    def none_left(found: Any) -> dict[str, Any]:
        assert found == []
        return {}

    counts = fetch_html(conn, [("arxiv:2401.00002", False)], lambda _: parse(oversized), none_left,
                        fallback, figure_failures)
    assert counts == (1, 0)
    assert "12" in figure_failures["arxiv:2401.00002"]
    body, figure = conn.execute(
        "SELECT body, figure FROM fulltext WHERE item_id = 'arxiv:2401.00002'").fetchone()
    assert "Evidence stays available." in body
    assert figure == "{}", "permanently unsupported groups do not retry every run"


def test_html_reuses_fast_model_checks_evidence_and_caches_cards(
    serve: Any, conn: sqlite3.Connection, chat: Any, read_json: Any, local_llm: Any, add_paper: Any
) -> None:
    inputs: list[Any] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        assert req.path == "/chat/completions"
        inputs.append(read_json(req))
        serve.reply(req, 200, chat({
            "method": {"value": "A simple observation model", "quote": "We train a simple model on public observations."},
            "results": {"value": "Accuracy 98.2%", "quote": "Ours reaches 98.2% accuracy."},
            "compute": {"value": "Uses 999 GPUs", "quote": "We train a simple model on public observations."},
        }))

    llm = local_llm(serve(handle), OPENAI_API_KEY="local-mock-key", ISBE_LLM_NO_AUTH="",
                    ISBE_LLM_MODEL_FAST="test-fast")
    add_paper("arxiv:2401.00001")
    html = ("<article class='ltx_document'><h1>Test</h1><p>We train a simple model on public observations.</p>"
            "<p>Ours reaches 98.2% accuracy.</p></article>")
    fetch_html(conn, [("arxiv:2401.00001", False)], lambda _: parse(html), lambda _: {}, {}, {})

    def twice(run: str, _: dict[str, Any]) -> None:
        assert cards.refresh(conn, llm, run, Lang.EN, "arxiv:2401.00001") == 1
        assert cards.refresh(conn, llm, run, Lang.EN, "arxiv:2401.00001") is None

    db.job(conn, "enrich", "test", twice)
    assert len(inputs) == 1
    assert inputs[0]["model"] == "test-fast"
    assert "Ours reaches 98.2% accuracy." in inputs[0]["messages"][1]["content"]
    card = json.loads(conn.execute("SELECT card FROM cards").fetchone()[0])
    assert card["fields"]["results"]["value"] == "Accuracy 98.2%"
    assert card["rejected"] == ["compute"]
    assert conn.execute("SELECT COUNT(*) FROM llm_calls WHERE purpose = 'cards'").fetchone()[0] == 1


def _kept(conn: sqlite3.Connection, add_paper: Any, ids: list[str], extra: str = "{}") -> None:
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for id in ids:
        add_paper(id, published=now, extra=extra)
        conn.execute("INSERT INTO triage (topic_id, item_id, contract_sha, verdict, tier, reason, judged_at) "
                     "VALUES ('t', ?, 'sha', 'kept', 'core', 'r', ?)", (id, now))


def _run(conn: sqlite3.Connection, llm: Any, topic: Any, data: Path, essaies: str | None = None,
         **options: Any) -> tuple[dict[str, Any], BaseException | None]:
    outcome: dict[str, Any] = {}
    try:
        db.job(conn, "enrich", "t", lambda run_id, stats: (
            outcome.update(stats=stats),
            run(conn, llm, run_id, topic, data, essaies, stats, sleep=lambda _: None, **options),
        ))
    except Exception as error:
        return outcome["stats"], error
    return outcome["stats"], None


def test_off_and_articles_topics_skip_with_the_stored_stats(
    conn: sqlite3.Connection, add_paper: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _kept(conn, add_paper, ["arxiv:2401.00001", "arxiv:2401.00002", "arxiv:2401.00003"])
    monkeypatch.setenv("ISBE_FULLTEXT_EXTRACTOR", "off")
    stats, error = _run(conn, None, topics.parse(PAPER_TOPIC, "t"), tmp_path)
    assert error is None
    assert jsonfmt.dumps(stats) == (
        '{"extractor":"off","papers":3,"skipped":"full-text fetching and new cards disabled"}'
    )
    articles = topics.parse("id: t\nlabel: T\ncadence: daily\nrss: {feeds: []}\n", "t")
    stats, error = _run(conn, None, articles, tmp_path)
    assert error is None and jsonfmt.dumps(stats) == '{"skipped":"not a papers topic"}'
    monkeypatch.setenv("ISBE_FULLTEXT_EXTRACTOR", "metrail")
    stats, error = _run(conn, None, topics.parse(PAPER_TOPIC, "t"), tmp_path)
    assert str(error) == (
        "metrail requires METRAIL_API_URL or topic metrail.base_url and metrail.enabled"
    )
    disabled = topics.parse(PAPER_TOPIC + "metrail: {base_url: 'http://m', enabled: false}\n", "t")
    stats, error = _run(conn, None, disabled, tmp_path)
    assert error is not None and stats == {"extractor": "metrail", "papers": 3}


def test_html_mode_extracts_figures_falls_back_and_builds_cards(
    conn: sqlite3.Connection, add_paper: Any, serve: Any, chat: Any, read_json: Any,
    local_llm: Any, tmp_path: Path
) -> None:
    _kept(conn, add_paper, ["arxiv:2401.00001", "arxiv:2401.00002"])
    requested: list[str] = []

    def arxiv(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/html/2401.00002":
            return httpx.Response(404)
        if request.url.path.endswith(".png"):
            return httpx.Response(200, headers={"Content-Type": "image/png"}, content=PNG)
        return httpx.Response(200, headers={"Content-Type": "text/html"}, text=HTML)

    def model(req: BaseHTTPRequestHandler) -> None:
        read_json(req)
        serve.reply(req, 200, chat({"method": {"value": "A simple model",
                                               "quote": "We train a simple model on public observations."}}))

    client = http.Http(5, transport=httpx.MockTransport(arxiv))
    topic = topics.parse(PAPER_TOPIC, "t")
    stats, error = _run(conn, local_llm(serve(model)), topic, tmp_path, client=client)
    assert error is None, error
    assert stats == {
        "extractor": "llm", "papers": 2, "extracted": 1, "figures": 1, "cards": 1, "rejected_facts": 0,
        "abstract_fallback": {"arxiv:2401.00002": "fetch arXiv HTML: http status: 404"},
    }
    figure = json.loads(conn.execute("SELECT figure FROM fulltext").fetchone()[0])
    assert figure["caption"] == "Figure 1: Our framework."
    assert (tmp_path / figure["files"][0]).read_bytes() == PNG
    assert requested == [
        "https://arxiv.org/html/2401.00001",
        "https://arxiv.org/html/2401.00001v1/x1.png",
        "https://arxiv.org/html/2401.00002",
    ]
    # Second run: the text and figure are in, the card is current; only the
    # unavailable paper is tried again.
    requested.clear()
    stats, error = _run(conn, local_llm(serve(model)), topic, tmp_path, client=client)
    assert error is None and requested == ["https://arxiv.org/html/2401.00002"]
    assert stats["extracted"] == 0 and stats["cards"] == 0


def test_card_failures_fail_the_run_after_recording_stats(
    conn: sqlite3.Connection, add_paper: Any, serve: Any, read_json: Any, local_llm: Any,
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _kept(conn, add_paper, ["arxiv:2401.00001", "arxiv:2401.00002"])
    for id in ["arxiv:2401.00001", "arxiv:2401.00002"]:
        conn.execute("INSERT INTO fulltext (item_id, via, body, extracted_at, figure) "
                     "VALUES (?, 'arxiv-html', 'b', 'x', '{}')", (id,))

    def refuse(req: BaseHTTPRequestHandler) -> None:
        read_json(req)
        serve.reply(req, 400, "{}")

    monkeypatch.setenv("ISBE_FULLTEXT_EXTRACTOR", "llm")
    stats, error = _run(conn, local_llm(serve(refuse)), topics.parse(PAPER_TOPIC, "t"), tmp_path)
    assert str(error) == "2 of 2 papers failed"
    assert stats["failed"]["arxiv:2401.00001"].startswith("card: cards call to ")
    assert stats["cards"] == 0 and stats["extracted"] == 0
    row = conn.execute("SELECT status, error FROM runs").fetchone()
    assert tuple(row) == ("failed", "2 of 2 papers failed")


def _metrail(serve: Any, counts: dict[str, int], figure_type: str) -> str:
    def handle(req: BaseHTTPRequestHandler) -> None:
        path = req.path
        counts[path.split("?")[0]] = counts.get(path.split("?")[0], 0) + 1
        if req.command == "POST":
            req.rfile.read(int(req.headers["Content-Length"]))
            serve.reply(req, 200, '{"id": "d1"}')
        elif path.startswith("/api/file?"):
            serve.reply(req, 200, b"%PDF-1.4 x")
        elif path == "/api/documents/d1":
            serve.reply(req, 200, '{"state": "done"}')
        elif path == "/api/documents/d1/atoms":
            serve.reply(req, 200, json.dumps([
                {"page": 0, "kind": "text", "text": "We train a simple model on public observations."},
                {"page": 0, "kind": "figure", "text": "Figure 1: Our framework.",
                 "metadata": {"caption_detected": "yes"}, "image_url": "/img/1.png"},
            ]))
        elif path == "/img/1.png?w=960":
            serve.reply(req, 200, PNG, {"Content-Type": figure_type})
        else:
            serve.reply(req, 404)

    return str(serve(handle))


def test_metrail_figure_with_content_type_parameters_is_not_refetched(
    conn: sqlite3.Connection, add_paper: Any, serve: Any, chat: Any, read_json: Any,
    local_llm: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _kept(conn, add_paper, ["arxiv:2401.00001"], extra='{"path": "a.pdf"}')
    counts: dict[str, int] = {}
    base = _metrail(serve, counts, "image/png; charset=binary")
    monkeypatch.setenv("METRAIL_API_URL", base + "/")

    def model(req: BaseHTTPRequestHandler) -> None:
        read_json(req)
        serve.reply(req, 200, chat({"method": {"value": "A simple model",
                                               "quote": "We train a simple model on public observations."}}))

    llm = local_llm(serve(model))
    topic = topics.parse(PAPER_TOPIC, "t")
    stats, error = _run(conn, llm, topic, tmp_path, base)
    assert error is None, error
    assert jsonfmt.dumps(stats) == (
        '{"cards":1,"extracted":1,"extractor":"metrail","figures":1,"papers":1,"rejected_facts":0}'
    )
    row = conn.execute("SELECT via, figure FROM fulltext").fetchone()
    assert tuple(row) == ("metrail", '{"caption":"Figure 1: Our framework.","file":"figures/2401.00001.png"}')
    assert (tmp_path / "figures" / "2401.00001.png").read_bytes() == PNG
    stats, error = _run(conn, llm, topic, tmp_path, base)
    assert error is None
    assert counts["/api/documents"] == 1, "the PDF is uploaded once"
    assert counts["/api/file"] == 1
    assert stats["extracted"] == 0 and stats["cards"] == 0


def test_metrail_failures_name_the_document(
    conn: sqlite3.Connection, add_paper: Any, serve: Any, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path
) -> None:
    _kept(conn, add_paper, ["arxiv:2401.00001", "rss:x"], extra='{"path": "a.pdf"}')

    def handle(req: BaseHTTPRequestHandler) -> None:
        if req.command == "POST":
            req.rfile.read(int(req.headers["Content-Length"]))
            serve.reply(req, 200, '{"id": "d9"}')
        elif req.path.startswith("/api/file"):
            serve.reply(req, 200, b"%PDF-1.4 x")
        else:
            serve.reply(req, 200, '{"state": "error", "error": "corrupt PDF"}')

    base = serve(handle)
    topic = topics.parse(PAPER_TOPIC + f"metrail: {{base_url: '{base}'}}\n", "t")
    stats, error = _run(conn, None, topic, tmp_path, base)
    assert str(error) == "2 of 2 papers failed"
    # No full text means no card to build, so the metrail failure stands.
    assert stats["failed"] == {"arxiv:2401.00001": "metrail document d9: corrupt PDF",
                               "rss:x": "metrail document d9: corrupt PDF"}
    assert stats["extracted"] == 0
    # Without essaies an arXiv paper's PDF comes from arxiv.org; other papers have none.
    seen: list[str] = []

    def offline(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(404)

    client = http.Http(2, transport=httpx.MockTransport(offline))
    stats, error = _run(conn, None, topic, tmp_path, None, client=client)
    assert describe(error) == "2 of 2 papers failed"
    assert stats["failed"] == {"arxiv:2401.00001": "download paper PDF: http status: 404",
                               "rss:x": "no PDF to fetch"}
    assert seen == ["https://arxiv.org/pdf/2401.00001"]
