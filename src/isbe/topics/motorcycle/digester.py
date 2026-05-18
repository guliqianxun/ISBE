"""Motorcycle weekly digester — thin shell over _shared.articles_digester.

All behavior lives in the factory; this module declares the four parameters
that distinguish motorcycle from other article-based topics (china-tech etc.).
"""

from __future__ import annotations

from pathlib import Path

from isbe.llm.motorcycle_prompts import MOTORCYCLE_SYSTEM_PROMPT, build_motorcycle_prompt
from isbe.topics._shared.articles_digester import make_articles_digester

TOPIC_ID = "motorcycle"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "weekly.j2"

motorcycle_digester = make_articles_digester(
    topic_id=TOPIC_ID,
    flow_name="motorcycle-digester",
    system_prompt=MOTORCYCLE_SYSTEM_PROMPT,
    prompt_builder=build_motorcycle_prompt,
    second_bucket_kind="brand_notes",
    template_path=TEMPLATE_PATH,
)

# Canonical entry point picked up by topics.dispatch.
digest = motorcycle_digester
