import hashlib
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from isbe.core import settings
from isbe.topics import (
    Cadence,
    Flavor,
    Lang,
    Topic,
    TopicError,
    check_id,
    load,
    load_dir,
    parse,
    parse_with_env,
    scan_dir,
)
from isbe.topics.model import _expand_env

EXAMPLES = Path(__file__).resolve().parents[3] / "examples" / "topics"
MIN = "id: t\nlabel: T\ncadence: weekly\narxiv: {categories: [cs.CV], include_keywords: [denoising]}\n"


def env(name: str) -> str | None:
    return "http://set" if name == "SET" else None


def topic(yaml: str, stem: str = "t") -> Topic:
    return parse_with_env(yaml, stem, env)


def error(yaml: str, stem: str = "t") -> str:
    with pytest.raises(TopicError) as e:
        topic(yaml, stem)
    return str(e.value)


def test_examples_load() -> None:
    topics = load_dir(EXAMPLES)
    assert len(topics) >= 2
    assert any(t.flavor() == Flavor.PAPERS for t in topics)
    assert any(t.flavor() == Flavor.ARTICLES for t in topics)
    nowcasting = next(t for t in topics if t.id == "nowcasting")
    assert nowcasting.lang == Lang.ZH and nowcasting.schedule_timezone == "Asia/Shanghai"
    assert nowcasting.metrail is not None and nowcasting.metrail.table_backend == "docling"
    assert "radar echo" in nowcasting.arxiv.include_keywords  # type: ignore[union-attr]


def test_defaults() -> None:
    t = topic(MIN)
    assert (t.lang, t.window_days, t.schedule_timezone, t.active, t.cadence) == (Lang.EN, 7, "UTC", True, Cadence.WEEKLY)
    assert t.arxiv is not None and t.arxiv.max_results == 50
    assert (t.digest.max_papers, t.digest.fulltext_per_paper_chars, t.digest.fulltext_total_chars) == (25, 3000, 24000)
    assert t.schedules.collect is None and t.retrieval is None and t.metrail is None


@pytest.mark.parametrize(
    ("yaml", "message"),
    [
        (MIN + "typo: 1\n", "line 5 column 1: unknown field `typo`, expected one of id, label, lang, cadence, active,"),
        (MIN + 'schedules: {digest: "0 8 * *"}\n', 'schedules.digest: bad cron "0 8 * *": Invalid pattern: Pattern must have between 5 and 7 fields.'),
        (MIN + 'schedules: {collect: "0 6 * * *", nightly: "0 1 * * *"}\n', "unknown field `nightly`, expected one of collect, digest, monthly"),
        (MIN + "schedule_timezone: Asia/Shanghia\n", 'unknown IANA timezone "Asia/Shanghia"'),
        (MIN + "github: {repos: [justname]}\n", 'github repo "justname" must be owner/name'),
        ("id: t\nlabel: T\ncadence: weekly\n", "no source: add an arxiv, rss or zhihu block"),
        (MIN.replace("id: t", "id: other"), "id does not match file name"),
        (MIN.replace("[denoising]", "[]"), "arxiv.include_keywords is empty: list the phrases a paper must mention"),
    ],
    ids=["unknown key", "bad cron", "unknown schedule", "bad timezone", "bad repo", "no source", "id != file name", "no keywords"],
)
def test_rejects_bad_config(yaml: str, message: str) -> None:
    assert message in error(yaml)


def test_env_expansion() -> None:
    assert _expand_env("${SET}/feed", env) == "http://set/feed"
    assert _expand_env("${UNSET:-http://d}/feed", env) == "http://d/feed"
    assert _expand_env("no vars", env) == "no vars"
    for bad, message in [
        ("${UNSET}", "undefined env var UNSET"),
        ("${UNSET:?set the feed host}", "requires env var UNSET: set the feed host"),
        ("${SET", 'unclosed ${ in "${SET"'),
    ]:
        with pytest.raises(ValueError, match=r"^" + message.replace("$", r"\$").replace("{", r"\{") + "$"):
            _expand_env(bad, env)
    assert _expand_env("${EMPTY:-fallback}", lambda _: "") == "fallback", "empty counts as missing"


