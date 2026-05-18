"""Tests for the make_articles_digester factory in _shared/articles_digester.py.

The factory replaces what used to be two near-identical 197-line modules
(motorcycle/digester.py and china_tech/digester.py). Behavior parity is
covered by the broader scheduler+dispatch suite; here we lock the factory's
own contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from isbe.topics._shared.articles_digester import make_articles_digester


def _noop_builder(**kwargs) -> str:
    return "prompt-body"


def test_factory_returns_a_prefect_flow_with_given_name(tmp_path: Path) -> None:
    tpl = tmp_path / "weekly.j2"
    tpl.write_text("{{ topic_id }} | {{ second_bucket }}", encoding="utf-8")
    digester = make_articles_digester(
        topic_id="x",
        flow_name="x-digester",
        system_prompt="SYS",
        prompt_builder=_noop_builder,
        second_bucket_kind="brand_notes",
        template_path=tpl,
    )
    assert digester.name == "x-digester"
    assert hasattr(digester, "to_deployment")  # is an actual Prefect flow


def test_two_invocations_produce_distinct_flows(tmp_path: Path) -> None:
    """Two different factories must yield independent flows; closing-over
    must not bleed config from one into the other."""
    tpl_a = tmp_path / "a.j2"
    tpl_a.write_text("a", encoding="utf-8")
    tpl_b = tmp_path / "b.j2"
    tpl_b.write_text("b", encoding="utf-8")

    a = make_articles_digester(
        topic_id="a",
        flow_name="a-digester",
        system_prompt="SYS-A",
        prompt_builder=_noop_builder,
        second_bucket_kind="brand_notes",
        template_path=tpl_a,
    )
    b = make_articles_digester(
        topic_id="b",
        flow_name="b-digester",
        system_prompt="SYS-B",
        prompt_builder=_noop_builder,
        second_bucket_kind="company_notes",
        template_path=tpl_b,
    )
    assert a is not b
    assert a.name == "a-digester"
    assert b.name == "b-digester"


@pytest.mark.parametrize(
    "topic_id, expected_flow_name, expected_second_bucket",
    [
        ("motorcycle", "motorcycle-digester", "brand_notes"),
        ("china-tech", "china-tech-digester", "company_notes"),
    ],
)
def test_existing_topics_factory_matches_expected_contract(
    topic_id: str, expected_flow_name: str, expected_second_bucket: str
) -> None:
    """The two shipped topics must keep their previous flow.name and
    second-bucket kind after migration to the factory."""
    from isbe.topics.dispatch import resolve_flow

    flow, _ = resolve_flow(topic_id, "digester")
    assert flow.name == expected_flow_name
    # Each shipped digester exposes its bucket kind for introspection.
    assert getattr(flow, "_isbe_second_bucket_kind", None) == expected_second_bucket
