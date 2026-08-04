"""Paper cards — per-paper grounded extraction (抽取员).

Two layers over the metrail fulltext corpus, replacing "feed the first N
chars into the weekly prompt" (which starved every field whose evidence lives
past the intro):

1. **regex layer** — hard facts grep'd deterministically from the FULL corpus
   (code URLs, GPU mentions, dataset names). Zero LLM, zero hallucination.
2. **LLM layer** — one fast-tier full-corpus call returning JSON; every field
   carries a verbatim anchor quote. Anchors are verified against the corpus
   LangExtract-style: exact substring → whitespace/case-normalized match →
   field rejected (value dropped, name recorded in `rejected_fields`).

Cards are cached as `<id>.metrail.card.json` next to the corpus, keyed by a
corpus hash — re-extraction happens only when the corpus changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from isbe.llm.client import complete

# ---------------------------------------------------------------------------
# regex layer
# ---------------------------------------------------------------------------

_CODE_RE = re.compile(
    r"https?://(?:github\.com|gitlab\.com|huggingface\.co|gitee\.com)/[\w.\-]+/[\w.\-]+",
    re.IGNORECASE,
)
_GPU_RE = re.compile(
    r"\d+\s*[×xX]\s*(?:NVIDIA\s+)?(?:[AVH]100|A6000|A800|H800|RTX\s?\d{4}|TPU[\w-]*)"
    r"|\b(?:[AVH]100|A6000|A800|H800|RTX\s?\d{4})\b",
)
# Domain lexicon (weather/CV benchmarks) — extend per topic via `datasets` arg.
DEFAULT_DATASET_LEXICON = (
    "SEVIR", "KNMI", "MeteoNet", "IMDAA", "ERA5", "HKO-7", "MRMS", "GOES",
    "Shanghai2020", "RYDL", "OPERA", "ImageNet", "COCO", "UCF101", "Kinetics",
    "Vimeo-90K", "DIV2K", "GoPro", "Rain100", "SPA-Data",
)


def grep_facts(corpus: str, datasets: tuple[str, ...] = DEFAULT_DATASET_LEXICON) -> dict:
    """Deterministic hard facts from the full corpus. These need no anchors —
    the match itself is the evidence."""
    code_urls = sorted({m.rstrip(".,);]") for m in _CODE_RE.findall(corpus)})
    gpu_mentions = sorted({re.sub(r"\s+", "", m) for m in _GPU_RE.findall(corpus) if m.strip()})
    found_datasets = sorted({d for d in datasets if re.search(re.escape(d), corpus, re.I)})
    return {
        "code_urls": code_urls,
        "gpu_mentions": gpu_mentions,
        "dataset_mentions": found_datasets,
    }


# ---------------------------------------------------------------------------
# anchor verification (LangExtract-style: exact → normalized → reject)
# ---------------------------------------------------------------------------


def _normalize(s: str) -> str:
    return re.sub(r"[\s ]+", "", s).lower()


def verify_anchor(anchor: str, corpus: str) -> bool:
    if not anchor or len(anchor) < 8:
        return False
    if anchor in corpus:
        return True
    return _normalize(anchor) in _normalize(corpus)


# ---------------------------------------------------------------------------
# card schema
# ---------------------------------------------------------------------------


class CardField(BaseModel):
    value: str
    anchor: str
    verified: bool = False


# LLM-extracted fields and the question each answers (drives the prompt too)
LLM_FIELDS: dict[str, str] = {
    "method": "核心方法一句话 + 它建立在什么已有工作/范式之上（谱系）",
    "data": "训练与评测数据集名 + 公开还是自采（可复现性视角）",
    "results": "主结果：指标@数据集 vs 基线 的具体数字（论文正文表格/文字中出现的）",
    "compute": "训练/推理算力（GPU 型号与数量），仅当正文明确给出",
    "reproducibility": "开源/权重/训练与评测脚本的实际情况（正文或脚注所述）",
    "limitations": "作者自述的局限或失败案例",
    "figure_reading": "框架图（若正文描述了总体架构图）在讲什么：输入→模块→输出一句话",
}


class PaperCard(BaseModel):
    arxiv_id: str
    corpus_sha256: str
    generated_at: str
    code_urls: list[str] = Field(default_factory=list)
    gpu_mentions: list[str] = Field(default_factory=list)
    dataset_mentions: list[str] = Field(default_factory=list)
    fields: dict[str, CardField] = Field(default_factory=dict)
    rejected_fields: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# LLM layer
# ---------------------------------------------------------------------------

_FIELD_LIST = "\n".join(f"- {k}: {v}" for k, v in LLM_FIELDS.items())

_CARD_SYSTEM = f"""你是论文事实抽取器。给你一篇论文的全文（metrail 提取的 markdown），
只输出一个 JSON 对象，不要任何其他文字。