def test_portable_ids_and_bounded_work() -> None:
    for id in ["", "../t", "a/b", "a\\b", "UPPER", "-t", "con", "aux", "com1", "lpt9", "论文"]:
        with pytest.raises(TopicError):
            check_id(id)
    with pytest.raises(TopicError):
        check_id("a" * 65)
    check_id("a" * 64)
    check_id("research_2026-v2")
    check_id("com0")
    for yaml, message in [
        (MIN.replace("label: T", "label: '  '"), "label must not be empty"),
        (MIN + "window_days: 0\n", "window_days must be between 1 and 3650"),
        (MIN + "window_days: 3651\n", "window_days must be between 1 and 3650"),
        (MIN.replace("categories:", "max_results: 0, categories:"), "arxiv.max_results must be between 1 and 2000"),
        (MIN.replace("categories:", "max_results: 2001, categories:"), "arxiv.max_results must be between 1 and 2000"),
        (MIN + "digest: {max_papers: 0}\n", "digest.max_papers must be between 1 and 200"),
        (MIN + "digest: {max_papers: 201}\n", "digest.max_papers must be between 1 and 200"),
        (MIN + "rss: {lookback_days: 0}\n", "rss.lookback_days must be between 1 and 3650"),
        (MIN + "metrail: {poll_timeout_s: 0}\n", "metrail.poll_timeout_s must be between 1 and 86400"),
    ]:
        assert error(yaml) == message


def test_validation_errors_never_include_expanded_secrets() -> None:
    text = "id: t\nlabel: T\ncadence: daily\nrss:\n  feeds:\n    - name: Feed\n      url: '${TOKEN}/${MISSING}'\n"
    with pytest.raises(TopicError) as e:
        parse_with_env(text, "t", lambda key: "do-not-reveal-this" if key == "TOKEN" else None)
    assert str(e.value) == 'rss feed "Feed": undefined env var MISSING'
    assert "do-not-reveal-this" not in str(e.value)


def test_feed_urls_expand_from_the_service_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "_saved", {"ESSAIES_URL": "https://saved.test"})
    text = "id: t\nlabel: T\ncadence: daily\nrss: {feeds: [{name: F, url: '${ESSAIES_URL}/rss'}]}\n"
    assert parse(text, "t").rss.feeds[0].url == "https://saved.test/rss"  # type: ignore[union-attr]
    with settings.task_scope({"ESSAIES_URL": "https://task.test"}):
        assert parse(text, "t").rss.feeds[0].url == "https://task.test/rss"  # type: ignore[union-attr]


def test_flavor_and_window_start() -> None:
    papers = topic(MIN + "window_days: 3\n")
    assert papers.flavor() == Flavor.PAPERS
    articles = topic("id: t\nlabel: T\ncadence: daily\nrss: {feeds: []}\n")
    assert articles.flavor() == Flavor.ARTICLES
    now = datetime(2026, 10, 20, 1, 30, tzinfo=timezone(timedelta(hours=8)))  # 2026-10-19 17:30 UTC
    assert papers.since(now) == datetime(2026, 10, 16, tzinfo=UTC)
    with pytest.raises(ValueError):
        papers.since(datetime(2026, 10, 20))


def test_strings_stay_strings_where_yaml_1_1_would_retype_them() -> None:
    t = topic(
        "id: 2026-10-16\nlabel: 2026-10-16\ncadence: weekly\n"
        "arxiv: {categories: [1.10, 0x1F], include_keywords: [yes, no, on, off, y, n, true, ~x, 12:30, 1_000, 017]}\n"
        "retrieval: {intent: no, require_any: [NO, Off, 2026-10-16T10:00:00Z]}\n",
        "2026-10-16",
    )
    assert t.id == t.label == "2026-10-16"
    assert t.arxiv is not None and t.arxiv.categories == ["1.10", "0x1F"]
    assert t.arxiv.include_keywords == ["yes", "no", "on", "off", "y", "n", "true", "~x", "12:30", "1_000", "017"]
    assert t.retrieval is not None and t.retrieval.intent == "no"
    assert t.retrieval.require_any == ["NO", "Off", "2026-10-16T10:00:00Z"]


