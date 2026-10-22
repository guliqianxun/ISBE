import json
import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from isbe.core import paths, settings, validators
from isbe.core.validators import Invalid


def env_of(values: dict[str, str]) -> Callable[[str], str | None]:
    return values.get


def nothing(_: str) -> None:
    return None


def text(view: object) -> str:
    return json.dumps(view, ensure_ascii=False)


def test_task_settings_reload_freeze_overrides_and_restore_after_errors(data: Path) -> None:
    settings.save(data, {"ISBE_FULLTEXT_EXTRACTOR": "off", "ESSAIES_URL": "http://saved.invalid"})
    first = settings._for_task(data, env_of({"ESSAIES_URL": "http://environment.invalid"}))
    assert first["ESSAIES_URL"] == "http://environment.invalid"
    settings.save(data, {"ISBE_FULLTEXT_EXTRACTOR": "llm"})
    following = settings._for_task(data, nothing)
    assert following["ISBE_FULLTEXT_EXTRACTOR"] == "llm"
    with settings.task_scope(first):
        assert settings.get("ISBE_FULLTEXT_EXTRACTOR") == "off"
        assert settings.get("ESSAIES_URL") == "http://environment.invalid"
        with pytest.raises(RuntimeError):
            with settings.task_scope(following):
                assert settings.get("ISBE_FULLTEXT_EXTRACTOR") == "llm"
                raise RuntimeError("test unwind")
        assert settings.get("ISBE_FULLTEXT_EXTRACTOR") == "off"
        seen: list[str | None] = []

        def other_thread() -> None:
            with settings.task_scope({}):
                seen.append(settings.get("ESSAIES_URL"))

        worker = threading.Thread(target=other_thread)
        worker.start()
        worker.join()
        assert seen == [None]
        assert settings.get("ESSAIES_URL") == "http://environment.invalid"


