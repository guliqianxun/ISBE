"""Which service settings a queued report depends on.

A task records a fingerprint of the settings at submit time and fails with
"settings changed" if it differs at execution, so a report never runs on a
model or source the submitter did not see. Hashing every setting except a
deny-list (the legacy fingerprint) caught mail settings too: rotating the SMTP
password failed every queued report, and two mail secrets went into the hash.

The fingerprint is an allow-list of the non-secret settings the pipeline
reads (collect, triage, enrich, digest and the model client). A setting added
to `settings.KEYS` later is ignored here until it is listed. Keys are read
late on purpose: a rotated API key is used, not refused.

Cut-over: rows queued by the Rust build hold the legacy fingerprint. `unchanged`
accepts either hash, so such a task runs when nothing the legacy fingerprint
covered has changed and fails explicitly otherwise; nothing waits forever.
Both inputs are compact JSON of a sorted map, so with no mail settings the
two hashes are equal.
"""

from collections.abc import Mapping

from isbe.core import db, jsonfmt

GENERATION: tuple[str, ...] = (
    # The model client (`Llm.from_env`); its key is read late, not fingerprinted.
    "ISBE_LLM_PROVIDER",
    "ISBE_LLM_NO_AUTH",
    "ISBE_LLM_BASE_URL",
    "ISBE_LLM_MODEL_FAST",
    "ISBE_LLM_MODEL_SMART",
    "ISBE_LLM_MAX_TOKENS",
    "ISBE_LLM_TIMEOUT_S",
    "ISBE_LLM_EXTRA_BODY",
    # Where collection gets papers and repositories.
    "ESSAIES_URL",
    "ARXIV_API_URL",
    "GITHUB_API_URL",
    # How enrichment extracts full text.
    "ISBE_FULLTEXT_EXTRACTOR",
    "METRAIL_API_URL",
)

# What the legacy fingerprint left out; everything else in KEYS went in.
_LEGACY_EXCLUDED = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DEEPSEEK_API_KEY",
        "GITHUB_TOKEN",
        "BAIDU_SEARCH_API_KEY",
        "SEARXNG_URL",
        "ISBE_SEARCH_DAILY_LIMIT",
        "ISBE_ARTICLE_DNS",
        "ISBE_EMAIL_DEFAULT_RECIPIENTS",
        # The question runs' paper source (#121); topic reports do not read it.
        "ISBE_PAPER_SOURCE",
        "OPENALEX_API_URL",
        "OPENALEX_API_KEY",
        "OPENALEX_MAILTO",
    }
)


def fingerprint(values: Mapping[str, str]) -> str:
    """The value stored in `tasks.settings_fingerprint` for a new task."""
    return db.sha256(jsonfmt.dumps({k: v for k, v in values.items() if k in GENERATION}))


def legacy_fingerprint(values: Mapping[str, str]) -> str:
    """The legacy fingerprint; computed only to compare, never stored."""
    return db.sha256(jsonfmt.dumps({k: v for k, v in values.items() if k not in _LEGACY_EXCLUDED}))


def unchanged(stored: str, values: Mapping[str, str]) -> bool:
    """Whether a task queued with `stored` may run on `values`.

    A legacy hash covers every generation setting, so matching it implies the
    allow-listed values are unchanged as well; a match on either is sound.
    """
    return stored in (fingerprint(values), legacy_fingerprint(values))
