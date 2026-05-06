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
