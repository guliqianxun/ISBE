"""NVDA financial-domain facts: prices, news, SEC filings."""
from datetime import date, datetime

from sqlalchemy import ARRAY, BigInteger, Date, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from isbe.facts.db import Base


class PriceDaily(Base):
    """Daily OHLCV for NVDA + peers/customers/benchmarks. Composite PK."""

    __tablename__ = "prices_daily"

    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int] = mapped_column(BigInteger)
    adj_close: Mapped[float] = mapped_column(Float)


class NewsItem(Base):
    """News article — id is sha1(source + url) so we can dedupe across collectors."""

    __tablename__ = "news_items"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    headline: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    tickers: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    lang: Mapped[str] = mapped_column(String(8), default="en")


class SecFiling(Base):
    """SEC EDGAR filing — accession_no is the canonical EDGAR identifier."""

    __tablename__ = "sec_filings"

    accession_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    form_type: Mapped[str] = mapped_column(String(16), index=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    body_url: Mapped[str] = mapped_column(String(1024))
