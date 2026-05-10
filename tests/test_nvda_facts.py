"""Smoke checks for NVDA facts ORM definitions."""
from isbe.topics.nvda.facts import NewsItem, PriceDaily, SecFiling


def test_price_daily_columns():
    cols = {c.name for c in PriceDaily.__table__.columns}
    assert cols == {
        "symbol", "trade_date", "open", "high", "low",
        "close", "volume", "adj_close",
    }
    pk = {c.name for c in PriceDaily.__table__.primary_key.columns}
    assert pk == {"symbol", "trade_date"}


def test_news_item_columns():
    cols = {c.name for c in NewsItem.__table__.columns}
    assert {"id", "source", "published_at", "headline",
            "url", "body", "tickers", "lang"} <= cols


def test_sec_filing_columns():
    cols = {c.name for c in SecFiling.__table__.columns}
    assert {"accession_no", "ticker", "form_type",
            "filed_at", "body_url"} <= cols
