"""Previous-close prices from Yahoo Finance's public chart endpoint (unofficial, delayed)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import requests

from ai_market_agent.errors import TransientError
from ai_market_agent.retry import retry_call

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
PAGE = "https://finance.yahoo.com/quote/{symbol}"
TIMEOUT = (5, 20)
USER_AGENT = "Mozilla/5.0 (compatible; market-intelligence-agents/1.0)"

INDICES: tuple[tuple[str, str], ...] = (
    ("^NSEI", "NIFTY 50"),
    ("^BSESN", "SENSEX"),
    ("^NSEBANK", "BANK NIFTY"),
)
SECTORS: tuple[tuple[str, str], ...] = (
    ("^CNXIT", "IT"),
    ("^CNXAUTO", "Auto"),
    ("^CNXPHARMA", "Pharma"),
    ("^CNXFMCG", "FMCG"),
    ("^CNXMETAL", "Metal"),
    ("^CNXENERGY", "Energy"),
    ("^CNXREALTY", "Realty"),
)


@dataclass(frozen=True)
class Quote:
    symbol: str
    name: str
    close: float | None = None
    prev_close: float | None = None
    session: date | None = None  # trading day of `close`, in IST
    error: str = ""  # set when the data could not be obtained

    @property
    def available(self) -> bool:
        return self.close is not None and self.prev_close is not None

    @property
    def change_pct(self) -> float | None:
        if self.close is None or not self.prev_close:
            return None
        return (self.close - self.prev_close) / self.prev_close * 100

    @property
    def source_url(self) -> str:
        return PAGE.format(symbol=self.symbol)


def parse_chart(payload: Any, symbol: str, name: str) -> Quote:
    """Take the last two valid daily closes. Anything malformed yields an 'unavailable' quote."""
    try:
        result = payload["chart"]["result"][0]
        stamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError):
        return Quote(symbol, name, error="no data returned")
    bars = [
        (ts, float(c))
        for ts, c in zip(stamps, closes, strict=False)
        if isinstance(ts, int) and isinstance(c, int | float) and c > 0
    ]
    if len(bars) < 2:
        return Quote(symbol, name, error="fewer than two closes available")
    (_, prev), (ts, last) = bars[-2], bars[-1]
    session = datetime.fromtimestamp(ts, tz=UTC).astimezone(IST).date()
    return Quote(symbol, name, close=last, prev_close=prev, session=session)


def fetch_quote(session: requests.Session, symbol: str, name: str) -> Quote:
    def attempt() -> Any:
        try:
            resp = session.get(
                URL.format(symbol=symbol),
                params={"range": "5d", "interval": "1d"},
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            raise TransientError(f"{symbol}: {type(exc).__name__}") from None
        if resp.status_code == 429:
            raise TransientError(f"{symbol}: rate limited", retry_after=5)
        if resp.status_code >= 500:
            raise TransientError(f"{symbol}: HTTP {resp.status_code}")
        if resp.status_code != 200:
            return None
        try:
            return resp.json()
        except ValueError:
            return None

    try:
        payload = retry_call(attempt, what=f"quote {symbol}", attempts=3, base_delay=1.5)
    except TransientError as exc:
        log.warning("Quote unavailable for %s: %s", symbol, exc)
        return Quote(symbol, name, error="provider error or rate limit")
    if payload is None:
        return Quote(symbol, name, error="provider rejected the request")
    return parse_chart(payload, symbol, name)


def fetch_all(
    watchlist: tuple[str, ...] = (), *, session: requests.Session | None = None
) -> tuple[list[Quote], list[Quote], list[Quote]]:
    """Return (indices, sectors, watchlist quotes). Individual failures never raise."""
    session = session or requests.Session()
    indices = [fetch_quote(session, s, n) for s, n in INDICES]
    sectors = [fetch_quote(session, s, n) for s, n in SECTORS]
    stocks = [fetch_quote(session, s, s.rsplit(".", 1)[0]) for s in watchlist]
    return indices, sectors, stocks
