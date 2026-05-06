from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class MemoryType(str, Enum):
    user = "user"
    feedback = "feedback"
    topic = "topic"
    reading = "reading"
    reference = "reference"


class MemorySource(str, Enum):
    user_edited = "user-edited"
    agent_inferred = "agent-inferred"
    agent_summarized = "agent-summarized"


class MemoryFrontmatter(BaseModel):
    name: str
    description: str = Field(max_length=150)
    type: MemoryType
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date
    source: MemorySource
    revision: int = 1
    supersedes: list[str] = Field(default_factory=list)
