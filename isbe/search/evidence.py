"""Publication-date evidence on saved search materials."""

from typing import Any

from isbe.core.errors import HttpError

from .plans import SearchPlan, parse_date

_OBSERVED = ("published_at", "updated_at", "date_status", "date_evidence", "date_error", "read_status")


def _first(old: dict[str, Any], *keys: str) -> Any:
    # The first key present, even when its value is null.
    for key in keys:
        if key in old:
            return old[key]
    return None


def _day(value: Any) -> str | None:
    """`value.get(..10)`: the first ten bytes of a string, if they end on a character."""
    if not isinstance(value, str):
        return None
    head = value.encode()
    if len(head) < 10:
        return None
    try:
        return head[:10].decode()
    except UnicodeDecodeError:
        return None


def _status(old: dict[str, Any], plan: SearchPlan) -> str:
    day = _day(old.get("published_at"))
    if day is not None:
        try:
            published = parse_date(day)
        except HttpError:
            pass
        else:
            inside = parse_date(plan.start_date) <= published <= parse_date(plan.end_date)
            return "in_range" if inside else "out_of_range"
    return "conflict" if old.get("date_status") == "conflict" else "unknown"


# A missed or failed read cannot erase previously observed publication evidence.
def merge_dates(
    content: dict[str, Any],
    previous: Any,
    observed: dict[str, Any],
    plan: SearchPlan,
    run_id: str,
    now: str,
) -> bool:
    """Write `observed`'s date fields into `content`, keeping `previous` evidence
    when this read was skipped or failed; true if the evidence was retained."""
    checked = observed.get("read_status") != "not_checked"
    failed = observed.get("read_status") == "failed" or observed.get("date_error") is not None
    old = previous if isinstance(previous, dict) else None
    published = old.get("published_at") if old is not None else None
    evidence = old.get("date_evidence") if old is not None else None
    retained = (
        (not checked or failed)
        and old is not None
        and ((isinstance(published, str) and published != "") or (isinstance(evidence, list) and evidence != []))
    )
    for key in _OBSERVED:
        content[key] = observed.get(key)
    content["date_result_retained"] = retained
    content["date_check_status"] = "not_checked" if not checked else "failed" if failed else "succeeded"
    content["date_checked_at"] = now if checked else None
    content["date_check_run_id"] = run_id
    content["start_date"] = plan.start_date
    content["end_date"] = plan.end_date
    if retained:
        assert old is not None
        for key in ("published_at", "updated_at", "date_evidence"):
            content[key] = old.get(key)
        content["date_evidence_run_id"] = _first(old, "date_evidence_run_id", "date_check_run_id", "run_id")
        content["date_evidence_checked_at"] = _first(old, "date_evidence_checked_at", "date_checked_at")
        content["date_status"] = _status(old, plan)
    else:
        content["date_evidence_run_id"] = run_id
        content["date_evidence_checked_at"] = now if checked else None
    return retained
