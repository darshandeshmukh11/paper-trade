"""NIFTY 50 / NIFTY 100 constituent symbols (NSE tickers, no .NS suffix)."""

from __future__ import annotations

import pandas as pd

from nse_symbols import normalize_nse_symbol

NIFTY_50_FALLBACK: list[str] = [
    "ADANIENT",
    "ADANIPORTS",
    "APOLLOHOSP",
    "ASIANPAINT",
    "AXISBANK",
    "BAJAJ-AUTO",
    "BAJFINANCE",
    "BAJAJFINSV",
    "BEL",
    "BHARTIARTL",
    "CIPLA",
    "COALINDIA",
    "DRREDDY",
    "EICHERMOT",
    "ETERNAL",
    "GRASIM",
    "HCLTECH",
    "HDFCBANK",
    "HDFCLIFE",
    "HINDALCO",
    "HINDUNILVR",
    "HINDZINC",
    "ICICIBANK",
    "INDIGO",
    "INFY",
    "ITC",
    "JIOFIN",
    "JSWSTEEL",
    "KOTAKBANK",
    "LT",
    "M&M",
    "MARUTI",
    "NESTLEIND",
    "NTPC",
    "ONGC",
    "POWERGRID",
    "RELIANCE",
    "SBILIFE",
    "SBIN",
    "SHRIRAMFIN",
    "SUNPHARMA",
    "TATACONSUM",
    "TATAMOTORS",
    "TATASTEEL",
    "TCS",
    "TECHM",
    "TITAN",
    "TRENT",
    "ULTRACEMCO",
    "WIPRO",
]

NIFTY_100_EXTRA_FALLBACK: list[str] = [
    "ABB",
    "ADANIGREEN",
    "ADANIPOWER",
    "AMBUJACEM",
    "DMART",
    "GAIL",
    "HAL",
    "HAVELLS",
    "ICICIPRULI",
    "INDUSTOWER",
    "IOC",
    "IRFC",
    "JINDALSTEL",
    "LICI",
    "LODHA",
    "NAUKRI",
    "PIDILITIND",
    "PNB",
    "SIEMENS",
    "VEDL",
]


def _symbols_from_wikipedia_title(title: str, min_count: int = 45) -> list[str]:
    tables = pd.read_html(f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}")
    for table in tables:
        cols = {str(c).lower(): c for c in table.columns}
        symbol_col = None
        for key in ("symbol", "ticker", "nse symbol"):
            if key in cols:
                symbol_col = cols[key]
                break
        if symbol_col is None:
            continue
        symbols = (
            table[symbol_col]
            .astype(str)
            .str.strip()
            .str.upper()
            .replace({"NAN": None, "": None})
            .dropna()
            .tolist()
        )
        symbols = [s for s in symbols if s.isalnum() or "-" in s]
        if len(symbols) >= min_count:
            return sorted({normalize_nse_symbol(s) for s in symbols})
    raise ValueError(f"Could not parse symbols from Wikipedia: {title}")


def get_nifty50_symbols(prefer_live: bool = True) -> list[str]:
    if prefer_live:
        try:
            return _symbols_from_wikipedia_title("NIFTY 50", min_count=45)
        except Exception:
            pass
    return sorted({normalize_nse_symbol(s) for s in NIFTY_50_FALLBACK})


def get_nifty100_symbols(prefer_live: bool = True) -> list[str]:
    if prefer_live:
        try:
            return _symbols_from_wikipedia_title("NIFTY 100", min_count=90)
        except Exception:
            pass
    return sorted(
        {normalize_nse_symbol(s) for s in NIFTY_50_FALLBACK}
        | {normalize_nse_symbol(s) for s in NIFTY_100_EXTRA_FALLBACK}
    )


def intersect_with_nse_universe(index_symbols: list[str], nse_universe: list[str]) -> list[str]:
    """Keep index members that exist in the NSE equity list."""
    allowed = set(nse_universe)
    return sorted(s for s in index_symbols if s in allowed)
