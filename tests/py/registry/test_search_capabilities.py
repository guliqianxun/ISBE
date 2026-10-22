"""What SearXNG and each fixed-source kind can enforce, and the routing the query
syntax builds on."""

import json
import sqlite3
from pathlib import Path

import pytest
from registry_helpers import plan, source

from isbe import search
from isbe.core import settings
from isbe.core.errors import HttpError
from isbe.search import capabilities, query
from isbe.search.capabilities import SUPPORTED, UNKNOWN, UNSUPPORTED
from isbe.search.plans import BAIDU_REMOVED
from isbe.sources import registry_config
from isbe.sources.reader import Kind

FIXTURE = Path(__file__).parents[3] / "examples" / "search" / "capabilities.json"


def test_the_r2_fixture_matches_the_code_and_routes_as_recorded() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert fixture["revision"] == capabilities.REVISION
    assert fixture["sources"] == capabilities.describe_all()
    for example in fixture["routing"]:
        routed = capabilities.route(fixture["sources"][example["source"]], example["wanted"])
        assert routed == example["expected"], example["source"]
        if "query" in example:
            line, region, institution = example["query"], example["region"], example["institution"]
            assert query.compile(line, region, institution) == example["sent"]
            assert query.constraints(line, region, institution) == example["wanted"]


def test_every_provider_and_kind_states_every_constraint_with_a_basis() -> None:
    described = capabilities.describe_all()
    providers = {p.value for p in search.Provider if p != search.Provider.BAIDU}  # Baidu: removed
    assert set(described) == providers | {k.value for k in Kind}
    for name, description in described.items():
        assert description["id"] == name and description["revision"] == capabilities.REVISION
        assert list(description["constraints"]) == list(capabilities.CONSTRAINTS)
        for field in description["constraints"].values():
            assert field["status"] in (SUPPORTED, UNSUPPORTED, UNKNOWN) and field["basis"]
    # The scope fields the issue names have an explicit status on the search provider.
    constraints = described["searxng"]["constraints"]
    assert constraints["region"]["status"] == UNSUPPORTED
    assert constraints["pages"]["status"] == UNSUPPORTED
    assert constraints["publication_dates"]["status"] == UNSUPPORTED


def test_unknown_is_never_executed() -> None:
    for description in capabilities.describe_all().values():
        routed = capabilities.route(description, [*capabilities.CONSTRAINTS, "not-described"])
        statuses = {name: field["status"] for name, field in description["constraints"].items()}
        assert all(statuses[name] == SUPPORTED for name in routed["executed"])
        assert sorted(routed["executed"] + routed["deferred"]) == sorted([*capabilities.CONSTRAINTS, "not-described"])
        assert set(routed["unknown"]) == {n for n in routed["deferred"] if statuses.get(n) != UNSUPPORTED}
        assert "not-described" in routed["unknown"]
    # A status the description does not spell out counts as unknown; repeats route once.
    odd = {"constraints": {"terms": {"status": "maybe", "basis": "x"}}}
    assert capabilities.route(odd, ["terms", "terms"]) == {
        "executed": [], "deferred": ["terms"], "unknown": ["terms"],
    }


def test_descriptions_hold_setting_names_never_values() -> None:
    before = capabilities.describe_all()
    private = {"SEARXNG_URL": "http://10.1.2.3:8888/instance", "GITHUB_TOKEN": "secret-key-123"}
    with settings.task_scope(private):
        during = capabilities.describe_all()
    text = json.dumps(during, ensure_ascii=False)
    assert during == before
    for value in private.values():
        assert value not in text
    assert "/home/" not in text and "/data" not in text
    assert during["searxng"]["requires"] == ["SEARXNG_URL"]
    # A description handed out is a copy: changing it leaves the next one intact.
    during["searxng"]["constraints"]["region"]["status"] = SUPPORTED
    assert capabilities.describe("searxng")["constraints"]["region"]["status"] == UNSUPPORTED


def test_plan_and_source_descriptions_carry_their_config_revisions(conn: sqlite3.Connection) -> None:
    p = plan()
    search.save(conn, p, None, None)
    first = capabilities.for_plan(conn, p.id)
    assert first == {
        "plan": p.id,
        "plan_revision": search.get(conn, p.id)["revision"],
        "capabilities": capabilities.describe("searxng"),
    }
    search.save(conn, p.model_copy(update={"language": "zh-CN"}), first["plan_revision"], p.id)
    second = capabilities.for_plan(conn, p.id)
    assert second["plan_revision"] != first["plan_revision"]
    assert second["capabilities"] == first["capabilities"]
    # A plan still saved for the removed Baidu provider has no description.
    old = p.model_copy(update={"id": "old-baidu", "provider": search.Provider.BAIDU})
    conn.execute("INSERT INTO search_plans(id,config) VALUES (?1,?2)", (old.id, old.dumps()))
    with pytest.raises(HttpError) as removed:
        capabilities.for_plan(conn, old.id)
    assert (removed.value.status, removed.value.message) == (400, BAIDU_REMOVED)

    registry_config.save(conn, source("city"), None, None)
    bound = capabilities.for_source(conn, "city")
    assert bound == {
        "source": "city",
        "source_revision": registry_config.get(conn, "city")["revision"],
        "capabilities": capabilities.describe("rss"),
    }
    for lookup in (capabilities.for_plan, capabilities.for_source):
        with pytest.raises(HttpError) as missing:
            lookup(conn, "absent")
        assert missing.value.status == 404
    for name in ("tavily", "baidu"):
        with pytest.raises(KeyError):
            capabilities.describe(name)
