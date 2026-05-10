"""Unit tests for NVDA SEC EDGAR filings collector."""
from isbe.topics.nvda.collectors.sec import parse_submissions_response

SAMPLE_SUBMISSIONS = {
    "filings": {
        "recent": {
            "accessionNumber": ["0001045810-26-000010", "0001045810-26-000009"],
            "form": ["8-K", "10-Q"],
            "filingDate": ["2026-05-06", "2026-04-29"],
            "primaryDocument": ["nvda-8k.htm", "nvda-10q.htm"],
        }
    }
}


def test_parse_submissions_yields_typed_rows():
    rows = parse_submissions_response(
        SAMPLE_SUBMISSIONS, ticker="NVDA", form_filter={"8-K", "10-Q", "10-K"}
    )
    assert len(rows) == 2
    forms = sorted(r.form_type for r in rows)
    assert forms == ["10-Q", "8-K"]
    assert all(r.ticker == "NVDA" for r in rows)
    assert rows[0].body_url.startswith("https://www.sec.gov/")


def test_parse_submissions_filters_unwanted_forms():
    data = {
        "filings": {
            "recent": {
                "accessionNumber": ["x-1", "x-2"],
                "form": ["4", "8-K"],
                "filingDate": ["2026-05-01", "2026-05-02"],
                "primaryDocument": ["a.htm", "b.htm"],
            }
        }
    }
    rows = parse_submissions_response(data, ticker="NVDA", form_filter={"8-K"})
    assert len(rows) == 1
    assert rows[0].form_type == "8-K"
