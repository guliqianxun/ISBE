"""`isbe sources …`: arguments, output and exit status."""

from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from isbe import cli
from isbe.core import db, jsonfmt

FEED = (
    '<rss version="2.0"><channel><title>City news</title><link>https://example.com</link>'
    "<description>news</description><item><title>Public policy update</title>"
    "<link>https://example.com/article</link></item></channel></rss>"
)


def sources(data: Path, *args: str) -> int:
    return cli.main(["--data", str(data), "sources", *args])


def test_configuration_commands_print_the_established_text(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert sources(data, "list") == 0
    assert capsys.readouterr().out == "[]\n"
    file = data / "sources.yaml"
    file.write_text(
        "sources:\n"
        "  - {id: city, name: City news, topic: 上海, kind: rss, url: 'https://Example.com/feed'}\n"
        "  - id: page\n    name: Notices\n    topic: 上海\n    kind: page\n    url: https://example.com/n/\n"
        "    enabled: no\n    max_items: 5\n",
        encoding="utf-8",
    )
    assert sources(data, "import", str(file)) == 0
    assert capsys.readouterr().out == '{"imported":2}\n'
    assert sources(data, "export") == 0
    exported = capsys.readouterr().out
    assert exported.startswith('{\n  "sources": [\n    {\n      "enabled": true,\n      "id": "city",')
    assert jsonfmt.loads(exported)["sources"][1] == {
        "id": "page", "name": "Notices", "topic": "上海", "kind": "page", "url": "https://example.com/n/",
        "enabled": False, "link_pattern": "", "max_items": 5,
    }  # fmt: skip
    assert sources(data, "list") == 0
    listed = jsonfmt.loads(capsys.readouterr().out)
    assert [(s["config"]["id"], s["site"], s["revision"], s["origin"]) for s in listed] == [
        ("city", "example.com", "1", "manual"),
        ("page", "example.com", "1", "manual"),
    ]
    # Existing ids are never overwritten.
    assert sources(data, "import", str(file)) == 1
    assert capsys.readouterr().err == "Error: Source ID city already exists; no sources were imported\n"


def test_search_plans_are_saved_listed_and_revised(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = data / "plan.yaml"
    file.write_text(
        "id: esg\nname: ESG\ntopic: 上海\nprovider: searxng\nqueries: [上海 ESG]\n"
        "start_date: '2026-10-01'\nend_date: '2026-10-18'\nlanguage: zh-CN\nmax_results: 5\n",
        encoding="utf-8",
    )
    assert sources(data, "search-save", str(file)) == 0
    saved = capsys.readouterr().out
    assert saved.startswith('{"config":{"end_date":"2026-10-18","id":"esg",')
    assert saved.endswith(',"item_count":0,"last_checked":null,"last_status":null,"revision":"1"}\n')
    assert sources(data, "search-save", str(file), "--revision", "1") == 0
    assert jsonfmt.loads(capsys.readouterr().out)["revision"] == "2"
    assert sources(data, "search-save", str(file)) == 1
    assert capsys.readouterr().err == "Error: Search plan ID already exists\n"
    assert sources(data, "search-list") == 0
    assert jsonfmt.loads(capsys.readouterr().out)[0]["config"]["queries"] == ["上海 ESG"]
    big = data / "big.yaml"
    big.write_bytes(b"#" * (64 * 1024 + 1))
    assert sources(data, "search-save", str(big)) == 1
    assert capsys.readouterr().err == "Error: Search configuration must not exceed 64 KiB\n"
    assert sources(data, "import", str(data / "missing.yaml")) == 1
    assert capsys.readouterr().err == "Error: No such file or directory (os error 2)\n"
    # A run needs SEARXNG_URL; nothing is started without it.
    assert sources(data, "search", "esg") == 1
    assert capsys.readouterr().err == "Error: Configure SEARXNG_URL before searching\n"
    conn = db.connect(data / "isbe.db")
    assert conn.execute("SELECT count(*) FROM source_jobs").fetchone()[0] == 0
    conn.close()


def test_discover_and_collect_wait_for_their_jobs(
    data: Path, serve, local_sources: None, capsys: pytest.CaptureFixture[str]
) -> None:
    def handle(req: BaseHTTPRequestHandler) -> None:
        serve.reply(req, 200, FEED) if req.path == "/feed" else serve.reply(req, 500, "down")

    port = serve(handle).rsplit(":", 1)[1]
    url = f"http://feed.test:{port}/feed"
    assert sources(data, "discover", url, "--topic", " Shanghai ") == 0
    job = jsonfmt.loads(capsys.readouterr().out)
    assert job["id"].startswith("cli-") and job["id"].endswith("-0")
    assert (job["operation"], job["state"], job["error"]) == ("discover", "succeeded", None)
    (candidate,) = job["stats"]["candidates"]
    assert (candidate["state"], candidate["entries"], candidate["name"]) == ("enabled", 1, "City news")
    assert sources(data, "collect") == 0  # every enabled source
    collected = jsonfmt.loads(capsys.readouterr().out)
    assert collected["stats"] == {
        "found": 1, "new": 0, "source_id": candidate["id"], "truncated": False, "unchanged": 1, "updated": 0,
    }  # fmt: skip
    conn = db.connect(data / "isbe.db")
    conn.execute(
        "UPDATE source_definitions SET config=json_set(config,'$.url',?1)", (url.replace("/feed", "/down"),)
    )
    conn.close()
    assert sources(data, "collect", candidate["id"]) == 1
    captured = capsys.readouterr()
    failed = jsonfmt.loads(captured.out)
    assert (failed["state"], failed["error"]) == ("failed", "http status: 500")
    assert captured.err == "Error: 1 source operations failed or were incomplete\n"
    assert sources(data, "discover", url, "--topic", "Beijing", "--candidates-only") == 0
    again = jsonfmt.loads(capsys.readouterr().out)
    assert again["stats"]["candidates"][0]["state"] == "candidate"


def test_recheck_and_arguments(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    file = data / "plan.json"
    file.write_text(
        jsonfmt.dumps(
            {"id": "esg", "name": "ESG", "topic": "上海", "provider": "searxng", "queries": ["上海"],
             "start_date": "2026-10-01", "end_date": "2026-10-18", "language": "zh-CN", "max_results": 5}
        )
    )  # fmt: skip
    assert sources(data, "search-save", str(file)) == 0
    capsys.readouterr()
    assert sources(data, "recheck", "esg") == 0
    job = jsonfmt.loads(capsys.readouterr().out)
    assert (job["operation"], job["state"]) == ("recheck", "succeeded")
    assert job["stats"]["checked"] == 0 and job["stats"]["next_cursor"] is None
    assert sources(data, "recheck", "esg", "--after", "nope") == 1
    assert capsys.readouterr().err == "Error: Invalid date-recheck cursor\n"
    with pytest.raises(SystemExit):
        sources(data, "discover", "https://example.com/")  # --topic is required
    with pytest.raises(SystemExit):
        sources(data)
    assert sources(data, "list", "--data", str(data)) == 0  # the global --data option
