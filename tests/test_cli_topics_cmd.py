from typer.testing import CliRunner

from isbe.cli.main import app


def test_radar_topics_list_includes_nowcasting():
    runner = CliRunner()
    result = runner.invoke(app, ["topics", "list"])
    assert result.exit_code == 0
    assert "nowcasting" in result.stdout
    assert "weekly" in result.stdout


def test_radar_topics_run_unknown_topic_fails():
    runner = CliRunner()
    result = runner.invoke(app, ["topics", "run", "nonexistent", "--collect"])
    assert result.exit_code != 0
    assert "unknown topic" in result.stdout.lower() or "unknown topic" in result.stderr.lower()


def test_collect_chains_metrail_enrich_after_pdfs(monkeypatch):
    """--collect on a topic that schedules metrail_enrich must run the chain;
    --no-pdfs must skip it. Flows are stubbed via dispatch."""
    from unittest.mock import MagicMock

    from isbe.cli import topics_cmd

    calls: list[str] = []

    def fake_resolve(topic_id, key):
        fn = MagicMock(return_value=0)
        fn.side_effect = lambda **kw: calls.append(key) or 0
        return fn, {"topic_id": topic_id}

    monkeypatch.setattr(topics_cmd, "resolve_flow", fake_resolve)
    runner = CliRunner()

    result = runner.invoke(app, ["topics", "run", "nowcasting", "--collect"])
    assert result.exit_code == 0, result.output
    assert "metrail_enrich" in calls
    assert "arxiv_download_pdfs" in calls

    calls.clear()
    result = runner.invoke(app, ["topics", "run", "nowcasting", "--collect", "--no-pdfs"])
    assert result.exit_code == 0, result.output
    assert "arxiv_download_pdfs" not in calls
    # extraction needs no downloads — --no-pdfs must NOT suppress it
    assert "metrail_enrich" in calls
