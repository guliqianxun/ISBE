"""China-tech weekly digester — thin shell over _shared.articles_digester.

All behavior lives in the factory; this module declares the four parameters
that distinguish china-tech from other article-based topics (motorcycle etc.).
"""

from __future__ import annotations

from pathlib import Path

from isbe.llm.china_tech_prompts import CHINA_TECH_SYSTEM_PROMPT, build_china_tech_prompt
from isbe.topics._shared.articles_digester import make_articles_digester

TOPIC_ID = "china-tech"
TEMPLATE_PATH = Path(__file__).parent / "templates" / "weekly.j2"

china_tech_digester = make_articles_digester(
    topic_id=TOPIC_ID,
    flow_name="china-tech-digester",
    system_prompt=CHINA_TECH_SYSTEM_PROMPT,
    prompt_builder=build_china_tech_prompt,
    second_bucket_kind="company_notes",
    template_path=TEMPLATE_PATH,
)

# Canonical entry point picked up by topics.dispatch.
digest = china_tech_digester
