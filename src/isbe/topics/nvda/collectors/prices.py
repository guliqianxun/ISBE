"""yfinance-based daily OHLCV collector for NVDA + watchlist symbols."""
from __future__ import annotations

from datetime import date

import pandas as pd
import yfinance as yf
from prefect import flow

from isbe.facts.db import make_session_factory
from isbe.observability.runs import topic_run
from isbe.topics.nvda.facts import PriceDaily
from isbe.topics.registry import default_topics_root, load_topic_config

TOPIC_ID = "nvda"


def dataframe_to_rows(df: pd.DataFrame, *, symbol: str) -> list[PriceDaily]:
    """Map a yfinance OHLCV DataFrame to PriceDaily rows."""
    if df.empty:
        return []
    out: list[PriceDaily] = []
    for ts, row in df.iterrows():
        out.append(
            PriceDaily(
                symbol=symbol,
                trade_date=ts.date() if hasattr(ts, "date") else date.fromisoformat(str(ts)[:10]),
                open=float(row["Open"]),
                high=float(row["High"]),
                low=float(row["Low"]),
                close=float(row["Close"]),
                volume=int(row["Volume"]),
                adj_close=float(row.get("Adj Close", row["Close"])),
            )
        )
    return out


def upsert_prices(session, rows: list[PriceDaily]) -> int:
    """Idempotent upsert; returns count of newly-inserted rows."""
    if not rows:
        return 0
    n = 0
    for r in rows:
        existing = session.get(PriceDaily, (r.symbol, r.trade_date))
        if existing is None:
            session.add(r)
            n += 1
        else:
            existing.open = r.open
            existing.high = r.high
            existing.low = r.low
            existing.close = r.close
            existing.volume = r.volume
            existing.adj_close = r.adj_close
    session.commit()
    return n


@flow(name="nvda-prices-collector")
def nvda_prices_collector(period: str = "5d") -> int:
    """Fetch last `period` of OHLCV for NVDA + each watchlist symbol from topic.yaml.

    Returns count of newly-inserted rows across all symbols.
    """
    cfg = load_topic_config(default_topics_root(), TOPIC_ID)
    prices_cfg = cfg.get("prices", {}) or {}
    symbols: list[str] = list(prices_cfg.get("symbols", ["NVDA"]))

    with topic_run(TOPIC_ID, "nvda-prices-collector") as run:
        Session = make_session_factory()
        total_new = 0
        with Session() as s:
            for sym in symbols:
                try:
                    df = yf.Ticker(sym).history(period=period, auto_adjust=False)
                except Exception as e:
                    print(f"[nvda-prices] skip {sym}: {e}")
                    continue
                rows = dataframe_to_rows(df, symbol=sym)
                total_new += upsert_prices(s, rows)
        run.payload["symbols"] = symbols
        run.payload["new_rows"] = total_new
        run.payload["period"] = period
        return total_new


if __name__ == "__main__":
    print(nvda_prices_collector())
