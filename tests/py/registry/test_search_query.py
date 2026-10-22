"""Scholar-style query syntax, what a run sends, and the constraints it
leaves to check in the results. Request building makes no network call."""

import json
import sqlite3
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from registry_helpers import plan

from isbe import search
from isbe.core import settings
from isbe.core.errors import HttpError
from isbe.search import capabilities, providers, query, runner


def refused(line: str) -> str:
    with pytest.raises(HttpError) as error:
        query.parse(line)
    assert error.value.status == 400
    return error.value.message


def test_scholar_syntax_in_chinese_english_and_mixed_queries() -> None:
    zh = query.parse('上海 “绿色金融” ESG OR 可持续 -招聘')
    assert (zh.terms, zh.phrases, zh.any, zh.excluded) == (["上海"], ["绿色金融"], [["ESG", "可持续"]], ["招聘"])
    assert zh.text == '上海 "绿色金融" ESG OR 可持续 -招聘'
    en = query.parse('Shanghai   "green   finance" intitle:"transition plan" site:gov.cn -"job ad"')
    assert (en.terms, en.phrases, en.intitle, en.sites, en.excluded) == (
        ["Shanghai"], ["green finance"], ["transition plan"], ["gov.cn"], ['"job ad"'],
    )
    assert en.text == 'Shanghai "green finance" intitle:"transition plan" site:gov.cn -"job ad"'
    mixed = query.parse('"carbon market" OR 碳市场 OR "emissions trading" 上海')
    assert mixed.any == [['"carbon market"', "碳市场", '"emissions trading"']]
    assert mixed.terms == ["上海"] and mixed.phrases == []
    assert query.constraints('"carbon market" OR 碳市场 上海') == [
        "terms", "phrase", "boolean", "language", "publication_window",
    ]


def test_lowercase_or_is_a_word_not_an_operator() -> None:
    ambiguous = query.parse("ESG or green")
    assert ambiguous.terms == ["ESG", "or", "green"] and ambiguous.any == []
    assert "boolean" not in query.constraints("ESG or green")
    # Hyphens inside a word or a date range are not exclusions.
    plain = query.parse("low-carbon 2024-2026")
    assert plain.terms == ["low-carbon", "2024-2026"] and plain.excluded == []


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ('上海 "绿色金融', "close every quotation mark"),
        ('ESG ""', "a quoted phrase is empty"),
        ("OR ESG", "OR must join two words or phrases"),
        ("ESG OR", "OR must join two words or phrases"),
        ("ESG OR OR green", "OR must join two words or phrases"),
        ("ESG OR -green", "OR must join two words or phrases"),
        ("ESG OR site:gov.cn", "OR must join two words or phrases"),
        ("ESG - green", "a minus sign must be followed"),
        ("ESG site:", "site: needs a value"),
        ('ESG site:"gov cn"', "site: takes a domain"),
        ("ESG intitle:", "intitle: needs a value"),
        ("ESG author:Zhang", "author: is a Google Scholar field"),
        ("ESG Source:Nature", "source: is a Google Scholar field"),
        ("-招聘 site:gov.cn", "add at least one word or phrase"),
    ],
)
def test_unsupported_or_malformed_syntax_is_refused_with_the_problem(line: str, problem: str) -> None:
    message = refused(line)
    assert problem in message and line in message


def test_scholar_only_fields_suggest_quoting_the_name() -> None:
    assert 'put the name in quotes instead, e.g. "Name"' in refused("ESG author:Zhang")


def test_region_and_institution_are_sent_as_phrases_once_and_listed_for_checking() -> None:
    assert query.compile("ESG  disclosure", "上海", "上海证券交易所") == 'ESG disclosure "上海" "上海证券交易所"'
    # Already in the query: not added again (case-insensitive).
    assert query.compile("上海 ESG", "上海") == "上海 ESG"
    assert query.compile('"Hong Kong" ESG', "hong kong") == '"Hong Kong" ESG'
    assert query.compile("ESG") == "ESG"
    names = query.constraints("上海 ESG", "上海", "上海证券交易所")
    assert names == ["terms", "institution", "region", "language", "publication_window"]
    assert names == [n for n in capabilities.CONSTRAINTS if n in names]
    routed = capabilities.route(capabilities.describe("searxng"), names)
    assert routed == {
        "executed": ["terms"],
        "deferred": ["institution", "region", "language", "publication_window"],
        "unknown": ["language"],
    }


def test_plan_validation_parses_queries_and_checks_region_and_institution() -> None:
    p = plan()
    with pytest.raises(HttpError, match="close every quotation mark"):
        p.model_copy(update={"queries": ['"ESG']}).validated()
    clean = p.model_copy(update={"region": "  上海 ", "institution": " 上交所 "}).validated()
    assert (clean.region, clean.institution) == ("上海", "上交所")
    for bad in ("x" * 61, 'Shang"hai', "上海“", "a\x07b"):
        with pytest.raises(HttpError, match="Region and institution allow up to 60 characters"):
            p.model_copy(update={"region": bad}).validated()
    assert "region" not in p.to_json() and "institution" not in p.to_json()
    assert clean.to_json()["region"] == "上海" and list(clean.to_json())[-2:] == ["region", "institution"]


def test_a_plan_saved_before_region_existed_still_runs(conn: sqlite3.Connection) -> None:
    p = plan()
    # Stored exactly as older plans were: no region or institution members.
    stored = json.dumps({k: v for k, v in p.model_dump(mode="json").items() if k not in ("region", "institution")})
    conn.execute("INSERT INTO search_plans(id,config) VALUES (?1,?2)", (p.id, stored))
    loaded = search.SearchPlan.model_validate(search.get(conn, p.id)["config"])
    assert search.validate_saved(conn, loaded) == p
    with_region = search.save(conn, p.model_copy(update={"region": "上海"}), "1", p.id)
    assert with_region["config"]["region"] == "上海" and "institution" not in with_region["config"]


def test_a_run_sends_the_compiled_query_and_records_what_was_not_applied(
    conn: sqlite3.Connection, serve: Any
) -> None:
    p = plan().model_copy(
        update={"queries": ['Shanghai "green finance" OR ESG -jobs', "Shanghai ESG"], "region": "Shanghai"}
    )
    search.save(conn, p, None, None)
    conn.execute(
        "INSERT INTO source_jobs(id,fingerprint,operation,state,created_at) "
        "VALUES ('r2','fingerprint','search','running','now')"
    )
    search.snapshot(conn, "r2", p)
    seen: list[str] = []

    def handle(req: BaseHTTPRequestHandler) -> None:
        seen.append(parse_qs(urlsplit(req.path).query)["q"][0])
        serve.reply(req, 200, '{"results":[]}', {})

    with settings.task_scope({"SEARXNG_URL": serve(handle)}):
        stats = runner.run_with(conn, "r2", p, providers.request, lambda *_: {})
    assert seen == ['Shanghai "green finance" OR ESG -jobs', "Shanghai ESG"]
    written = [r[0] for r in conn.execute("SELECT query FROM search_requests WHERE run_id='r2' ORDER BY query_index")]
    assert written == p.queries
    first, second = stats["queries"]
    assert first["sent"] == 'Shanghai "green finance" OR ESG -jobs'
    assert first["constraints"] == {
        "executed": ["terms"],
        "deferred": ["phrase", "boolean", "exclusion", "region", "language", "publication_window"],
        "unknown": ["phrase", "boolean", "exclusion", "language"],
    }
    assert second["constraints"]["deferred"] == ["region", "language", "publication_window"]
    assert stats["unexecuted"] == ["phrase", "boolean", "exclusion", "region", "language", "publication_window"]
