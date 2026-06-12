"""检索契约（= TREC topic）：领域信息需求的单一事实源。

采集取词、triage 打分、评估打靶三者共享这一份声明。
human 字段（intent / in_scope / out_of_scope / quality_bar）供阅读与 LLM-judge 阶段；
machine 字段（out_of_scope_keywords / entity_terms）供规则阶段直接消费。
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class KeyEntities(BaseModel):
    brands: list[str] = Field(default_factory=list)
    segments: list[str] = Field(default_factory=list)


class RetrievalContract(BaseModel):
    model_config = {"extra": "forbid"}  # 同 TopicConfig：挡 typo

    intent: str
    version: int = 1

    # human prose（LLM-judge / 阅读）
    in_scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    quality_bar: str = ""

    # RC1 子主题面（faceted；多标签，可重叠）。驱动分面 digest（M3）+ 分面覆盖核算。
    facets: list[str] = Field(default_factory=list)

    # machine signals（规则阶段直接用）
    out_of_scope_keywords: list[str] = Field(default_factory=list)
    entity_terms: list[str] = Field(default_factory=list)

    key_entities: KeyEntities = Field(default_factory=KeyEntities)
    must_not_miss: list[str] = Field(default_factory=list)


def load_contract(path: str | Path) -> RetrievalContract:
    """从 yaml 载入契约。允许 flat 或嵌在 `retrieval:` 键下（与 topic.yaml 落点一致）。"""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if "retrieval" in data:
        data = data["retrieval"]
    return RetrievalContract.model_validate(data)
