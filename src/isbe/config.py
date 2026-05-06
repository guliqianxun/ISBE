import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel


class TopicSource(BaseModel):
    type: str
    url: str | None = None
    category: str | None = None


class Topic(BaseModel):
    id: str
    label: str
    sources: list[TopicSource]


@dataclass(frozen=True)
class Config:
    uid: str
    topics: list[Topic]


def load_config(topics_path: Path) -> Config:
    uid = os.getenv("ISBE_UID", "me")
    raw = yaml.safe_load(topics_path.read_text(encoding="utf-8")) or {}
    topics = [Topic.model_validate(t) for t in raw.get("topics", [])]
    return Config(uid=uid, topics=topics)
