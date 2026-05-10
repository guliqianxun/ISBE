"""SEC EDGAR filings collector for NVDA (and any other CIK in topic.yaml)."""
from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx
from prefect import flow

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.nvda.facts import SecFiling
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nvda"
EDGAR_BASE = "https://www.sec.gov"
EDGAR_DATA_BASE = "https://data.sec.gov"


def _user_agent() -> str:
    """SEC requires identifying UA. Configurable via env; safe default for personal use."""
    return os.getenv(
        "SEC_USER_AGENT",
        "ISBE Research Agent (single-user; contact: see project README)",
    )


def _zero_pad_cik(cik: int | str) -> str:
    return str(int(cik)).zfill(10)


def _acc_to_cik_unknown(acc: str) -> str:
    """First component of accessionNumber is the filer CIK (zero-padded)."""
    return acc.split("-", 1)[0].lstrip("0") or "0"


def parse_submissions_response(
    data: dict, *, ticker: str, form_filter: set[str]
) -> list[SecFiling]:
    """Walk EDGAR submissions JSON → SecFiling rows for forms we care about."""
    recent = data.get("filings", {}).get("recent", {}) or {}
    accs = recent.get("accessionNumber", [])
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    docs = recent.get("primaryDocument", [])
    out: list[SecFiling] = []
    for acc, form, fdate, doc in zip(accs, forms, dates, docs, strict=False):
        if form not in form_filter:
            continue
        acc_clean = acc.replace("-", "")
        # canonical body URL: https://www.sec.gov/Archives/edgar/data/<cik>/<acc-no-dashes>/<doc>
        # The CIK is the first dash-separated component of the accession_no
        # (zero-padded). _acc_to_cik_unknown strips the leading zeros to match
        # the un-padded form EDGAR archive URLs use.
        body_url = f"{EDGAR_BASE}/Archives/edgar/data/{_acc_to_cik_unknown(acc)}/{acc_clean}/{doc}"
        out.append(
            SecFiling(
                accession_no=acc,
                ticker=ticker,
                form_type=form,
                filed_at=datetime.fromisoformat(fdate).replace(tzinfo=UTC),
                body_url=body_url,
            )
        )
    return out


def upsert_filings(session, filings: list[SecFiling]) -> int:
    n = 0
    for f in filings:
        if session.get(SecFiling, f.accession_no) is None:
            session.add(f)
            n += 1
    session.commit()
    return n


@flow(name="nvda-sec-collector")
def nvda_sec_collector() -> int:
    """For each (ticker, cik) pair in topic.yaml `sec_edgar.companies`, fetch and upsert."""
    cfg = load_topic_config(default_topics_root(), TOPIC_ID)
    sec_cfg = cfg.get("sec_edgar", {}) or {}
    companies: list[dict] = list(sec_cfg.get("companies", []))
    form_filter = set(sec_cfg.get("forms", ["8-K", "10-Q", "10-K"]))

    with topic_run(TOPIC_ID, "nvda-sec-collector") as run:
        Session = make_session_factory()
        total_new = 0
        with Session() as s:
            for company in companies:
                ticker = company["ticker"]
                cik = _zero_pad_cik(company["cik"])
                url = f"{EDGAR_DATA_BASE}/submissions/CIK{cik}.json"
                try:
                    resp = httpx.get(
                        url, timeout=30.0, headers={"User-Agent": _user_agent()}
                    )
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    print(f"[nvda-sec] skip {ticker}: {e}")
                    continue
                rows = parse_submissions_response(
                    resp.json(), ticker=ticker, form_filter=form_filter
                )
                total_new += upsert_filings(s, rows)
        run.payload["companies"] = [c["ticker"] for c in companies]
        run.payload["forms"] = sorted(form_filter)
        run.payload["new_filings"] = total_new
        return total_new


if __name__ == "__main__":
    print(nvda_sec_collector())