def test_typed_fields_accept_the_established_values() -> None:
    # The earlier build read booleans with YAML 1.1 spellings and integers from
    # quoted, hex, octal, binary and underscored text; files relying on
    # that must keep loading.
    for spelling, value in [("yes", True), ("No", False), ("ON", True), ("off", False), ("'false'", False), ("y", True)]:
        assert topic(MIN + f"active: {spelling}\n").active is value
    for spelling, value in [("'7'", 7), ("0x10", 16), ("0o10", 8), ("0b101", 5), ("1_000", 1000), ("+7", 7)]:
        assert topic(MIN + f"window_days: {spelling}\n").window_days == value
    for spelling in ["1", "t", "''", "~", "!!str true"]:
        assert error(MIN + f"active: {spelling}\n").endswith("invalid boolean")
    for spelling in ["7.0", "010", "-1", "1e1", "1__0", "_1", "0x_1F", "4294967296", "null", "!!str 7"]:
        assert error(MIN + f"window_days: {spelling}\n").endswith("invalid u32"), spelling
    assert topic(MIN + "digest: {fulltext_total_chars: 18446744073709551615}\n").digest.fulltext_total_chars == 2**64 - 1
    assert error(MIN + "digest: {fulltext_total_chars: 18446744073709551616}\n").endswith("invalid u64")
    for spelling in ["!!float 1", "!!bool yes"]:
        assert error(MIN + f"window_days: {spelling}\n").endswith("invalid u32"), spelling
    # An enum may also be written as a one-entry `{variant: null}` mapping.
    for spelling in ["{zh: }", "{zh: ~}", "{'zh': !!null ''}"]:
        assert topic(MIN + f"lang: {spelling}\n").lang == "zh"


@pytest.mark.parametrize(
    ("spelling", "message"),
    [
        ("", "unknown variant `~`"),
        ("''", "unknown variant ``"),
        ("!!null ''", "unknown variant ``"),
        ('"a\\tb\\x1b"', "unknown variant `a\\tb\\u{1b}`"),
        ('"a\\\\b"', "unknown variant `a\\b`"),
        ("[en]", "externally tagged enum expected scalar or mapping"),
        ("{}", "expected string key for externally tagged enum"),
        ("{x: 1}", "unknown variant `x`"),
        ("{en: []}", "unexpected value for unit enum variant"),
        ("{en: , zh: }", "expected end of mapping after enum variant value"),
    ],
)
def test_enum_errors_use_the_strict_decoder_wording(spelling: str, message: str) -> None:
    assert message in error(MIN + f"lang: {spelling}\n")


@pytest.mark.parametrize(
    ("yaml", "message"),
    [
        ("id: t\nid: t\nlabel: T\ncadence: weekly\n" + MIN.split("\n")[3], "line 2 column 1: duplicate mapping key: id"),
        (MIN.replace("[denoising]}", "[x], categories: [y]}"), "line 4 column 53: duplicate mapping key: categories"),
        (MIN.replace("label: T", "label: null"), "line 2 column 8: cannot deserialize null into string; use Option<String>"),
        (MIN.replace("label: T", "label: [a]"), "line 2 column 8: expected string scalar"),
        (MIN.replace("cs.CV]", "cs.CV], typo: 1"), "unknown field `typo`, expected one of categories, include_keywords, max_results"),
        (MIN + "rss: {feeds: [{name: F, url: u, custom: keep}]}\n", "unknown field `custom`, expected one of name, url"),
        (MIN + "lang: ZH\n", "line 5 column 7: unknown variant `ZH`, expected one of en, zh"),
        (MIN + "github: {}\n", "missing field `repos`"),
        (MIN + "arxiv2: {}\n", "unknown field `arxiv2`"),
        ("- a\n", "line 1 column 1: expected mapping start"),
        (MIN + "---\n" + MIN, "expected a single document in the stream"),
        ("", "line 1 column 1: unexpected end of input"),
        ("---\n", "missing field `id`"),
        ("id: [broken", "line 1 column 12:"),
    ],
)
def test_yaml_errors_name_the_place_and_the_problem(yaml: str, message: str) -> None:
    assert message in error(yaml)


def test_merge_keys_aliases_and_json_documents_parse() -> None:
    merged = topic("<<: {id: t, label: Base}\nlabel: Own\ncadence: weekly\narxiv: {categories: &c [cs.CV], include_keywords: *c}\n")
    assert merged.label == "Own" and merged.arxiv is not None and merged.arxiv.include_keywords == ["cs.CV"]
    assert "unknown field `typo`" in error("<<: {id: t, typo: 1}\nlabel: T\ncadence: weekly\n" + MIN.split("\n")[3])
    json_text = '{"id": "t", "label": "T", "cadence": "weekly", "arxiv": {"categories": ["a"], "include_keywords": ["\\ud83d\\ude00"]}}'
    assert topic(json_text).arxiv.include_keywords == ["\N{GRINNING FACE}"]  # type: ignore[union-attr]
    assert topic("\N{ZERO WIDTH NO-BREAK SPACE}" + MIN.replace("\n", "\r\n")).label == "T"


