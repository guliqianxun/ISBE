"""The digest's inputs as read from the database."""

from dataclasses import dataclass
from typing import Any

from isbe.core import jsonfmt

# Card fields in report order: key, Chinese and English label.
FACTS: tuple[tuple[str, str, str], ...] = (
    ("source", "来源", "Source"),
    ("method", "方法", "Method"),
    ("data", "数据", "Data"),
    ("code", "代码", "Code"),
    ("compute", "算力", "Compute"),
    ("results", "效果", "Results"),
    ("limits", "局限", "Limitations"),
)


@dataclass(frozen=True)
class Fact:
    value: str
    quote: str


@dataclass(frozen=True)
class Card:
    """Facts from the full text whose quotes `enrich` found in it."""

    fields: dict[str, Fact]
    rejected: list[str]

    @classmethod
    def from_json(cls, text: str) -> Card:
        """`cards.card` as `enrich` writes it; malformed is an error."""
        v = jsonfmt.loads(text)
        if not isinstance(v, dict) or "fields" not in v or "rejected" not in v:
            raise ValueError("card: missing field `fields` or `rejected`")
        fields, rejected = v["fields"], v["rejected"]
        if not isinstance(fields, dict) or not isinstance(rejected, list):
            raise ValueError("card: `fields` must be a map and `rejected` a list")
        if not all(isinstance(r, str) for r in rejected):
            raise ValueError("card: `rejected` must hold strings")
        facts: dict[str, Fact] = {}
        for key, fact in fields.items():
            if not (
                isinstance(fact, dict)
                and isinstance(fact.get("value"), str)
                and isinstance(fact.get("quote"), str)
            ):
                raise ValueError(f"card: field {key} needs string `value` and `quote`")
            facts[key] = Fact(fact["value"], fact["quote"])
        return cls(facts, list(rejected))


@dataclass
class Paper:
    arxiv_id: str
    title: str
    summary: str
    url: str
    authors: list[str]
    categories: list[str]
    date: str  # YYYY-MM-DD
    secondary: bool = False
    # The normalized HTML or parsed PDF, from `enrich`.
    fulltext: str | None = None
    card: Card | None = None
    # The method figure `enrich` saved: (panel paths under the data dir, caption).
    figure: tuple[list[str], str] | None = None


@dataclass
class Repo:
    name: str  # `owner/name`
    url: str
    stars: int | None
    pushed: str | None  # YYYY-MM-DD
    active: bool


def get(value: Any, key: str) -> Any:
    """`value[key]` on parsed JSON: `None` unless `value` is an object holding `key`."""
    return value.get(key) if isinstance(value, dict) else None

