"""The stable codes of a question run's `error` events (`data.code`), so the
console can say what failed and what to do in the reader's language while the
message keeps the details (docs/standards/backend.md B-O4). The list and what
each means are in docs/question/api.md#problem-codes; a new code goes there and
into the event schema's enum.
"""

import re

from isbe.core import upstreams
from isbe.core.errors import HttpError
from isbe.core.llm import LlmError
from isbe.question import plan, sources

# essaies
ESSAIES_UNREACHABLE = "essaies_unreachable"  # no connection: refused, no route, DNS
ESSAIES_DOWN = "essaies_down"  # it answers 5xx or times out, request after request
ESSAIES_NOT_SET = "essaies_not_set"  # no ESSAIES_URL
OPENALEX_QUOTA = "openalex_quota"  # essaies' OpenAlex mirror is out of quota or rate-limited
QUERY_FAILED = "query_failed"  # one search query failed; the others went on
SEARCH_FAILED = "search_failed"  # every query failed, for reasons of their own
CITATIONS_FAILED = "citations_failed"  # citation lookups failed
ESSAIES_OLD = "essaies_old"  # essaies lacks a capability; upgrade it
# the OpenAlex API, when it is the paper source (#121)
OPENALEX_UNREACHABLE = "openalex_unreachable"  # no connection to the OpenAlex API
OPENALEX_DOWN = "openalex_down"  # it answers 5xx or times out, request after request
OPENALEX_REJECTED = "openalex_rejected"  # 401/403: OPENALEX_API_KEY is wrong
# the model
MODEL_UNREACHABLE = "model_unreachable"
MODEL_NOT_CONFIGURED = "model_not_configured"
MODEL_REJECTED = "model_rejected"  # 401/403: a wrong key, or a model the key may not use
MODEL_FAILED = "model_failed"  # calls failed (5xx, timeouts) or gave no usable reply
PLAN_FALLBACK = "plan_fallback"  # the plan is the question as asked: the run searched badly
# anything else
STAGE_FAILED = "stage_failed"

CODES = (
    ESSAIES_UNREACHABLE, ESSAIES_DOWN, ESSAIES_NOT_SET, OPENALEX_QUOTA, QUERY_FAILED, SEARCH_FAILED,
    CITATIONS_FAILED, ESSAIES_OLD, OPENALEX_UNREACHABLE, OPENALEX_DOWN, OPENALEX_REJECTED, MODEL_UNREACHABLE, MODEL_NOT_CONFIGURED, MODEL_REJECTED, MODEL_FAILED,
    PLAN_FALLBACK, STAGE_FAILED,
)


def source_code(error: sources.SourceError) -> str:
    """The code of a paper source's failure."""
    if error.status == 429 or "quota" in str(error).lower():
        return OPENALEX_QUOTA
    if error.upstream == upstreams.OPENALEX:
        if isinstance(error, sources.Unreachable) and error.connect:
            return OPENALEX_UNREACHABLE
        if error.status in (401, 403):
            return OPENALEX_REJECTED
        if isinstance(error, sources.Unreachable) or error.status is None or error.status >= 500:
            return OPENALEX_DOWN
        return QUERY_FAILED
    if isinstance(error, sources.Unreachable):
        return ESSAIES_UNREACHABLE if error.connect else ESSAIES_DOWN
    # 401/403/405: essaies, or a proxy before it, refuses the service, not one query.
    if error.status is None or error.status >= 500 or error.status in (401, 403, 405):
        return ESSAIES_DOWN
    return QUERY_FAILED


def advice(error: sources.SourceError) -> str:
    """What to check when the paper source failed, for a message."""
    if error.upstream == upstreams.OPENALEX:
        return ("check that this machine reaches the OpenAlex API (OPENALEX_API_URL, through HTTPS_PROXY if it "
                "needs one) and that OPENALEX_API_KEY, when set, is valid")
    return "check that essaies is running and that ESSAIES_URL points to it"


def code(error: BaseException) -> str:
    """The code of the exception that ended a stage."""
    if isinstance(error, sources.SourceError):
        return source_code(error)
    if isinstance(error, plan.ModelUnreachable):
        return MODEL_UNREACHABLE
    if isinstance(error, plan.ModelRejected):
        return MODEL_REJECTED
    if isinstance(error, LlmError):
        if upstreams.unreachable(error):
            return MODEL_UNREACHABLE
        return MODEL_REJECTED if upstreams.rejected(error) else MODEL_FAILED
    if isinstance(error, HttpError) and "ESSAIES_URL" in error.message:
        return ESSAIES_NOT_SET
    return STAGE_FAILED


# The call a model error names before its cause: "question_screen call to m: ". A model
# id has no spaces but may have colons (qwen3:8b, …-v1:0).
_CALL = re.compile(r"^question_\w+ (?:call to|reply from) \S+: ")


def model_detail(why: str) -> str:
    """The `detail` of a model problem: why the model failed or cannot be used,
    without the call it was or the setup advice after a ";"."""
    return _CALL.sub("", why.split(";")[0].strip()) or "no model is configured"


def detail(error: BaseException) -> str | None:
    """The upstream's own words for `error`, without the advice the message adds: what
    a console shows under its own translated advice (`data.detail`)."""
    if isinstance(error, sources.SourceError):
        return str(error)
    if isinstance(error, plan.ModelUnreachable | plan.ModelRejected):
        return error.detail
    if code(error) == ESSAIES_NOT_SET:
        return "ESSAIES_URL is not set"
    return None