def test_null_blocks_take_their_defaults() -> None:
    t = topic(MIN + "rss:\nschedules: null\ndigest: ~\nmetrail: {table_backend: null, base_url: ''}\n")
    assert t.rss is None and t.schedules.collect is None and t.digest.max_papers == 25
    assert t.metrail is not None and t.metrail.table_backend is None and t.metrail.base_url == ""
    assert error("id: t\nlabel: T\ncadence: weekly\narxiv: {categories: null, include_keywords: [x]}\n") == "arxiv.categories is empty"
    assert "missing field `name`" in error("id: t\nlabel: T\ncadence: weekly\nrss: {feeds: [null]}\n")


def test_merge_bombs_and_deep_nesting_are_refused() -> None:
    chain = ["&m0 {intent: x}"] + [f"&m{i} {{<<: [{', '.join([f'*m{i - 1}'] * 10)}]}}" for i in range(1, 8)]
    assert "document expands to too many nodes" in error(MIN + "retrieval:\n  <<: [" + ", ".join(chain) + "]\n")
    assert "document is nested too deeply" in error(MIN + "retrieval: {intent: x, facets: " + "[" * 2000 + "]" * 2000 + "}\n")


def test_retrieval_contract_matches_the_cache_key() -> None:
    # SHA-256 of the compact JSON of `topic.retrieval` as the earlier Rust build wrote it:
    # equal text keeps the triage cache valid across the upgrade.
    digests = {
        "nowcasting": "e94cc7801a5f56cd54064fe5d4c6c2008a53eb61261b17885c774dffb7821555",
        "rust-news": "b4f06179a6001d4bc35d018e8afd52554215affb7a7f33d041e3553ed711297c",
    }
    for stem, digest in digests.items():
        contract = load(EXAMPLES / f"{stem}.yaml").retrieval.contract_json()  # type: ignore[union-attr]
        assert hashlib.sha256(contract.encode()).hexdigest() == digest
    special = topic(
        MIN + "retrieval:\n"
        '  intent: "引号 \\" 反斜杠 \\\\ tab\\t nl\\n ctl\\x01 del\\x7f ls\\u2028 \\U0001F600 </script>"\n'
        "  version: 3\n  key_entities: {brands: [Acme], segments: []}\n  facets: [x, \"y z\"]\n"
    )
    contract = special.retrieval.contract_json()  # type: ignore[union-attr]
    assert hashlib.sha256(contract.encode()).hexdigest() == "e552ff4d8effea7ed09421eea02bb3f3785d70d775c36ad9a96419774932a017"


def test_load_names_the_file_and_checks_the_stem(tmp_path: Path) -> None:
    (tmp_path / "t.yaml").write_text(MIN)
    assert load(tmp_path / "t.yaml").id == "t"
    (tmp_path / "other.yml").write_text(MIN)
    with pytest.raises(TopicError, match=r"other\.yml: id does not match file name$"):
        load(tmp_path / "other.yml")
    (tmp_path / "bin.yaml").write_bytes(b"id: \xff\n")
    with pytest.raises(TopicError, match="stream did not contain valid UTF-8"):
        load(tmp_path / "bin.yaml")


def test_scan_dir_skips_a_broken_file_and_reports_it(tmp_path: Path) -> None:
    (tmp_path / "good.yaml").write_text(MIN.replace("id: t", "id: good"))
    (tmp_path / "broken.yaml").write_text("id: [broken")
    (tmp_path / "wrong.yml").write_text(MIN)
    (tmp_path / "notes.txt").write_text("not a topic")
    topics, errors = scan_dir(tmp_path)
    assert [t.id for t in topics] == ["good"]
    assert len(errors) == 2
    assert errors[0].startswith(f"{tmp_path / 'broken.yaml'}: line 1 column 12:")
    assert errors[1] == f"{tmp_path / 'wrong.yml'}: id does not match file name"
    with pytest.raises(TopicError) as strict:
        load_dir(tmp_path)
    assert str(strict.value).startswith("2 of 3 topic files are invalid:\n\n")
    assert errors[0] in str(strict.value) and errors[1] in str(strict.value)
    with pytest.raises(TopicError, match="read topics dir"):
        scan_dir(tmp_path / "missing")
