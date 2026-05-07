from datetime import UTC, datetime

from isbe.topics.base import (
    DigestResult,
    DigestSection,
    PendingMemoryDraft,
    TopicMetadata,
)


def test_topic_metadata_minimal():
    m = TopicMetadata(id="nowcasting", label="临近降水预报", cadence="weekly", active=True)
    assert m.id == "nowcasting"
    assert m.cadence == "weekly"


def test_digest_result_three_sections():
    sections = [
        DigestSection(kind="facts", body="本周 12 篇..."),
        DigestSection(kind="analysis", body="重点新增..."),
        DigestSection(kind="distillation", body="新论点候选..."),
    ]
    result = DigestResult(
        topic_id="nowcasting",
        period_label="2026-W19",
        generated_at=datetime.now(UTC),
        sections=sections,
        fingerprint={"facts": [1, 2, 3], "memory": {"nowcasting": 1}},
        pending_drafts=[],
    )
    assert {s.kind for s in result.sections} == {"facts", "analysis", "distillation"}


def test_pending_memory_draft_shape():
    draft = PendingMemoryDraft(
        target_type="topic",
        target_path="topics/nowcasting.theses.md",
        body="新论点：...",
        rationale="本期新证据",
    )
    assert draft.target_type == "topic"
