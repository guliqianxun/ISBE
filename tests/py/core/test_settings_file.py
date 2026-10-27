import json
from pathlib import Path

import pytest

from isbe import cli
from isbe.core import settings, settings_file
from isbe.core.validators import Invalid

EXAMPLE = Path(__file__).parents[3] / "examples" / "settings.example.json"


def test_the_example_file_reads_as_the_frontend_test_expects() -> None:
    # frontend/src/settings/file.test.ts pins the same changes for this file.
    parsed = settings_file.parse(EXAMPLE.read_bytes())
    assert parsed.unknown == []
    assert parsed.changes == {
        "ISBE_FULLTEXT_EXTRACTOR": "llm",
        "ISBE_LLM_BASE_URL": "http://127.0.0.1:8080/v1",
        "ISBE_LLM_EXTRA_BODY": '{"chat_template_kwargs":{"enable_thinking":false}}',
        "ISBE_LLM_MODEL_FAST": "example-fast-model",
        "ISBE_LLM_MODEL_SMART": "example-smart-model",
        "ISBE_LLM_PROVIDER": "openai",
        "ISBE_LLM_TIMEOUT_S": "900",
        "ISBE_PAPER_SOURCE": "openalex",
        "OPENALEX_MAILTO": "you@example.org",
        "SEARXNG_URL": None,
        "SMTP_PORT": "465",
    }


def test_a_bare_settings_json_reads_and_unknown_names_are_listed() -> None:
    raw = b'{"ESSAIES_URL": "http://e.invalid", "NOPE": "x", "BAIDU_SEARCH_API_KEY": "old", "ISBE_LLM_NO_AUTH": true}'
    parsed = settings_file.parse(raw)
    assert parsed.changes == {"ESSAIES_URL": "http://e.invalid", "ISBE_LLM_NO_AUTH": "true"}
    assert parsed.unknown == ["NOPE"], "retired names are dropped quietly"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not json", "not valid JSON"),
        (b"[1]", "must be a JSON object"),
        (b'{"format": "other", "version": 1, "settings": {}}', '"format" must be'),
        (b'{"format": "isbe-settings", "version": 2, "settings": {}}', "only version 1"),
        (b'{"format": "isbe-settings", "version": 1}', '"settings" must be an object'),
        (b'{"ESSAIES_URL": ["a"]}', "ESSAIES_URL must be a string"),
        (b'{"ESSAIES_URL": {"a": 1}}', "ESSAIES_URL must be a string"),
        (b" " * (settings.MAX_FILE + 1), "larger than 64 KB"),
    ],
)
def test_files_that_are_not_settings_files_are_refused(raw: bytes, message: str) -> None:
    with pytest.raises(Invalid, match=message):
        settings_file.parse(raw)


def test_export_leaves_secrets_out_unless_asked(data: Path) -> None:
    settings.save(data, {"ESSAIES_URL": "http://e.invalid", "OPENAI_API_KEY": "sk-secret"})
    doc, invalid = settings_file.export(data)
    assert invalid == []
    assert doc == {
        "format": "isbe-settings",
        "version": 1,
        "settings": {"ESSAIES_URL": "http://e.invalid"},
        "secrets_omitted": ["OPENAI_API_KEY"],
    }
    assert settings_file.export(data, secrets=True)[0]["settings"]["OPENAI_API_KEY"] == "sk-secret"


