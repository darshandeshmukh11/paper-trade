#!/usr/bin/env python3
"""Web paper trading for Indian equities (NSE/BSE) — Streamlit."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from charges import ChargeSettings, compute_charges, net_cash_flow
from nifty_indices import (
    get_nifty100_symbols,
    get_nifty50_symbols,
    intersect_with_nse_universe,
)
from nse_symbols import (
    get_nse_equity_symbols,
    normalize_nse_symbol,
    symbol_picker_options,
    to_yahoo_ticker,
)
from portfolio import (
    DEFAULT_LTCG_RATE,
    cash_balance,
    compute_positions,
    equity_turnover_summary,
    ltcg_tax_summary,
    performance_metrics,
    realized_pnl,
    trades_to_df,
)
from store import (
    connect,
    delete_all_trades,
    delete_trade,
    export_all,
    get_setting,
    import_backup,
    init_db,
    insert_trade,
    list_trades,
    set_setting,
    storage_label,
)

IST = ZoneInfo("Asia/Kolkata")

# Softer profit/loss on near-black background (less glare than pure green/red).
PNL_COLOR_PROFIT = "#4ade80"
PNL_COLOR_LOSS = "#f87171"
PNL_COLOR_NEUTRAL = "#71717a"
PNL_COLOR_LABEL = "#a1a1aa"
PNL_COLOR_VALUE = "#e4e4e7"


def _inject_eye_friendly_theme() -> None:
    """Dark background + system UI font stack for lower eye strain."""
    st.markdown(
        """
        <style>
        html, body, [class*="css"] {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
                "Helvetica Neue", Arial, sans-serif !important;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            letter-spacing: 0.01em;
            line-height: 1.55;
        }
        .stApp {
            background-color: #0a0a0a;
        }
        [data-testid="stAppViewContainer"] {
            background-color: #0a0a0a;
        }
        [data-testid="stSidebar"] {
            background-color: #111111;
            border-right: 1px solid #262626;
        }
        [data-testid="stSidebar"] .stMarkdown,
        [data-testid="stSidebar"] label {
            color: #a1a1aa !important;
        }
        h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
            color: #f4f4f5 !important;
            font-weight: 600 !important;
        }
        p, .stMarkdown, label, span, div {
            color: #d4d4d8;
        }
        .stCaption, [data-testid="stCaptionContainer"] {
            color: #a1a1aa !important;
        }
        [data-testid="stMetricLabel"] {
            color: #a1a1aa !important;
            font-size: 0.875rem !important;
        }
        [data-testid="stMetricValue"] {
            color: #e4e4e7 !important;
        }
        [data-testid="stDataFrame"] {
            border: 1px solid #262626;
            border-radius: 8px;
            overflow: hidden;
        }
        .stTextInput input, .stNumberInput input,
        .stDateInput input, textarea {
            background-color: #1a1a1a !important;
            color: #e4e4e7 !important;
            border-color: #3f3f46 !important;
        }
        [data-baseweb="select"] > div {
            background-color: #1a1a1a !important;
            color: #e4e4e7 !important;
        }
        .stButton > button[kind="primary"] {
            background-color: #3b82f6 !important;
            color: #fafafa !important;
        }
        .stButton > button[kind="secondary"] {
            background-color: #27272a !important;
            color: #e4e4e7 !important;
            border: 1px solid #3f3f46 !important;
        }
        [data-testid="stAlert"] {
            border-radius: 8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _check_password() -> bool:
    """Optional gate via st.secrets PAPER_PASSWORD or env PAPER_PASSWORD."""
    expected = ""
    try:
        expected = st.secrets.get("PAPER_PASSWORD", "") or ""
    except Exception:
        expected = os.environ.get("PAPER_PASSWORD", "") or ""
    if not expected:
        return True
    if st.session_state.get("paper_auth_ok"):
        return True
    st.title("Paper trading — sign in")
    pwd = st.text_input("Password", type="password")
    if st.button("Enter"):
        if pwd == expected:
            st.session_state["paper_auth_ok"] = True
            st.rerun()
        else:
            st.error("Wrong password")
    return False


def _load_charge_settings(conn) -> ChargeSettings:
    raw = get_setting(conn, "charge_settings", "{}")
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        d = {}
    return ChargeSettings(
        brokerage_per_order=float(d.get("brokerage_per_order", 20)),
        gst_on_brokerage=float(d.get("gst_on_brokerage", 0.18)),
        stt_delivery_sell=float(d.get("stt_delivery_sell", 0.001)),
        stt_intraday_sell=float(d.get("stt_intraday_sell", 0.00025)),
        exchange_txn_pct=float(d.get("exchange_txn_pct", 0.0000345)),
        sebi_pct=float(d.get("sebi_pct", 0.000001)),
        stamp_duty_buy=float(d.get("stamp_duty_buy", 0.00015)),
        dp_delivery_sell=float(d.get("dp_delivery_sell", 15.93)),
    )


def _save_charge_settings(conn, cs: ChargeSettings) -> None:
    set_setting(
        conn,
        "charge_settings",
        json.dumps(
            {
                "brokerage_per_order": cs.brokerage_per_order,
                "gst_on_brokerage": cs.gst_on_brokerage,
                "stt_delivery_sell": cs.stt_delivery_sell,
                "stt_intraday_sell": cs.stt_intraday_sell,
                "exchange_txn_pct": cs.exchange_txn_pct,
                "sebi_pct": cs.sebi_pct,
                "stamp_duty_buy": cs.stamp_duty_buy,
                "dp_delivery_sell": cs.dp_delivery_sell,
            }
        ),
    )


@st.cache_data(ttl=3600)
def _load_nse_symbol_universe(force_refresh: bool = False) -> list[str]:
    return get_nse_equity_symbols(force_refresh=force_refresh)


@st.cache_data(ttl=86400)
def _load_nifty50() -> list[str]:
    return get_nifty50_symbols(prefer_live=True)


@st.cache_data(ttl=86400)
def _load_nifty100() -> list[str]:
    return get_nifty100_symbols(prefer_live=True)


def _universe_for_index(index_filter: str, nse_universe: list[str]) -> list[str]:
    if index_filter == "Nifty 50":
        return intersect_with_nse_universe(_load_nifty50(), nse_universe)
    if index_filter == "Nifty 100":
        return intersect_with_nse_universe(_load_nifty100(), nse_universe)
    return nse_universe


@st.cache_data(ttl=90)
def fetch_ltp(symbol: str, exchange: str = "NSE") -> Optional[float]:
    from live_price import fetch_live_price

    return fetch_live_price(symbol, exchange)


@st.cache_data(ttl=90)
def fetch_ltp_quote(symbol: str, exchange: str = "NSE"):
    from live_price import fetch_live_quote

    return fetch_live_quote(symbol, exchange)


def _fmt_inr(x: float) -> str:
    return f"₹{x:,.2f}"


def _optional_order_price(value: float) -> float | None:
    """Treat 0 as unset for optional stop loss / target fields."""
    return float(value) if value and value > 0 else None


def _display_optional_price(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    return _fmt_inr(num) if num > 0 else "—"


def _color_return_pct_cell(val: object) -> str:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return f"color: {PNL_COLOR_NEUTRAL}"
    if num > 0:
        return f"color: {PNL_COLOR_PROFIT}; font-weight: 600"
    if num < 0:
        return f"color: {PNL_COLOR_LOSS}; font-weight: 600"
    return f"color: {PNL_COLOR_NEUTRAL}"


def _color_pnl_cell(val: object) -> str:
    try:
        num = float(val)
    except (TypeError, ValueError):
        return f"color: {PNL_COLOR_NEUTRAL}"
    if num > 0:
        return f"color: {PNL_COLOR_PROFIT}; font-weight: 600"
    if num < 0:
        return f"color: {PNL_COLOR_LOSS}; font-weight: 600"
    return f"color: {PNL_COLOR_NEUTRAL}"


def _style_open_positions_table(df: pd.DataFrame):
    """Green / red unrealized P&L in the open positions grid (dark-friendly)."""
    dark_table = [
        {
            "selector": "th",
            "props": [
                ("background-color", "#1a1a1a"),
                ("color", "#e4e4e7"),
                ("border-color", "#262626"),
            ],
        },
        {
            "selector": "td",
            "props": [
                ("background-color", "#0f0f0f"),
                ("color", "#d4d4d8"),
                ("border-color", "#262626"),
            ],
        },
    ]
    styler = df.style.set_table_styles(dark_table)
    if hasattr(styler, "map"):
        styler = styler.map(_color_pnl_cell, subset=["Unrealized P&L"])
    else:
        styler = styler.applymap(_color_pnl_cell, subset=["Unrealized P&L"])
    return styler.format(
        {
            "Avg cost": "₹{:,.2f}",
            "LTP": "₹{:,.2f}",
            "Market value": "₹{:,.2f}",
            "Unrealized P&L": "₹{:+,.2f}",
        }
    )


def _fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def _metric_pct(container, label: str, value: Optional[float], subtext: str = "") -> None:
    """Metric tile for percentage returns (XIRR, CAGR, etc.)."""
    if value is None:
        color = PNL_COLOR_NEUTRAL
        text = "—"
    elif value > 0:
        color = PNL_COLOR_PROFIT
        text = f"{value:+.2f}%"
    elif value < 0:
        color = PNL_COLOR_LOSS
        text = f"{value:+.2f}%"
    else:
        color = PNL_COLOR_NEUTRAL
        text = "0.00%"
    sub = (
        f'<p style="margin:0.15rem 0 0;font-size:0.8rem;color:{color};">{subtext}</p>'
        if subtext
        else ""
    )
    with container:
        st.markdown(
            f'<p style="margin:0;font-size:0.875rem;color:{PNL_COLOR_LABEL};">{label}</p>'
            f'<p style="margin:0;font-size:1.75rem;font-weight:600;color:{color};">{text}</p>'
            f"{sub}",
            unsafe_allow_html=True,
        )


def _portfolio_equity(
    starting: float, trades_df: pd.DataFrame, use_live_ltp: bool = True
) -> tuple[float, float, float]:
    """Return (cash, holdings_value, equity). Holdings use LTP when use_live_ltp."""
    cash = cash_balance(starting, trades_df)
    positions = compute_positions(trades_df)
    holdings_value = 0.0
    if use_live_ltp and positions:
        quotes = _fetch_quotes_parallel(positions)
        for p in positions:
            q = quotes.get(p.symbol)
            ltp = q.price if q is not None else p.avg_cost
            holdings_value += ltp * p.qty
    else:
        holdings_value = sum(p.cost_basis for p in positions)
    return cash, holdings_value, cash + holdings_value


def _build_performance_context(
    starting: float, trades_df: pd.DataFrame, use_live_ltp: bool = True
) -> dict:
    _, _, equity = _portfolio_equity(starting, trades_df, use_live_ltp=use_live_ltp)
    return performance_metrics(trades_df, starting, equity)


def _render_returns_section(
    perf: dict,
    *,
    show_cycles_table: bool = True,
    cycles_expanded: bool = False,
    use_styled_cycles: bool = False,
) -> None:
    """Portfolio + closed-trade return metrics (shared by Dashboard and History)."""
    st.subheader("Returns (holding period & cash flows)")
    st.caption(
        "**Absolute return** = total gain on starting capital (current equity vs starting). "
        "**XIRR** = annualized return from dated cash flows (capital, trades, current equity). "
        "**CAGR** = annualized portfolio growth over calendar days since first trade. "
        "Per-trade **days held**, **abs. return %**, and **CAGR %** use FIFO buy → sell dates."
    )
    r1, r2, r3, r4 = st.columns(4)
    _metric_pct(
        r1,
        "Absolute return",
        perf.get("abs_return_pct"),
        f"{perf.get('portfolio_days', 0)} days since first trade",
    )
    _metric_pct(r2, "XIRR (annualized)", perf.get("xirr_pct"), "Money-weighted")
    _metric_pct(r3, "CAGR (annualized)", perf.get("cagr_pct"), "Portfolio level")
    with r4:
        avg_hold = perf.get("avg_hold_days")
        hold_txt = f"{avg_hold:.0f} days" if avg_hold is not None else "—"
        st.metric("Avg hold (closed)", hold_txt, f"{perf.get('closed_trades', 0)} round-trips")

    r5, r6, r7 = st.columns(3)
    _metric_pct(
        r5,
        "Avg abs. return (closed)",
        perf.get("avg_closed_abs_return_pct"),
        "Per completed trade",
    )
    _metric_pct(
        r6,
        "Avg CAGR (closed)",
        perf.get("avg_closed_cagr_pct"),
        "Annualized per trade",
    )
    r7.metric("Portfolio days", str(perf.get("portfolio_days", 0)))

    if not show_cycles_table:
        return

    closed_df = perf.get("closed_df")
    if closed_df is None or closed_df.empty:
        st.info("No completed buy/sell cycles yet — returns above are portfolio-level only.")
        return

    def _show_cycles_table() -> None:
        if use_styled_cycles:
            st.dataframe(
                _style_completed_cycles_table(closed_df),
                use_container_width=True,
                hide_index=True,
            )
        else:
            show = closed_df[
                [
                    "symbol",
                    "buy_date",
                    "sell_date",
                    "hold_days",
                    "qty",
                    "buy_avg",
                    "sell_avg",
                    "pnl_inr",
                    "abs_return_pct",
                    "cagr_pct",
                ]
            ].copy()
            show.columns = [
                "Symbol",
                "Buy date",
                "Sell date",
                "Days held",
                "Qty",
                "Buy avg (₹)",
                "Sell avg (₹)",
                "P&L (₹)",
                "Abs. return %",
                "CAGR %",
            ]
            st.dataframe(show, use_container_width=True, hide_index=True)

    if cycles_expanded:
        st.markdown("#### Completed trades — return & holding period")
        _show_cycles_table()
    else:
        with st.expander("Completed trades — return & holding period", expanded=False):
            _show_cycles_table()


def _render_turnover_and_tax(
    trades_df: pd.DataFrame,
    closed_df: pd.DataFrame,
    total_pnl: float,
) -> None:
    """SEBI-style turnover and post-tax profit (15% LTCG on realized long-term gains)."""
    turnover = equity_turnover_summary(trades_df, closed_df)
    tax = ltcg_tax_summary(closed_df, total_pnl, ltcg_rate=DEFAULT_LTCG_RATE)

    st.markdown("#### Turnover & tax (India / SEBI)")
    st.caption(
        "**Turnover:** buy/sell = gross traded value per leg. "
        "**Total (audit):** delivery = sell value only; intraday = |P&L| per closed trade. "
        f"**Post-tax profit:** total P&L minus {DEFAULT_LTCG_RATE:.0%} LTCG on realized gains held > 12 months."
    )

    t1, t2, t3, t4 = st.columns(4)
    t1.metric("Buy turnover", _fmt_inr(turnover["buy_turnover"]))
    t2.metric("Sell turnover", _fmt_inr(turnover["sell_turnover"]))
    t3.metric(
        "Total turnover (audit)",
        _fmt_inr(turnover["total_turnover"]),
        help=(
            f"Delivery sell: {_fmt_inr(turnover['delivery_turnover'])} · "
            f"Intraday |P&L|: {_fmt_inr(turnover['intraday_turnover'])}"
        ),
    )
    t4.metric(
        "Delivery / intraday",
        _fmt_inr(turnover["delivery_turnover"]),
        f"Intraday |P&L|: {_fmt_inr(turnover['intraday_turnover'])}",
    )

    p1, p2, p3 = st.columns(3)
    _pnl_metric(
        p1,
        "Total profit (pre-tax)",
        total_pnl,
        "Cash + holdings at LTP − starting capital",
    )
    p2.metric(
        "LTCG tax (est. 15%)",
        _fmt_inr(tax["ltcg_tax"]),
        help=f"On realized LTCG gains: {_fmt_inr(tax['ltcg_taxable_gains'])}",
    )
    _pnl_metric(
        p3,
        "Total profit (post-tax LTCG)",
        tax["post_tax_total_pnl"],
        f"STCG gains (not taxed here): {_fmt_inr(tax['stcg_taxable_gains'])}",
    )


def _pnl_metric(container, label: str, value: float, subtext: str = "") -> None:
    """Metric with green (profit) or red (loss) value."""
    if value > 0:
        color = PNL_COLOR_PROFIT
    elif value < 0:
        color = PNL_COLOR_LOSS
    else:
        color = PNL_COLOR_NEUTRAL
    sub = (
        f'<p style="margin:0.15rem 0 0;font-size:0.8rem;color:{color};">{subtext}</p>'
        if subtext
        else ""
    )
    with container:
        st.markdown(
            f'<p style="margin:0;font-size:0.875rem;color:{PNL_COLOR_LABEL};">{label}</p>'
            f'<p style="margin:0;font-size:1.75rem;font-weight:600;color:{color};">{_fmt_inr(value)}</p>'
            f"{sub}",
            unsafe_allow_html=True,
        )


def _fetch_quotes_parallel(positions: list) -> dict[str, object]:
    """Fetch Yahoo quotes in parallel (much faster than one-by-one)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    out: dict[str, object] = {}
    if not positions:
        return out

    def _one(p) -> tuple[str, object]:
        return p.symbol, fetch_ltp_quote(p.symbol, p.exchange)

    workers = min(6, len(positions))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_one, p) for p in positions]
        for fut in as_completed(futures):
            sym, quote = fut.result()
            out[sym] = quote
    return out


def page_dashboard(conn, starting: float, trades_df: pd.DataFrame) -> None:
    st.caption(
        "Open-position P&L uses **live LTP** (Yahoo). Cached ~90s · **Refresh LTP / prices** in sidebar. "
        "**Total P&L** = cash + holdings at LTP − starting capital."
    )
    cash = cash_balance(starting, trades_df)
    positions = compute_positions(trades_df)
    mtm = 0.0
    holdings_value = 0.0
    live_quote_count = 0
    pos_rows = []
    quotes = _fetch_quotes_parallel(positions)
    for p in positions:
        quote = quotes.get(p.symbol)
        if quote is not None:
            ltp = quote.price
            live_quote_count += 1
            quote_note = quote.source
        else:
            ltp = p.avg_cost
            quote_note = "No quote — using avg cost"
        mv = ltp * p.qty
        upl = mv - p.cost_basis
        mtm += upl
        holdings_value += mv
        pos_rows.append(
            {
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Avg cost": round(p.avg_cost, 2),
                "LTP": round(ltp, 2),
                "Quote": quote_note,
                "Market value": round(mv, 2),
                "Unrealized P&L": round(upl, 2),
            }
        )

    realized = realized_pnl(trades_df) if not trades_df.empty else 0.0
    equity = cash + holdings_value

    total_pnl = equity - starting
    return_pct = (total_pnl / starting * 100) if starting else 0.0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Cash available", _fmt_inr(cash))
    c2.metric("Holdings value (at LTP)", _fmt_inr(holdings_value))
    c3.metric("Portfolio equity", _fmt_inr(equity))
    _pnl_metric(c4, "Total P&L (mark-to-market)", total_pnl, f"{return_pct:+.2f}%")

    c5, c6, c7 = st.columns(3)
    _pnl_metric(c5, "Unrealized P&L (open)", mtm)
    _pnl_metric(c6, "Realized P&L (closed)", realized)
    c7.metric("Open positions", str(len(positions)))

    perf = _build_performance_context(starting, trades_df, use_live_ltp=True)
    closed_df = perf.get("closed_df")
    if closed_df is None:
        closed_df = pd.DataFrame()
    _render_turnover_and_tax(trades_df, closed_df, total_pnl)

    _render_returns_section(perf, show_cycles_table=True, cycles_expanded=False)

    if positions and live_quote_count < len(positions):
        st.warning(
            f"Live LTP for {live_quote_count}/{len(positions)} positions — "
            "others use avg cost until Yahoo returns a price. Use **Refresh LTP** in the sidebar."
        )

    if pos_rows:
        st.subheader("Open positions")
        st.dataframe(
            _style_open_positions_table(pd.DataFrame(pos_rows)),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No open positions. Place a BUY from the **New trade** tab.")


def page_new_trade(conn, starting: float, trades_df: pd.DataFrame, cs: ChargeSettings) -> None:
    st.subheader("Place order")
    st.caption(
        "Pick **Nifty 50 / Nifty 100** or **All NSE**, then search (e.g. `TATAST` → TATASTEEL)."
    )

    r1, r2 = st.columns([4, 1])
    with r2:
        if st.button("↻ Refresh lists"):
            _load_nse_symbol_universe.clear()
            _load_nifty50.clear()
            _load_nifty100.clear()
            st.rerun()
    nse_universe = _load_nse_symbol_universe()
    index_filter = st.radio(
        "Stock universe",
        ["All NSE", "Nifty 50", "Nifty 100"],
        horizontal=True,
        help="Nifty lists from Wikipedia (cached 24h), cross-checked with NSE equity master.",
    )
    active_universe = _universe_for_index(index_filter, nse_universe)
    if index_filter != "All NSE":
        st.caption(f"**{index_filter}:** {len(active_universe)} symbols available for trading in this app.")

    with r1:
        search = st.text_input(
            "Symbol search",
            placeholder="TATASTEEL, TATAST, RELIANCE, JINDAL…",
            help="Filters the list below within the selected universe.",
        )
    picker_options = symbol_picker_options(search, active_universe)
    if search.strip() and not picker_options:
        st.error(
            f"No symbols match «{search.strip().upper()}» in **{index_filter}**. "
            "Try another universe or Refresh lists."
        )
    sym_col, ex_col = st.columns([3, 1])
    with sym_col:
        if search.strip():
            label = f"Matches for «{search.strip().upper()}» ({len(picker_options)})"
        elif index_filter == "All NSE":
            label = f"Symbol — popular + search ({len(active_universe):,} NSE available)"
        else:
            label = f"Symbol — {index_filter} ({len(active_universe)} stocks)"
        symbol = st.selectbox(
            label,
            options=picker_options if picker_options else ["RELIANCE"],
            index=0,
        )
        if not search.strip():
            st.caption("Type in **Symbol search** to find any NSE ticker (list is capped for speed).")
        if search.strip() and picker_options:
            st.caption(f"Top match: **{picker_options[0]}**")
    with ex_col:
        exchange = st.selectbox("Exchange", ["NSE", "BSE"], help="BSE: use BSE ticker; Yahoo suffix .BO")

    default_price = 1000.0
    with st.form("new_trade", clear_on_submit=True):
        c2, c3 = st.columns(2)
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
            segment = st.selectbox("Segment", ["Equity Delivery", "Equity Intraday"])
        with c3:
            qty = st.number_input("Quantity", min_value=1, value=10, step=1)
            price = st.number_input("Price (₹)", min_value=0.01, value=default_price, step=0.05)
        st.caption("Tip: open **Dashboard** and use **Refresh LTP** to see live prices (fetched on demand).")

        c4, c5 = st.columns(2)
        with c4:
            traded_at = st.date_input("Trade date", value=date.today())
        with c5:
            position_id = st.text_input("Position ID (optional)", placeholder="T001")

        c6, c7 = st.columns(2)
        with c6:
            stop_loss = st.number_input(
                "Stop loss (₹)",
                min_value=0.0,
                value=0.0,
                step=0.05,
                help="Optional. Leave 0 if not set.",
            )
        with c7:
            target_price = st.number_input(
                "Target price (₹)",
                min_value=0.0,
                value=0.0,
                step=0.05,
                help="Optional. Leave 0 if not set.",
            )

        notes = st.text_input("Notes", placeholder="Swing entry, support rejection, etc.")
        submitted = st.form_submit_button("Submit order", type="primary")

    if not submitted:
        preview = compute_charges(side, int(qty), float(price), segment, cs)
        net = net_cash_flow(side, preview)
        flow = "leaves your account" if net < 0 else "enters your account"
        st.info(
            f"**Order preview** (not saved yet) · "
            f"**Trade value:** {_fmt_inr(preview.gross)} · "
            f"**Est. charges:** {_fmt_inr(preview.total)} · "
            f"**Cash impact:** {_fmt_inr(net)} ({flow})"
        )
        return

    sym = normalize_nse_symbol(symbol)
    if exchange == "NSE" and sym not in nse_universe:
        st.warning(
            f"**{sym}** is not in the cached NSE list — order will still be saved. "
            "Check spelling or click **Refresh NSE list**."
        )
    positions = compute_positions(trades_df)
    held = next((p for p in positions if p.symbol == sym), None)

    if side == "SELL":
        if held is None or held.qty < int(qty):
            st.error(f"Insufficient holdings: you have {held.qty if held else 0} shares of {sym}")
            return

    charges = compute_charges(side, int(qty), float(price), segment, cs)
    net = net_cash_flow(side, charges)
    cash = cash_balance(starting, trades_df)
    if side == "BUY" and cash + net < -0.01:
        st.error(f"Insufficient cash: need {_fmt_inr(-net)}, available {_fmt_inr(cash)}")
        return

    trade = {
        "traded_at": datetime.combine(traded_at, datetime.min.time()).replace(tzinfo=IST).isoformat(),
        "symbol": sym,
        "exchange": exchange,
        "segment": segment,
        "side": side,
        "qty": int(qty),
        "price": float(price),
        "position_id": position_id.strip() or None,
        "notes": notes,
        "stop_loss": _optional_order_price(float(stop_loss)),
        "target_price": _optional_order_price(float(target_price)),
        "gross": charges.gross,
        "charges": charges.total,
        "net_cash": net,
    }
    try:
        tid = insert_trade(conn, trade)
    except Exception as exc:
        st.error(f"Could not save order: {exc}")
        return

    st.session_state["toast"] = (
        "success",
        f"Order #{tid} saved — {side} {int(qty)} {sym} @ {_fmt_inr(float(price))}. See **History**.",
    )
    st.session_state["nav_tab"] = "History"
    st.rerun()


def _style_completed_cycles_table(df: pd.DataFrame):
    dark_table = [
        {
            "selector": "th",
            "props": [
                ("background-color", "#1a1a1a"),
                ("color", "#e4e4e7"),
                ("border-color", "#262626"),
            ],
        },
        {
            "selector": "td",
            "props": [
                ("background-color", "#0f0f0f"),
                ("color", "#d4d4d8"),
                ("border-color", "#262626"),
            ],
        },
    ]
    display = df.copy()
    display["buy_date"] = pd.to_datetime(display["buy_date"]).dt.strftime("%Y-%m-%d %H:%M")
    display["sell_date"] = pd.to_datetime(display["sell_date"]).dt.strftime("%Y-%m-%d %H:%M")
    if "abs_return_pct" not in display.columns and "return_pct" in display.columns:
        display["abs_return_pct"] = display["return_pct"]
    if "hold_days" not in display.columns:
        display["hold_days"] = 0
    if "cagr_pct" not in display.columns:
        display["cagr_pct"] = None
    display = display[
        [
            "symbol",
            "position_id",
            "buy_date",
            "sell_date",
            "hold_days",
            "qty",
            "buy_avg",
            "sell_avg",
            "pnl_inr",
            "abs_return_pct",
            "cagr_pct",
            "return_pct",
            "charges",
        ]
    ]
    display = display.rename(
        columns={
            "symbol": "Symbol",
            "position_id": "Position ID",
            "buy_date": "Buy date",
            "sell_date": "Sell date",
            "hold_days": "Days held",
            "qty": "Qty",
            "buy_avg": "Buy avg",
            "sell_avg": "Sell avg",
            "pnl_inr": "P&L ₹",
            "abs_return_pct": "Abs. return %",
            "cagr_pct": "CAGR %",
            "return_pct": "Return %",
            "charges": "Charges",
        }
    )
    styler = display.style.set_table_styles(dark_table)
    pct_cols = ["Abs. return %", "Return %", "CAGR %"]
    for col in pct_cols:
        if col in display.columns:
            if hasattr(styler, "map"):
                styler = styler.map(_color_return_pct_cell, subset=[col])
            else:
                styler = styler.applymap(_color_return_pct_cell, subset=[col])
    if hasattr(styler, "map"):
        styler = styler.map(_color_pnl_cell, subset=["P&L ₹"])
    else:
        styler = styler.applymap(_color_pnl_cell, subset=["P&L ₹"])
    fmt = {
        "Buy avg": "₹{:,.2f}",
        "Sell avg": "₹{:,.2f}",
        "P&L ₹": "₹{:+,.2f}",
        "Abs. return %": "{:+.2f}%",
        "Return %": "{:+.2f}%",
        "Charges": "₹{:,.2f}",
        "Days held": "{:.0f}",
    }
    if "CAGR %" in display.columns:
        fmt["CAGR %"] = "{:+.2f}%"
    return styler.format(fmt, na_rep="—")


def page_risk_reward() -> None:
    st.subheader("Risk / reward calculator")
    st.caption(
        "Plan a **long** trade before you place an order. "
        "Entry must be above stop loss and below target."
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        entry = st.number_input("Entry price (₹)", min_value=0.01, value=1000.0, step=0.05)
    with c2:
        stop_loss = st.number_input("Stop loss (₹)", min_value=0.01, value=980.0, step=0.05)
    with c3:
        target = st.number_input("Target price (₹)", min_value=0.01, value=1050.0, step=0.05)

    qty = st.number_input(
        "Quantity (optional — for ₹ risk & reward)",
        min_value=0,
        value=0,
        step=1,
        help="Leave 0 to see per-share risk/reward only.",
    )

    if entry <= stop_loss:
        st.error("For a long trade, **entry** must be **above** stop loss.")
        return
    if target <= entry:
        st.error("For a long trade, **target** must be **above** entry.")
        return

    risk_per_share = entry - stop_loss
    reward_per_share = target - entry
    risk_reward_ratio = reward_per_share / risk_per_share if risk_per_share else 0.0
    risk_pct = (risk_per_share / entry) * 100
    reward_pct = (reward_per_share / entry) * 100

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Risk / share", _fmt_inr(risk_per_share), f"−{risk_pct:.2f}%")
    m2.metric("Reward / share", _fmt_inr(reward_per_share), f"+{reward_pct:.2f}%")
    m3.metric("Risk : reward", f"1 : {risk_reward_ratio:.2f}")
    m4.metric("Reward : risk", f"{risk_reward_ratio:.2f} : 1")

    if qty > 0:
        st.markdown("#### Position size")
        t1, t2, t3 = st.columns(3)
        total_risk = risk_per_share * qty
        total_reward = reward_per_share * qty
        _pnl_metric(t1, "Max loss (to stop)", -total_risk)
        _pnl_metric(t2, "Target profit", total_reward)
        t3.metric("Capital at entry", _fmt_inr(entry * qty))

    st.info(
        f"If price hits **stop** ({_fmt_inr(stop_loss)}), you lose "
        f"**{risk_pct:.2f}%** per share"
        + (f" (~{_fmt_inr(risk_per_share * qty)} on {qty} shares)." if qty > 0 else ".")
        + f" If price hits **target** ({_fmt_inr(target)}), you gain **+{reward_pct:.2f}%**"
        + (f" (~{_fmt_inr(reward_per_share * qty)})." if qty > 0 else ".")
    )


def page_history(conn) -> None:
    st.subheader("Trade history")
    starting = float(get_setting(conn, "starting_capital", "500000"))
    trades = list_trades(conn, limit=300)
    if not trades:
        st.write("No trades yet.")
        return

    trades_df = trades_to_df(trades)
    perf = _build_performance_context(starting, trades_df, use_live_ltp=True)
    _render_returns_section(
        perf,
        show_cycles_table=True,
        cycles_expanded=True,
        use_styled_cycles=True,
    )

    st.markdown("#### All trades")
    df = trades_to_df(trades)
    df["traded_at"] = pd.to_datetime(df["traded_at"]).dt.strftime("%Y-%m-%d %H:%M")
    if "stop_loss" not in df.columns:
        df["stop_loss"] = None
    if "target_price" not in df.columns:
        df["target_price"] = None
    df["Stop loss"] = df["stop_loss"].apply(_display_optional_price)
    df["Target price"] = df["target_price"].apply(_display_optional_price)
    show = df[
        [
            "id",
            "traded_at",
            "symbol",
            "side",
            "qty",
            "price",
            "Stop loss",
            "Target price",
            "gross",
            "charges",
            "net_cash",
            "position_id",
            "segment",
            "notes",
        ]
    ].copy()
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.markdown("#### Delete a trade")
    del_id = st.number_input("Trade ID to delete", min_value=1, step=1)
    if st.button("Delete trade", type="secondary"):
        delete_trade(conn, int(del_id))
        st.warning(f"Deleted trade #{int(del_id)}")
        st.rerun()


def page_settings(conn) -> None:
    st.subheader("Settings")
    starting = float(get_setting(conn, "starting_capital", "500000"))
    new_cap = st.number_input("Starting capital (₹)", value=starting, step=10000.0)
    if st.button("Save starting capital"):
        set_setting(conn, "starting_capital", str(new_cap))
        st.success("Saved.")
        st.rerun()

    st.markdown("#### Brokerage & charges (approximate)")
    cs = _load_charge_settings(conn)
    cs.brokerage_per_order = st.number_input("Brokerage per order (₹)", value=cs.brokerage_per_order)
    cs.gst_on_brokerage = st.number_input("GST on brokerage (0.18 = 18%)", value=cs.gst_on_brokerage, format="%.4f")
    if st.button("Save charge settings"):
        _save_charge_settings(conn, cs)
        st.success("Charge settings saved.")

    st.markdown("#### Backup (JSON file)")
    st.caption(
        "On Streamlit Cloud, download a backup before redeploy; import after restart."
    )
    backup = export_all(conn)
    st.download_button(
        "Download backup (JSON)",
        data=json.dumps(backup, indent=2),
        file_name=f"paper_trading_backup_{date.today().isoformat()}.json",
        mime="application/json",
    )
    uploaded = st.file_uploader("Import backup JSON", type=["json"])
    if uploaded and st.button("Import (merge)"):
        data = json.load(uploaded)
        n = import_backup(conn, data, replace=False)
        st.success(f"Imported {n} trades.")
        st.rerun()
    if uploaded and st.button("Import (replace all data)", type="secondary"):
        data = json.load(uploaded)
        n = import_backup(conn, data, replace=True)
        st.success(f"Replaced with {n} trades.")
        st.rerun()

    st.markdown("#### Danger zone")
    if st.button("Reset all trades", type="secondary"):
        delete_all_trades(conn)
        st.warning("All trades deleted.")
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Paper Trading — India",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_eye_friendly_theme()
    if not _check_password():
        return

    try:
        init_db()
    except ValueError as exc:
        st.error("Database connection failed")
        for line in str(exc).split("\n"):
            st.markdown(line)
        with st.expander("Supabase setup checklist"):
            st.markdown(
                """
1. **SQL Editor** → run `schema.sql` from this repo  
2. **Streamlit secrets** (no URL needed):
   ```
   SUPABASE_PROJECT_REF = "jrtqpdjrsxnmsdxguqlg"
   SUPABASE_REGION = "ap-southeast-1"
   SUPABASE_PASSWORD = "your-database-password"
   ```
3. Password = **Database password** in Supabase (reset under Project Settings → Database)  
4. Or copy **Session pooler** host from Supabase → **Connect** → set `SUPABASE_HOST`, `SUPABASE_USER`, `SUPABASE_PASSWORD`, `SUPABASE_PORT = "5432"`
                """
            )
        st.stop()
    st.sidebar.title("Paper trading")
    st.sidebar.caption(f"INR · NSE/BSE · Storage: **{storage_label()}**")
    if st.sidebar.button("Refresh LTP / prices"):
        fetch_ltp.clear()
        fetch_ltp_quote.clear()
        st.rerun()

    with connect() as conn:
        starting = float(get_setting(conn, "starting_capital", "500000"))
        cs = _load_charge_settings(conn)
        trades = list_trades(conn)
        trades_df = trades_to_df(trades)

        nav_options = ["Dashboard", "New trade", "Risk / reward", "History", "Settings"]
        if "nav_tab" in st.session_state:
            st.session_state["nav_tab_radio"] = st.session_state.pop("nav_tab")
        tab = st.sidebar.radio(
            "Navigate",
            nav_options,
            index=0,
            label_visibility="collapsed",
            key="nav_tab_radio",
        )

        st.title("Indian equities — paper trading")
        st.caption(
            "All NSE-listed equities supported · virtual portfolio · approximate Indian charges · "
            "not investment advice."
        )

        toast = st.session_state.pop("toast", None)
        if toast:
            kind, msg = toast
            if kind == "success":
                st.success(msg)
            elif kind == "error":
                st.error(msg)
            else:
                st.info(msg)

        if tab == "Dashboard":
            page_dashboard(conn, starting, trades_df)
        elif tab == "New trade":
            page_new_trade(conn, starting, trades_df, cs)
        elif tab == "Risk / reward":
            page_risk_reward()
        elif tab == "History":
            page_history(conn)
        else:
            page_settings(conn)


if __name__ == "__main__":
    main()
