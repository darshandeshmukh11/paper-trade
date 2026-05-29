"""NSE equity symbol list (full market) with local cache."""

from __future__ import annotations

import io
import json
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "nse_equity_symbols.json"
CACHE_TTL_HOURS = 24

NSE_EQUITY_CSV = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"

# Yahoo tickers that differ from {SYMBOL}.NS
YAHOO_TICKER_ALIASES: dict[str, str] = {
    "TATAMOTORS": "TMPV.NS",
    "ZOMATO": "ETERNAL.NS",
}

NSE_SYMBOL_RENAMES: dict[str, str] = {
    "ZOMATO": "ETERNAL",
}

# Offline fallback when NSE archive is unreachable
FALLBACK_SYMBOLS: list[str] = sorted(
    {
        "RELIANCE",
        "TCS",
        "INFY",
        "HDFCBANK",
        "ICICIBANK",
        "SBIN",
        "BHARTIARTL",
        "ITC",
        "KOTAKBANK",
        "LT",
        "AXISBANK",
        "HINDUNILVR",
        "BAJFINANCE",
        "MARUTI",
        "SUNPHARMA",
        "TATASTEEL",
        "JINDALSTEL",
        "WIPRO",
        "HCLTECH",
        "NTPC",
        "ONGC",
        "POWERGRID",
        "ADANIENT",
        "ADANIPORTS",
        "M&M",
        "TITAN",
        "ULTRACEMCO",
    }
)


def normalize_nse_symbol(symbol: str) -> str:
    key = symbol.strip().upper()
    return NSE_SYMBOL_RENAMES.get(key, key)


def to_yahoo_ticker(symbol: str, exchange: str = "NSE") -> str:
    raw = normalize_nse_symbol(symbol)
    if raw in YAHOO_TICKER_ALIASES:
        return YAHOO_TICKER_ALIASES[raw]
    if exchange.upper() == "BSE":
        return f"{raw}.BO"
    if "." in raw:
        return raw
    return f"{raw}.NS"


def _fetch_from_nse_archive() -> list[str]:
    req = urllib.request.Request(
        NSE_EQUITY_CSV,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/csv,*/*",
        },
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read()
    df = pd.read_csv(io.BytesIO(raw))
    cols = {str(c).strip().upper(): c for c in df.columns}
    sym_col = cols.get("SYMBOL")
    if sym_col is None:
        sym_col = list(df.columns)[0]
    series_col = cols.get("SERIES")
    if series_col is not None:
        eq = df[series_col].astype(str).str.upper().str.strip() == "EQ"
        df = df.loc[eq]
    symbols = (
        df[sym_col]
        .astype(str)
        .str.strip()
        .str.upper()
        .replace({"NAN": None, "": None})
        .dropna()
        .tolist()
    )
    symbols = [normalize_nse_symbol(s) for s in symbols if s and (s.isalnum() or "-" in s)]
    if len(symbols) < 500:
        raise ValueError(f"NSE CSV parse too few symbols ({len(symbols)})")
    return sorted(set(symbols))


def _load_cache() -> Optional[list[str]]:
    if not CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(payload["fetched_at"])
        if datetime.now() - fetched > timedelta(hours=CACHE_TTL_HOURS):
            return None
        return payload["symbols"]
    except Exception:
        return None


def _save_cache(symbols: list[str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps(
            {"fetched_at": datetime.now().isoformat(), "count": len(symbols), "symbols": symbols},
            indent=0,
        ),
        encoding="utf-8",
    )


def get_nse_equity_symbols(force_refresh: bool = False) -> list[str]:
    """All NSE EQ symbols (cached). Falls back to a short list if download fails."""
    if not force_refresh:
        cached = _load_cache()
        if cached:
            return cached
    try:
        symbols = _fetch_from_nse_archive()
        _save_cache(symbols)
        return symbols
    except Exception:
        cached = None
        if CACHE_PATH.exists():
            try:
                cached = json.loads(CACHE_PATH.read_text(encoding="utf-8")).get("symbols")
            except Exception:
                cached = None
        if cached:
            return cached
        return list(FALLBACK_SYMBOLS)


# Shown when the search box is empty (not the first 100 symbols A–Z).
POPULAR_NSE_SYMBOLS: list[str] = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "BHARTIARTL",
    "ITC",
    "KOTAKBANK",
    "LT",
    "AXISBANK",
    "TATASTEEL",
    "TATAMOTORS",
    "JINDALSTEL",
    "WIPRO",
    "MARUTI",
    "SUNPHARMA",
    "HINDUNILVR",
    "BAJFINANCE",
    "ADANIENT",
    "M&M",
]


def search_nse_symbols(query: str, universe: Optional[list[str]] = None, limit: int = 150) -> list[str]:
    q = query.strip().upper()
    symbols = universe or get_nse_equity_symbols()
    universe_set = set(symbols)
    if not q:
        popular = [s for s in POPULAR_NSE_SYMBOLS if s in universe_set]
        return popular[:limit]

    exact = [s for s in symbols if s == q]
    prefix = [s for s in symbols if s.startswith(q) and s != q]
    contains = [s for s in symbols if q in s and not s.startswith(q)]
    out: list[str] = []
    for group in (exact, prefix, contains):
        for s in group:
            if s not in out:
                out.append(s)
            if len(out) >= limit:
                return out
    # Partial type-ahead: allow submitting typed symbol even if not in cache yet.
    if q.replace("-", "").isalnum() and q not in out:
        out.insert(0, q)
    return out[:limit]


def symbol_picker_options(query: str, universe: list[str]) -> list[str]:
    """Options for the symbol selectbox: full NSE list, or filtered matches."""
    q = query.strip().upper()
    if not q:
        popular = [s for s in POPULAR_NSE_SYMBOLS if s in set(universe)]
        if popular:
            return popular + [s for s in universe if s not in popular][: max(0, 80 - len(popular))]
        return universe
    return search_nse_symbols(q, universe, limit=300)