def test_for_task_rejects_invalid_environment(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISBE_LLM_PROVIDER", "OpenAI")
    with pytest.raises(Invalid, match="ISBE_LLM_PROVIDER must be"):
        settings.for_task(data)
    monkeypatch.setenv("ISBE_LLM_PROVIDER", " openai ")
    assert settings.for_task(data)["ISBE_LLM_PROVIDER"] == "openai"


def test_snapshot_reports_shared_readiness_without_keys_or_connection_claims() -> None:
    saved = {"ISBE_LLM_PROVIDER": "openai", "ISBE_LLM_BASE_URL": "http://localhost:11434/v1"}
    incomplete = settings._snapshot(saved, nothing)
    assert incomplete["llm"]["ready"] is False
    assert "OPENAI_API_KEY" in incomplete["llm"]["error"]
    saved["ISBE_LLM_NO_AUTH"] = "true"
    ready = settings._snapshot(saved, nothing)
    assert ready["llm"]["ready"] is True
    assert ready["llm"]["error"] is None
    assert ready["llm"]["connection_verified"] is False
    assert ready["configured"]["OPENAI_API_KEY"] is False
    saved["OPENAI_API_KEY"] = "never-return-this-key"
    view = settings._snapshot(saved, nothing)
    assert "never-return-this-key" not in text(view)
    for provider in ["anthropic", "deepseek", "openai"]:
        base, fast, smart = validators.provider_defaults(provider)
        assert view["provider_defaults"][provider] == {
            "ISBE_LLM_BASE_URL": base,
            "ISBE_LLM_MODEL_FAST": fast,
            "ISBE_LLM_MODEL_SMART": smart,
        }
    invalid = settings._snapshot(saved, env_of({"ISBE_LLM_BASE_URL": "https://api.openai.com/v1"}))
    assert invalid["llm"]["ready"] is False
    assert "never-return-this-key" not in text(invalid)


def test_smtp_settings_defaults_validate_and_redact_passwords() -> None:
    view = settings._snapshot({}, nothing)
    assert view["values"]["EMAIL_TRANSPORT"] == "smtp"
    view = settings._snapshot(
        {"EMAIL_TRANSPORT": "smtp", "SMTP_PASSWORD": "never-return-smtp-password"}, nothing
    )
    assert view["configured"]["SMTP_PASSWORD"] is True
    assert "never-return-smtp-password" not in text(view)
    for key, value in [
        ("SMTP_PORT", "0"),
        ("SMTP_PORT", "65536"),
        ("SMTP_HOST", "smtp://host"),
        ("SMTP_HOST", "host:465"),
        ("SMTP_SECURITY", "none"),
        ("SMTP_PASSWORD", "secret\r\nheader"),
    ]:
        with pytest.raises(Invalid):
            settings.validate(key, value)
    for key, value in [
        ("SMTP_PORT", "587"),
        ("SMTP_HOST", "smtp.example.com"),
        ("SMTP_HOST", "::1"),
        ("SMTP_SECURITY", "starttls"),
    ]:
        assert settings.validate(key, value) == value


def test_mail_settings_save_validate_redact_and_clear(data: Path) -> None:
    settings.save(data, {"ISBE_EMAIL_DEFAULT_RECIPIENTS": "a@example.com; A@example.com"})
    view = settings._snapshot(settings.read(data), nothing)
    assert view["values"]["ISBE_EMAIL_DEFAULT_RECIPIENTS"] == "a@example.com"
    settings.save(
        data,
        {
            "ZEPTOMAIL_API_URL": "https://api.zeptomail.com/v1.1/email",
            "ZEPTOMAIL_TOKEN": "never-return-mail-token",
            "SMTP_FROM": "sender@example.com",
            "SMTP_FROM_NAME": "ISBE",
        },
    )
    view = settings._snapshot(settings.read(data), nothing)
    assert view["configured"]["ZEPTOMAIL_TOKEN"] is True
    assert "never-return-mail-token" not in text(view)
    assert view["values"]["SMTP_FROM"] == "sender@example.com"
    assert view["values"]["EMAIL_TRANSPORT"] == "zeptomail_api"
    before = (data / "settings.json").read_bytes()
    for key, value in [
        ("SMTP_FROM", "not-an-address"),
        ("ZEPTOMAIL_TOKEN", "Zoho-enczapikey "),
        ("ZEPTOMAIL_API_URL", "http://example.com/email"),
        ("ZEPTOMAIL_API_URL", "https://example.com/email?token=secret"),
        ("EMAIL_TRANSPORT", "unsupported"),
    ]:
        with pytest.raises(Invalid):
            settings.save(data, {key: value})
        assert (data / "settings.json").read_bytes() == before
    overridden = settings._snapshot(
        settings.read(data), env_of({"ZEPTOMAIL_TOKEN": "environment-mail-token"})
    )
    assert overridden["sources"]["ZEPTOMAIL_TOKEN"] == "environment"
    assert "environment-mail-token" not in text(overridden)
    settings.save(data, {"ZEPTOMAIL_TOKEN": None})
    assert settings._snapshot(settings.read(data), nothing)["configured"]["ZEPTOMAIL_TOKEN"] is False
    settings.save(data, {"ISBE_EMAIL_DEFAULT_RECIPIENTS": None})
    view = settings._snapshot(settings.read(data), nothing)
    assert view["values"]["ISBE_EMAIL_DEFAULT_RECIPIENTS"] == ""


def test_effective_values_prioritize_environment_and_redact_keys() -> None:
    saved = {
        "ISBE_LLM_PROVIDER": "deepseek",
        "DEEPSEEK_API_KEY": "stored-secret",
        "ESSAIES_URL": "http://saved:8080",
    }
    env = env_of(
        {
            "DEEPSEEK_API_KEY": "environment-secret",
            "ESSAIES_URL": " ",
            "GITHUB_API_URL": "http://github:9000",
        }
    )
    view = settings._snapshot(saved, env)
    assert view["values"]["ISBE_LLM_BASE_URL"] == "https://api.deepseek.com"
    assert view["values"]["ESSAIES_URL"] == "http://saved:8080"
    assert view["sources"]["ESSAIES_URL"] == "file"
    assert view["values"]["GITHUB_API_URL"] == "http://github:9000"
    assert view["sources"]["GITHUB_API_URL"] == "environment"
    assert view["sources"]["ISBE_LLM_TIMEOUT_S"] == "default"
    assert view["values"]["ISBE_LLM_TIMEOUT_S"] == "900"
    assert view["configured"]["DEEPSEEK_API_KEY"] is True
    assert view["configured"]["OPENAI_API_KEY"] is False
    assert "secret" not in text(view)
    assert "DEEPSEEK_API_KEY" not in view["values"]
    assert len(view["sources"]) == len(settings.KEYS)
    assert set(view["configured"]) == settings.SECRETS
    assert set(view) == {"values", "configured", "sources", "provider_defaults", "llm"}


def test_search_settings_validate_limits_and_never_expose_credentials() -> None:
    view = settings._snapshot({"SEARXNG_URL": "http://127.0.0.1:8888/"}, nothing)
    assert view["values"]["ISBE_ARTICLE_DNS"] == "system"
    for value in ["system", "cloudflare"]:
        assert settings.validate("ISBE_ARTICLE_DNS", value) == value
    with pytest.raises(Invalid):
        settings.validate("ISBE_ARTICLE_DNS", "http://localhost")
    assert view["values"]["SEARXNG_URL"] == "http://127.0.0.1:8888/"
    for value in [
        "http://user:secret@example.com",
        "http://example.com/?token=secret",
        "http://example.com/?",
        "file:///tmp/search",
        "http://example.com/#token",
        "http://example.com/has space",
    ]:
        with pytest.raises(Invalid) as caught:
            settings.validate("SEARXNG_URL", value)
        assert "secret" not in caught.value.message
    assert settings.validate("SEARXNG_URL", "http://127.0.0.1:8888") == "http://127.0.0.1:8888/"


def test_baidu_removed_settings_still_load_and_the_next_save_drops_them(data: Path) -> None:
    # Baidu search was removed on 2026-10-21; a settings.json saved before still holds its keys.
    (data / "settings.json").write_text(
        json.dumps({"BAIDU_SEARCH_API_KEY": "old-key", "ISBE_SEARCH_DAILY_LIMIT": "20", "SEARXNG_URL": "http://127.0.0.1:8888/"})
    )
    assert settings.read(data) == {"SEARXNG_URL": "http://127.0.0.1:8888/"}
    assert set(settings._for_task(data, nothing)) == {"SEARXNG_URL"}
    view = settings._snapshot(settings.read(data), nothing)
    assert settings.RETIRED.isdisjoint(view["values"].keys() | view["configured"].keys() | view["sources"].keys())
    assert "old-key" not in text(view)
    for name in settings.RETIRED:
        with pytest.raises(Invalid, match="^unsupported setting name$"):
            settings.validate(name, "1")
        with pytest.raises(Invalid):
            settings.save(data, {name: None})
    settings.save(data, {"ISBE_ARTICLE_DNS": "cloudflare"})
    assert json.loads((data / "settings.json").read_text()) == {
        "ISBE_ARTICLE_DNS": "cloudflare",
        "SEARXNG_URL": "http://127.0.0.1:8888/",
    }


def test_snapshot_trims_llm_environment_values_like_runtime() -> None:
    view = settings._snapshot(
        {}, env_of({"ISBE_LLM_PROVIDER": " openai ", "ISBE_LLM_MODEL_FAST": " custom-model "})
    )
    assert view["values"]["ISBE_LLM_PROVIDER"] == "openai"
    assert view["values"]["ISBE_LLM_MODEL_FAST"] == "custom-model"
    assert view["values"]["ISBE_LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert view["sources"]["ISBE_LLM_PROVIDER"] == "environment"


def test_partial_updates_preserve_keys_and_invalid_updates_preserve_file(data: Path) -> None:
    settings.save(data, {"OPENAI_API_KEY": "keep-me"})
    settings.save(data, {"ISBE_LLM_PROVIDER": "openai"})
    assert settings.read(data)["OPENAI_API_KEY"] == "keep-me"
    assert settings.snapshot(data)["configured"]["OPENAI_API_KEY"] is True
    original = (data / "settings.json").read_bytes()
    assert original == b'{\n  "ISBE_LLM_PROVIDER": "openai",\n  "OPENAI_API_KEY": "keep-me"\n}\n'
    for name, value in [
        ("OPENAI_API_KEY", "bad\nkey"),
        ("UNKNOWN", "value"),
        ("ISBE_LLM_PROVIDER", "other"),
        ("ISBE_FULLTEXT_EXTRACTOR", "pdf"),
        ("ESSAIES_URL", "file:///etc/passwd"),
        ("ESSAIES_URL", "https://user:password@example.com"),
        ("ESSAIES_URL", "https://example.com/#fragment"),
        ("ISBE_LLM_TIMEOUT_S", "0"),
        ("ISBE_LLM_TIMEOUT_S", "86401"),
        ("ISBE_LLM_MAX_TOKENS", "1000001"),
        ("ISBE_LLM_EXTRA_BODY", "[]"),
    ]:
        with pytest.raises(Invalid):
            settings.save(data, {name: value})
        assert (data / "settings.json").read_bytes() == original, name
    with pytest.raises(Invalid, match="exceeds 4096 bytes"):
        settings.save(data, {"OPENAI_API_KEY": "x" * 4097})
    settings.save(data, {"OPENAI_API_KEY": None})
    assert "OPENAI_API_KEY" not in settings.read(data)
    settings.save(data, {"ISBE_LLM_PROVIDER": ""})
    assert settings.read(data) == {}
    assert os.stat(data / "settings.json").st_mode & 0o777 == 0o600
    for malformed in [
        '"hidden-secret-value"',
        '{"OPENAI_API_KEY":["hidden-secret-value"]}',
        '{"hidden-secret-value":"oops"}',
        "{hidden-secret-value",
    ]:
        (data / "settings.json").write_text(malformed)
        with pytest.raises(Invalid) as caught:
            settings.read(data)
        assert "hidden-secret-value" not in str(caught.value)
    (data / "settings.json").write_bytes(b" " * (settings.MAX_FILE + 1))
    with pytest.raises(Invalid, match="too large"):
        settings.read(data)


def test_extractor_defaults_preserve_metrail_and_allow_explicit_selection() -> None:
    saved: dict[str, str] = {}
    assert settings._snapshot(saved, nothing)["values"]["ISBE_FULLTEXT_EXTRACTOR"] == "llm"
    saved["METRAIL_API_URL"] = "http://localhost:8000"
    assert settings._snapshot(saved, nothing)["values"]["ISBE_FULLTEXT_EXTRACTOR"] == "metrail"
    for mode in ["llm", "metrail", "off"]:
        saved["ISBE_FULLTEXT_EXTRACTOR"] = settings.validate("ISBE_FULLTEXT_EXTRACTOR", mode)  # type: ignore[assignment]
        assert settings._snapshot(saved, nothing)["values"]["ISBE_FULLTEXT_EXTRACTOR"] == mode
    view = settings._snapshot(saved, env_of({"ISBE_FULLTEXT_EXTRACTOR": " llm "}))
    assert view["values"]["ISBE_FULLTEXT_EXTRACTOR"] == "llm"
    assert view["sources"]["ISBE_FULLTEXT_EXTRACTOR"] == "environment"


def test_extra_body_accepts_formatted_objects_and_rejects_controls() -> None:
    assert (
        settings.validate("ISBE_LLM_EXTRA_BODY", '{\n\t"temperature": 0.2,\n "a": [1e2]\n}')
        == '{"a":[100.0],"temperature":0.2}'
    )
    with pytest.raises(Invalid):
        settings.validate("ISBE_LLM_EXTRA_BODY", '{"value":"bad\x00"}')
    with pytest.raises(Invalid):
        settings.validate("ISBE_LLM_BASE_URL", "https://example.com\n")


def test_concurrent_saves_preserve_independent_fields(data: Path) -> None:
    changes = [
        ("ISBE_LLM_MODEL_FAST", "fast-fixture"),
        ("ISBE_LLM_MODEL_SMART", "smart-fixture"),
        ("ISBE_LLM_PROVIDER", "openai"),
        ("ISBE_LLM_MAX_TOKENS", "1234"),
    ]
    barrier = threading.Barrier(len(changes))
    errors: list[BaseException] = []

    def save(key: str, value: str) -> None:
        barrier.wait()
        try:
            settings.save(data, {key: value})
        except BaseException as error:  # surfaced below
            errors.append(error)

    threads = [threading.Thread(target=save, args=change) for change in changes]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    saved = settings.read(data)
    for key, value in changes:
        assert saved[key] == value


def test_save_refreshes_the_process_cache_for_its_data_directory(data: Path) -> None:
    settings.init(data)
    settings.save(data, {"ESSAIES_URL": "http://saved:1"})
    assert settings.get("ESSAIES_URL") == "http://saved:1"
    other = data / "elsewhere"
    settings.save(other, {"ESSAIES_URL": "http://other:1"})
    assert settings.get("ESSAIES_URL") == "http://saved:1"


def test_issue38a_snapshot_survives_a_bad_environment_provider(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings.save(data, {"ESSAIES_URL": "http://saved:8080", "OPENAI_API_KEY": "file-secret"})
    for provider in ["OpenAI", "gemini"]:
        monkeypatch.setenv("ISBE_LLM_PROVIDER", provider)
        view = settings.snapshot(data)
        assert view["llm"]["ready"] is False
        assert view["llm"]["error"] == (
            f'ISBE_LLM_PROVIDER="{provider}": expected anthropic, deepseek or openai'
        )
        assert view["values"]["ISBE_LLM_PROVIDER"] == provider
        assert view["sources"]["ISBE_LLM_PROVIDER"] == "environment"
        assert view["values"]["ISBE_LLM_BASE_URL"] == ""
        assert view["values"]["ESSAIES_URL"] == "http://saved:8080"
        assert view["configured"]["OPENAI_API_KEY"] is True
        assert set(view["provider_defaults"]) == {"anthropic", "deepseek", "openai"}
        assert "file-secret" not in text(view)


def test_issue38b_a_crashed_save_does_not_block_later_saves(
    data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A fixed-name temporary file left behind by a crash (older builds), and an orphan of ours.
    (data / f".settings-{os.getpid()}.tmp").write_text("stale")
    (data / ".settings.json.0123456789abcdef.tmp").write_text("stale")
    settings.save(data, {"ESSAIES_URL": "http://one:1"})
    real_replace = os.replace

    def crash(source: object, target: object) -> None:
        raise OSError("simulated crash while replacing settings.json")

    monkeypatch.setattr(paths.os, "replace", crash)
    with pytest.raises(OSError, match="simulated crash"):
        settings.save(data, {"ESSAIES_URL": "http://two:2"})
    monkeypatch.setattr(paths.os, "replace", real_replace)
    assert settings.read(data)["ESSAIES_URL"] == "http://one:1"
    leftovers = sorted(p.name for p in data.glob(".settings.json.*.tmp"))
    assert leftovers == [".settings.json.0123456789abcdef.tmp"], "a failed save cleans its own temp"
    settings.save(data, {"ESSAIES_URL": "http://three:3"})
    assert settings.read(data)["ESSAIES_URL"] == "http://three:3"


@pytest.mark.parametrize(
    "field", ["model", "messages", "system", "stream", "max_tokens", "max_completion_tokens"]
)
def test_issue38c_extra_body_cannot_override_request_fields(data: Path, field: str) -> None:
    with pytest.raises(Invalid) as caught:
        settings.validate("ISBE_LLM_EXTRA_BODY", json.dumps({"temperature": 0, field: "x"}))
    assert f"must not set {field}" in caught.value.message
    settings.save(data, {"ISBE_LLM_EXTRA_BODY": '{"chat_template_kwargs":{"model":"nested ok"}}'})
    before = (data / "settings.json").read_bytes()
    with pytest.raises(Invalid):
        settings.save(data, {"ISBE_LLM_EXTRA_BODY": json.dumps({field: 1})})
    assert (data / "settings.json").read_bytes() == before
