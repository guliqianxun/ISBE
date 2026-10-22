"""topic.yaml schema. Unknown keys are rejected and crons/timezones are checked
at load time, so a typo fails `isbe topics` instead of a scheduled run.
"""

import importlib.resources
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from functools import cache
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from isbe.core import settings

from . import cron, decode
from .decode import U32, U64
from .yamlio import YamlError

type Env = Callable[[str], str | None]


class TopicError(ValueError):
    """A topic file or document that cannot be used; the message is safe to show."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lang(StrEnum):
    """Language of the report and of the prompt set (`prompts/<lang>/`)."""

    EN = "en"
    ZH = "zh"


class Cadence(StrEnum):
    WEEKLY = "weekly"
    DAILY = "daily"


class Flavor(StrEnum):
    """Which digest a topic gets. Derived from its sources, not configured."""

    PAPERS = "papers"
    ARTICLES = "articles"


class Schedules(_Strict):
    """`digest` runs triage → enrich → digest → notify in order."""

    collect: str | None = None
    digest: str | None = None
    monthly: str | None = None


class Arxiv(_Strict):
    """Papers come from essaies when `ESSAIES_URL` is set, else from the arXiv API."""

    # A paper listed in any of these (cross-lists count)...
    categories: list[str] = []
    # ...that mentions any of these phrases in its title or abstract.
    include_keywords: list[str] = []
    # Newest N per run from the arXiv API; essaies returns the whole window.
    max_results: Annotated[int, U32] = 50


class Github(_Strict):
    repos: list[str]  # `owner/name`


class Feed(_Strict):
    name: str
    url: str  # may contain `${VAR}`, `${VAR:-default}` or `${VAR:?message}`


class Rss(_Strict):
    feeds: list[Feed] = []
    exclude_keywords: list[str] = []
    exclude_url_patterns: list[str] = []
    lookback_days: Annotated[int, U32] = 14


class Zhihu(_Strict):
    """知乎数据开放平台 search. Auth: `ZHIHU_ACCESS_KEY`."""

    queries: list[str] = []
    count: Annotated[int, U32] = 10
    lookback_days: Annotated[int, U32] = 3


class Metrail(_Strict):
    """LAN PDF-to-text service. Base URL falls back to `METRAIL_API_URL`."""

    enabled: bool = True
    base_url: str | None = None
    poll_timeout_s: Annotated[int, U64] = 120
    backend: str = "pdfplumber"
    ocr: bool = False
    # Backend for tables only (`docling` reads unruled result tables); unset,
    # the server's default applies.
    table_backend: str | None = None
    # Backend for figures only (`docling` crops whole figures, vector parts
    # included); unset, the server's default applies.
    figure_backend: str | None = None


class Digest(_Strict):
    """What enters the digest prompt."""

    # Kept papers reviewed one by one (core first, then newest); the rest are
    # listed by title only.
    max_papers: Annotated[int, U64] = 25
    fulltext_per_paper_chars: Annotated[int, U64] = 3000
    fulltext_total_chars: Annotated[int, U64] = 24000


class KeyEntities(_Strict):
    brands: list[str] = []
    segments: list[str] = []


class Retrieval(_Strict):
    """The topic's information need, shared by collection, triage and evaluation.
    Prose fields feed the LLM judge; keyword fields feed the rule gates."""

    intent: str = ""
    version: Annotated[int, U32] = 0
    in_scope: list[str] = []
    out_of_scope: list[str] = []
    quality_bar: str = ""
    facets: list[str] = []
    # Kept items matching these go to the secondary tier.
    secondary_terms: list[str] = []
    queries: list[str] = []
    # A title hit drops the item before any LLM call.
    out_of_scope_keywords: list[str] = []
    entity_terms: list[str] = []
    # When non-empty, an item must mention at least one of these to survive.
    require_any: list[str] = []
    key_entities: KeyEntities = Field(default_factory=KeyEntities)
    must_not_miss: list[str] = []

    def contract_json(self) -> str:
        """The block as compact UTF-8 JSON in field order, as it has always been
        written: it keys the triage cache, so any edit forces a re-judge."""
        return json.dumps(self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


class Topic(_Strict):
    id: str
    label: str
    lang: Lang = Lang.EN
    cadence: Cadence
    active: bool = True
    # IANA zone every cron in `schedules` is read in.
    schedule_timezone: str = "UTC"
    schedules: Schedules = Field(default_factory=Schedules)
    # How far back the digest looks, in days.
    window_days: Annotated[int, U32] = 7
    arxiv: Arxiv | None = None
    github: Github | None = None
    rss: Rss | None = None
    zhihu: Zhihu | None = None
    metrail: Metrail | None = None
    digest: Digest = Field(default_factory=Digest)
    retrieval: Retrieval | None = None

    def flavor(self) -> Flavor:
        return Flavor.PAPERS if self.arxiv is not None else Flavor.ARTICLES

    def since(self, now: datetime) -> datetime:
        """Start of the window: midnight UTC `window_days` ago. Whole days,
        because papers carry dates, not times. `now` must be aware."""
        if now.tzinfo is None:
            raise ValueError("since() needs an aware datetime")
        day = (now.astimezone(UTC) - timedelta(days=self.window_days)).date()
        return datetime.combine(day, time.min, UTC)


def debug(value: str) -> str:
    """A string quoted and escaped for validation messages."""
    out = ['"']
    for c in value:
        if c in '"\\':
            out.append("\\" + c)
        elif c in "\n\r\t\0":
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}[c])
        elif not c.isprintable() and c != " ":
            out.append(f"\\u{{{ord(c):x}}}")
        else:
            out.append(c)
    return "".join(out) + '"'


def check_id(id: str) -> None:
    """Topic IDs are portable file names, including on Windows."""
    reserved = id in ("con", "prn", "aux", "nul") or (
        len(id) == 4 and id.startswith(("com", "lpt")) and "1" <= id[3] <= "9"
    )
    if (
        not id
        or len(id.encode()) > 64
        or not ("a" <= id[0] <= "z" or "0" <= id[0] <= "9")
        or not all("a" <= c <= "z" or "0" <= c <= "9" or c in "-_" for c in id)
        or reserved
    ):
        raise TopicError(
            "id must be 1–64 lowercase letters, digits, '-' or '_', start with a letter or digit, "
            "and not be a Windows device name"
        )


@cache
def _zones() -> frozenset[str]:
    # tzdata's list is IANA's, backward links included, as topic files have
    # always been checked against; `Factory` is a placeholder and is omitted.
    names = importlib.resources.files("tzdata").joinpath("zones").read_text(encoding="utf-8").split()
    return frozenset(names) - {"Factory"}


def _check(topic: Topic, stem: str, env: Env) -> None:
    check_id(topic.id)
    if topic.id != stem:
        raise TopicError("id does not match file name")
    if not topic.label.strip():
        raise TopicError("label must not be empty")
    if not 1 <= topic.window_days <= 3650:
        raise TopicError("window_days must be between 1 and 3650")
    if not 1 <= topic.digest.max_papers <= 200:
        raise TopicError("digest.max_papers must be between 1 and 200")
    if topic.rss is not None and not 1 <= topic.rss.lookback_days <= 3650:
        raise TopicError("rss.lookback_days must be between 1 and 3650")
    if topic.metrail is not None and not 1 <= topic.metrail.poll_timeout_s <= 86400:
        raise TopicError("metrail.poll_timeout_s must be between 1 and 86400")
    if topic.arxiv is None and topic.rss is None and topic.zhihu is None:
        raise TopicError("no source: add an arxiv, rss or zhihu block")
    if (a := topic.arxiv) is not None:
        if not 1 <= a.max_results <= 2000:
            raise TopicError("arxiv.max_results must be between 1 and 2000")
        if not a.categories:
            raise TopicError("arxiv.categories is empty")
        if all(not k.strip() for k in a.include_keywords):
            raise TopicError("arxiv.include_keywords is empty: list the phrases a paper must mention")
    if topic.retrieval is not None and not topic.retrieval.intent.strip():
        raise TopicError("retrieval.intent is empty")
    for repo in topic.github.repos if topic.github else []:
        if len([p for p in repo.split("/") if p]) != 2:
            raise TopicError(f"github repo {debug(repo)} must be owner/name")
    if topic.schedule_timezone not in _zones():
        raise TopicError(f"unknown IANA timezone {debug(topic.schedule_timezone)}")
    for key in ("collect", "digest", "monthly"):
        if (pattern := getattr(topic.schedules, key)) is not None:
            try:
                cron.check(pattern)
            except cron.CronError as e:
                raise TopicError(f"schedules.{key}: bad cron {debug(pattern)}: {e}") from None
    for feed in topic.rss.feeds if topic.rss else []:
        try:
            feed.url = _expand_env(feed.url, env)
        except ValueError as e:
            raise TopicError(f"rss feed {debug(feed.name)}: {e}") from None


def _expand_env(s: str, env: Env) -> str:
    """Shell/compose-style `${VAR}`, `${VAR:-default}`, `${VAR:?message}`.
    Unset or empty counts as missing; errors never include expanded values."""
    out: list[str] = []
    rest = s
    while (start := rest.find("${")) != -1:
        out.append(rest[:start])
        length = rest[start:].find("}")
        if length == -1:
            raise ValueError(f"unclosed ${{ in {debug(s)}")
        expr = rest[start + 2 : start + length]
        i = expr.find(":")
        if i != -1 and expr[i : i + 2] in (":-", ":?"):
            name, op, arg = expr[:i], expr[i : i + 2], expr[i + 2 :]
        else:
            name, op, arg = expr, "", ""
        value = env(name) or None
        if value is not None:
            out.append(value)
        elif op == ":-":
            out.append(arg)
        elif op == ":?":
            raise ValueError(f"requires env var {name}: {arg}")
        else:
            raise ValueError(f"undefined env var {name}")
        rest = rest[start + length + 1 :]
    out.append(rest)
    return "".join(out)


def parse_with_env(text: str, stem: str, env: Env) -> Topic:
    """Validate a document whose file stem is `stem`. Environment expansion
    affects only the returned runtime copy; JSON text is accepted too."""
    try:
        topic = decode.decode(text, Topic)
    except YamlError as e:
        raise TopicError(str(e)) from None
    _check(topic, stem, env)
    return topic


def parse(text: str, stem: str) -> Topic:
    """`parse_with_env` with `${VAR}` read from the service settings."""
    return parse_with_env(text, stem, settings.get)


def load(path: Path) -> Topic:
    """Parse one topic file; its id must equal the file stem."""
    try:
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise TopicError(f"read {path}: stream did not contain valid UTF-8") from None
    except OSError as e:
        raise TopicError(f"read {path}: {e.strerror or e}") from None
    try:
        return parse(text, path.stem)
    except TopicError as e:
        raise TopicError(f"{path}: {e}") from None


def _paths(dir: Path) -> list[Path]:
    try:
        names = os.listdir(dir)
    except OSError as e:
        raise TopicError(f"read topics dir {dir}: {e.strerror or e}") from None
    return sorted(dir / n for n in names if Path(n).suffix in (".yaml", ".yml"))


def scan_dir(dir: Path) -> tuple[list[Topic], list[str]]:
    """Every loadable `*.yaml`/`*.yml` in `dir`, sorted by file name, plus one
    error per file that is not: pages and "all active
    topics" runs skip a broken file and report it. Raises `TopicError` only
    when the directory itself cannot be read."""
    topics: list[Topic] = []
    errors: list[str] = []
    for path in _paths(dir):
        try:
            topics.append(load(path))
        except TopicError as e:
            errors.append(str(e))
    return topics, errors


def load_dir(dir: Path) -> list[Topic]:
    """Like `scan_dir`, but any bad file is an error that lists every bad file."""
    topics, errors = scan_dir(dir)
    if errors:
        count = len(topics) + len(errors)
        raise TopicError(f"{len(errors)} of {count} topic files are invalid:\n\n" + "\n\n".join(errors))
    return topics
