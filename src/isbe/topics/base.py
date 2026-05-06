from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

DigestSectionKind = Literal["facts", "analysis", "distillation"]


@dataclass(frozen=True)
class TopicMetadata:
    id: str
    label: str
    cadence: str  # "weekly" / "daily_after_close" / ...
    active: bool


@dataclass(frozen=True)
class DigestSection:
    kind: DigestSectionKind
    body: str  # markdown


@dataclass(frozen=True)
class PendingMemoryDraft:
    target_type: str  # "topic" / "feedback" / "user" / "reading" / "reference"
    target_path: str  # 相对 memory/<uid>/ 的路径，例 "topics/nowcasting.theses.md"
    body: str  # 完整的 markdown including frontmatter
    rationale: str  # 为什么 agent 提议这条草稿


@dataclass(frozen=True)
class DigestResult:
    topic_id: str
    period_label: str  # "2026-W19" or "2026-05-06"
    generated_at: datetime
    sections: list[DigestSection]
    fingerprint: dict  # {"facts": [...], "memory": {"name": rev}, "trace_id": "..."}
    pending_drafts: list[PendingMemoryDraft] = field(default_factory=list)


class Collector(Protocol):
    """A Prefect flow that writes facts to DB, returns count of new rows."""

    def __call__(self, *args, **kwargs) -> int:
        ...


class Digester(Protocol):
    """A Prefect flow that reads facts + memory → DigestResult."""

    def __call__(self, period_label: str) -> DigestResult:
        ...
