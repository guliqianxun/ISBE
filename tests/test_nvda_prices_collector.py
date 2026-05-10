"""Unit tests for NVDA prices collector — yfinance is mocked."""
from datetime import date

import pandas as pd

from isbe.topics.nvda.collectors.prices import dataframe_to_rows


def _fake_df():
    return pd.DataFrame(
        {
            "Open": [100.0, 102.0],
            "High": [105.0, 106.0],
            "Low": [99.0, 101.0],
            "Close": [104.0, 105.5],
            "Volume": [1_000_000, 1_200_000],
            "Adj Close": [104.0, 105.5],
        },
        index=pd.to_datetime(["2026-05-08", "2026-05-09"]),
    )


def test_dataframe_to_rows_yields_one_row_per_date():
    rows = dataframe_to_rows(_fake_df(), symbol="NVDA")
    assert len(rows) == 2
    assert rows[0].symbol == "NVDA"
    assert rows[0].trade_date == date(2026, 5, 8)
    assert rows[0].close == 104.0
    assert rows[1].volume == 1_200_000


def test_dataframe_to_rows_handles_empty_df():
    rows = dataframe_to_rows(pd.DataFrame(), symbol="NVDA")
    assert rows == []