def test_cli_import_saves_the_file_and_prints_names_only(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENALEX_MAILTO", "env@example.org")
    settings.save(data, {"SEARXNG_URL": "http://old.invalid"})
    file = tmp_path / "in.json"
    file.write_bytes(
        EXAMPLE.read_bytes()
        .replace(b'"SMTP_PORT"', b'"UNKNOWN_NAME"')
        .replace(b'"OPENALEX_MAILTO"', b'"OPENAI_API_KEY": "sk-file-only", "OPENALEX_MAILTO"')
    )
    assert cli.main(["--data", str(data), "settings", "import", str(file)]) == 0
    out = capsys.readouterr().out
    assert "sk-file-only" not in out
    assert "set      OPENALEX_MAILTO (the environment overrides it)\n" in out
    assert "cleared  SEARXNG_URL\n" in out
    assert "skipped  UNKNOWN_NAME (unknown setting)\n" in out
    assert out.endswith("imported: 10 set, 1 cleared, 1 skipped\n")
    saved = settings.read(data)
    assert saved["OPENAI_API_KEY"] == "sk-file-only"
    assert saved["ISBE_LLM_EXTRA_BODY"] == '{"chat_template_kwargs":{"enable_thinking":false}}'
    assert "SEARXNG_URL" not in saved


def test_cli_import_of_an_invalid_value_saves_nothing(
    data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings.save(data, {"ESSAIES_URL": "http://kept.invalid"})
    before = (data / "settings.json").read_bytes()
    file = tmp_path / "in.json"
    file.write_text(json.dumps({"ESSAIES_URL": "http://new.invalid", "ISBE_LLM_TIMEOUT_S": "soon"}))
    assert cli.main(["--data", str(data), "settings", "import", str(file)]) == 1
    assert "ISBE_LLM_TIMEOUT_S" in capsys.readouterr().err
    assert (data / "settings.json").read_bytes() == before


def test_cli_export_round_trips_through_import(data: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    settings.save(data, {"ESSAIES_URL": "http://e.invalid", "GITHUB_TOKEN": "ghp-x", "ISBE_LLM_MAX_TOKENS": "4096"})
    assert cli.main(["--data", str(data), "settings", "export"]) == 0
    exported = capsys.readouterr().out
    assert "ghp-x" not in exported
    assert json.loads(exported)["secrets_omitted"] == ["GITHUB_TOKEN"]
    assert cli.main(["--data", str(data), "settings", "export", "--secrets"]) == 0
    file = tmp_path / "out.json"
    file.write_text(capsys.readouterr().out)
    other = tmp_path / "other"
    assert cli.main(["--data", str(other), "settings", "import", str(file)]) == 0
    assert settings.read(other) == settings.read(data)


@pytest.mark.parametrize(
    "argv",
    [["settings", "import"], ["settings", "export", "x.json"], ["settings", "import", "x.json", "--secrets"]],
)
def test_cli_settings_refuses_misplaced_arguments(data: Path, argv: list[str]) -> None:
    assert cli.main(["--data", str(data), *argv]) == 1


def test_export_includes_environment_overrides(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.save(data, {"ESSAIES_URL": "http://file.invalid"})
    monkeypatch.setenv("ESSAIES_URL", "http://env.invalid")
    monkeypatch.setenv("ISBE_LLM_MODEL_FAST", "env-model")
    assert settings_file.export(data)[0]["settings"] == {
        "ESSAIES_URL": "http://env.invalid",
        "ISBE_LLM_MODEL_FAST": "env-model",
    }


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b'{"ISBE_LLM_TIMEOUT_S": 900.0}', {"ISBE_LLM_TIMEOUT_S": "900"}),
        (b'{"ISBE_LLM_TIMEOUT_S": -3}', {"ISBE_LLM_TIMEOUT_S": "-3"}),
        (
            b'{"ISBE_LLM_EXTRA_BODY": {"t": 1.0, "p": 0.5, "a": [2.0, 1e21]}}',
            {"ISBE_LLM_EXTRA_BODY": '{"a":[2,1e+21],"p":0.5,"t":1}'},
        ),
    ],
)
def test_numbers_read_as_javascript_reads_them(raw: bytes, expected: dict[str, str]) -> None:
    # frontend/src/settings/file.test.ts pins the same texts.
    assert settings_file.parse(raw).changes == expected


@pytest.mark.parametrize(
    "raw",
    [b'{"ISBE_LLM_TIMEOUT_S": 1.5}', b'{"ISBE_LLM_TIMEOUT_S": 1e21}', b'{"SMTP_PORT": 9007199254740992}'],
)
def test_numbers_that_are_not_whole_are_refused(raw: bytes) -> None:
    with pytest.raises(Invalid, match="must be a whole number"):
        settings_file.parse(raw)


def test_version_must_be_the_number_one() -> None:
    with pytest.raises(Invalid, match="only version 1"):
        settings_file.parse(b'{"format": "isbe-settings", "version": true, "settings": {}}')


def test_export_leaves_out_an_invalid_environment_value_and_says_so(
    data: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.save(data, {"ISBE_LLM_TIMEOUT_S": "120", "ESSAIES_URL": "http://e.invalid"})
    monkeypatch.setenv("ISBE_LLM_TIMEOUT_S", "abc")
    assert cli.main(["--data", str(data), "settings", "export"]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["settings"] == {"ESSAIES_URL": "http://e.invalid", "ISBE_LLM_TIMEOUT_S": "120"}
    assert out.err == "warning: ISBE_LLM_TIMEOUT_S in the environment is not valid; left out\n"
