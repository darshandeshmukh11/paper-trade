"""Fetch latest traded price for NSE/BSE symbols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import yfinance as yf

from paper_trading.nse_symbols import to_yahoo_ticker


@dataclass
class LiveQuote:
    price: float
    source: str


def _pick_positive(*values: object) -> Optional[float]:
    for v in values:
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f > 0:
            return f
    return None


def fetch_live_quote(symbol: str, exchange: str = "NSE") -> Optional[LiveQuote]:
    """
  Latest price for Indian equities.

  Priority (most reliable for NSE live LTP):
    1. Yahoo fast_info.last_price / lastPrice
    2. Yahoo info regularMarketPrice / currentPrice
    3. Same-day 1m bars — last close only if above fail

  Avoid using stale intraday bars when Yahoo already has a newer last price.
    """
    yahoo_sym = to_yahoo_ticker(symbol, exchange)
    try:
        ticker = yf.Ticker(yahoo_sym)

        # 1) fast_info — closest to exchange LTP on Yahoo
        try:
            fi = ticker.fast_info
            last = _pick_positive(
                getattr(fi, "last_price", None),
                getattr(fi, "lastPrice", None),
                fi.get("last_price") if hasattr(fi, "get") else None,
            )
            if last is not None:
                return LiveQuote(last, f"Yahoo live ({yahoo_sym})")
        except Exception:
            pass

        # 2) Full quote metadata
        try:
            info = ticker.info or {}
            last = _pick_positive(
                info.get("regularMarketPrice"),
                info.get("currentPrice"),
                info.get("postMarketPrice"),
                info.get("preMarketPrice"),
                info.get("previousClose"),
            )
            if last is not None:
                key = "regularMarketPrice"
                if info.get("currentPrice"):
                    key = "currentPrice"
                return LiveQuote(last, f"Yahoo {key} ({yahoo_sym})")
        except Exception:
            pass

        # 3) Intraday fallback — last 1m close (can lag; use only if nothing else)
        hist = ticker.history(period="1d", interval="1m", prepost=False)
        if hist is not None and not hist.empty and "Close" in hist.columns:
            closes = hist["Close"].dropna()
            if not closes.empty:
                return LiveQuote(
                    float(closes.iloc[-1]),
                    f"Yahoo 1m bar ({yahoo_sym})",
                )
    except Exception:
        return None
    return None


def fetch_live_price(symbol: str, exchange: str = "NSE") -> Optional[float]:
    q = fetch_live_quote(symbol, exchange)
    return q.price if q else None
