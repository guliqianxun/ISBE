"""Typed schema for topic.yaml — single source of truth for what a topic may declare.

Loaded at registry time so a malformed yaml fails at startup with a field-pointed
error instead of three days later in a Prefect run.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from isbe.triage.contract import RetrievalContract

# ---------------------------------------------------------------------------
# Closed-set vocabularies — extend deliberately, not by typo
# ---------------------------------------------------------------------------

Cadence = Literal["weekly", "daily", "daily_after_close"]
DigestFlavor = Literal["weekly", "finance_daily"]
FeedLang = Literal["en", "zh"]
SecForm = Literal["8-K", "10-Q", "10-K", "S-1", "S-3", "424B", "DEF 14A"]


class _Strict(BaseModel):
    """Reject unknown keys so typos surface at load time."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Source blocks
# ---------------------------------------------------------------------------


class ArxivConfig(_Strict):
    categories: list[str]
    include_keywords: list[str] = Field(default_factory=list)
    max_results: int = 50


class RssFeed(_Strict):
    name: str
    url: str
    # Optional per-feed override; some adapters (e.g. NVDA's news_rss) ship
    # without a name field, so we tolerate that variant via NvdaNewsFeed below.


class RssConfig(_Strict):
    feeds: list[RssFeed]
    exclude_keywords: list[str] = Field(default_factory=list)
    exclude_url_patterns: list[str] = Field(default_factory=list)
    lookback_days: int = 14
    lang: FeedLang = "en"


class Crawl4AIPage(_Strict):
    name: str
    url: str


class Crawl4AIConfig(_Strict):
    pages: list[Crawl4AIPage]
    lookback_days: int = 14
    lang: FeedLang = "en"


class PricesConfig(_Strict):
    symbols: list[str]


class NvdaNewsFeed(_Strict):
    source: str
    url: str


class NewsRssConfig(_Strict):
    feeds: list[NvdaNewsFeed]


class SecCompany(_Strict):
    ticker: str
    cik: int


class SecEdgarConfig(_Strict):
    companies: list[SecCompany]
    forms: list[SecForm]


# ---------------------------------------------------------------------------
# Digest block (varies per flavor; keep permissive for now and tighten in PR2)
# ---------------------------------------------------------------------------


class DigestConfig(_Strict):
    flavor: DigestFlavor = "weekly"
    facts_window_days: int = 7
    include_repos: bool = False
    # NVDA-specific windows
    news_window_days: int | None = None
    filings_window_days: int | None = None
    # Prompt budget for the weekly paper digester. Extraction parameters live
    # in the metrail: block; these answer "how much of it enters the prompt".
    include_abstract: bool = True
    fulltext_per_paper_chars: int = 3000
    fulltext_total_chars: int = 24000
    # "cards" switches fact fields to the grounded paper-cards pipeline
    # (regex + anchored extraction over the full corpus); "legacy" keeps the
    # single-pass writer as the source of fact fields.
    pipeline: Literal["legacy", "cards"] = "legacy"


class MetrailConfig(_Strict):
    """LAN PDF-extraction service (metrail-web). Global default via METRAIL_API_URL."""

    enabled: bool = True
    base_url: str | None = None
    poll_timeout_s: int = 120
    backend: str = "pdfplumber"
    ocr: bool = False


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------


class TopicConfig(_Strict):
    id: str
    label: str
    cadence: Cadence
    active: bool = True

    # Source blocks — all optional, at least one required at runtime by the
    # CLI/scheduler, not by schema (a topic with only schedules is technically
    # valid yaml even if it does nothing useful).
    arxiv: ArxivConfig | None = None
    rss: RssConfig | None = None
    crawl4ai: Crawl4AIConfig | None = None
    prices: PricesConfig | None = None
    news_rss: NewsRssConfig | None = None
    sec_edgar: SecEdgarConfig | None = None

    digest: DigestConfig = Field(default_factory=DigestConfig)
    metrail: MetrailConfig | None = None
    schedules: dict[str, str] = Field(default_factory=dict)

    # 检索契约（triage 相关性门 + 优先级分层）。缺省 None → triage 直通（行为不变）。
    retrieval: RetrievalContract | None = None

    @classmethod
    def from_yaml_file(cls, path: Path) -> "TopicConfig":
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.model_validate(raw)