JSON 形如：
{{"method": {{"value": "...", "anchor": "..."}}, "data": {{...}}, ...}}

字段清单（没有依据的字段直接省略，禁止编造）：
{_FIELD_LIST}

**anchor 铁律**：每个字段必须附 anchor —— 从全文**逐字复制**的一段原文（20–160 字符），
它必须能直接支撑 value。系统会用字符串匹配校验 anchor 是否真的在原文中；
校验失败该字段会被整个丢弃。所以：宁可省略字段，不要伪造引文。
value 用中文概括；anchor 保持原文语言逐字不动。"""


def _strip_json_envelope(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


_CORPUS_CHAR_CAP = 60_000  # ~20-25k tokens; whole corpus for all但极端超长论文


def extract_llm_fields(corpus: str, *, complete_fn=None, max_attempts: int = 2) -> dict:
    """One full-corpus call → {field: CardField}, anchors unverified yet."""
    if complete_fn is None:
        def complete_fn(system: str, user: str):
            return complete(system=system, user=user, tier="fast").text

    body = corpus[:_CORPUS_CHAR_CAP]
    last_err: Exception | None = None
    for _ in range(max_attempts):
        raw = complete_fn(_CARD_SYSTEM, body)
        try:
            data = json.loads(_strip_json_envelope(raw))
            if not isinstance(data, dict):
                raise ValueError("card JSON is not an object")
            out: dict[str, CardField] = {}
            for name in LLM_FIELDS:
                item = data.get(name)
                if isinstance(item, dict) and item.get("value") and item.get("anchor"):
                    out[name] = CardField(
                        value=str(item["value"])[:600], anchor=str(item["anchor"])[:300]
                    )
            return out
        except (ValueError, ValidationError) as e:
            last_err = e
    raise ValueError(f"card extraction JSON invalid after {max_attempts} attempts: {last_err}")


# ---------------------------------------------------------------------------
# assembly + cache
# ---------------------------------------------------------------------------


def build_card(
    arxiv_id: str,
    corpus: str,
    *,
    complete_fn=None,
    datasets: tuple[str, ...] = DEFAULT_DATASET_LEXICON,
) -> PaperCard:
    sha = hashlib.sha256(corpus.encode("utf-8")).hexdigest()
    card = PaperCard(
        arxiv_id=arxiv_id,
        corpus_sha256=sha,
        generated_at=datetime.now(UTC).isoformat(),
        **grep_facts(corpus, datasets),
    )
    fields = extract_llm_fields(corpus, complete_fn=complete_fn)
    for name, f in fields.items():
        if verify_anchor(f.anchor, corpus):
            f.verified = True
            card.fields[name] = f
        else:
            card.rejected_fields.append(name)
    return card


def card_path_for(corpus_path: Path) -> Path:
    return corpus_path.with_name(corpus_path.name.replace(".metrail.md", ".metrail.card.json"))


def load_or_build_card(
    arxiv_id: str,
    corpus_path: Path,
    *,
    complete_fn=None,
    datasets: tuple[str, ...] = DEFAULT_DATASET_LEXICON,
) -> PaperCard | None:
    """Cached card for a corpus file; None when the corpus is unreadable.
    The cache key is the corpus hash — a re-extracted corpus rebuilds the card."""
    try:
        corpus = corpus_path.read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    sha = hashlib.sha256(corpus.encode("utf-8")).hexdigest()
    cpath = card_path_for(corpus_path)
    try:
        cached = PaperCard.model_validate_json(cpath.read_text(encoding="utf-8"))
        if cached.corpus_sha256 == sha:
            return cached
    except (OSError, ValueError, ValidationError):
        pass
    card = build_card(arxiv_id, corpus, complete_fn=complete_fn, datasets=datasets)
    try:
        cpath.write_text(card.model_dump_json(), encoding="utf-8")
    except OSError:
        pass  # cache is best-effort
    return card
