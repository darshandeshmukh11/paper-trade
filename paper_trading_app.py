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

from charges import ChargeSettings, TradeCharges, compute_charges, net_cash_flow
from page_expenses import page_expenses, trades_expenses, consolidated_expense_summary
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
    update_trade,
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
        dp_delivery_sell=float(d.get("dp_delivery_sell", 15.93)),
    )


def _save_charge_settings(conn, cs: ChargeSettings) -> None:
    set_setting(
        conn,
        "charge_settings",
        json.dumps(
            {
                "dp_delivery_sell": cs.dp_delivery_sell,
            }
        ),
    )


def _format_zerodha_charge_preview(charges: TradeCharges) -> str:
    parts = [
        f"brokerage {_fmt_inr(charges.brokerage)}",
        f"STT {_fmt_inr(charges.stt)}",
        f"exchange & SEBI {_fmt_inr(charges.exchange_sebi)}",
        f"GST {_fmt_inr(charges.gst)}",
    ]
    if charges.stamp:
        parts.append(f"stamp {_fmt_inr(charges.stamp)}")
    if charges.dp:
        parts.append(f"DP {_fmt_inr(charges.dp)}")
    return " · ".join(parts)


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


def _fmt_trade_datetime(value: object) -> str:
    """Format a trade timestamp for display (handles mixed ISO / date-only strings)."""
    if value is None or value == "" or (isinstance(value, float) and pd.isna(value)):
        return "—"
    ts = pd.to_datetime(value, errors="coerce")
    if pd.isna(ts):
        return "—"
    if hasattr(ts, "tz") and ts.tz is not None:
        ts = ts.tz_convert(IST)
    elif hasattr(ts, "tz_localize"):
        ts = ts.tz_localize(IST)
    return ts.strftime("%Y-%m-%d %H:%M")


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


def _display_optional_pct(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{num:+.2f}%"


def _positive_optional_float(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _aggregate_open_exit_levels(lots: list[dict]) -> tuple[float | None, float | None]:
    """Qty-weighted stop loss / target from remaining open BUY lots."""
    sl_num = sl_den = 0.0
    tg_num = tg_den = 0.0
    for lot in lots:
        q = int(lot.get("qty") or 0)
        if q <= 0:
            continue
        sl = lot.get("stop_loss")
        tg = lot.get("target_price")
        if sl is not None:
            sl_num += float(sl) * q
            sl_den += q
        if tg is not None:
            tg_num += float(tg) * q
            tg_den += q
    stop = (sl_num / sl_den) if sl_den else None
    target = (tg_num / tg_den) if tg_den else None
    return stop, target


def _pct_distance_from_ltp(ltp: float, level: float | None, *, above_is_positive: bool) -> float | None:
    """% distance between LTP and a level.

    * Stop (``above_is_positive=True``): ``(ltp - level) / ltp`` → + means LTP still above stop.
    * Target (``above_is_positive=False``): ``(level - ltp) / ltp`` → + means still below target.
    """
    if level is None or ltp <= 0:
        return None
    level_f = float(level)
    if above_is_positive:
        return (ltp - level_f) / ltp * 100.0
    return (level_f - ltp) / ltp * 100.0


def _lot_key(row: pd.Series) -> tuple[str, str]:
    position_id = str(row.get("position_id") or "").strip()
    if position_id:
        return (str(row["symbol"]).upper(), position_id)
    return (str(row["symbol"]).upper(), "")


def _remaining_buy_lots(
    trades_df: pd.DataFrame,
    symbol: str | None = None,
) -> list[dict]:
    """Return FIFO-remaining BUY lots (qty > 0) with trade id and notes.

    Optionally filter to one symbol. Used for open-position notes editing and
    as the base for exit-order lot tracking.
    """
    if trades_df.empty:
        return []

    chron = trades_df.sort_values(["traded_at", "id"])
    lots_by_key: dict[tuple[str, str], list[dict]] = {}

    for _, row in chron.iterrows():
        side = str(row["side"]).upper()
        key = _lot_key(row)
        qty = int(row["qty"])

        if side == "BUY":
            lots_by_key.setdefault(key, []).append(
                {
                    "qty": qty,
                    "symbol": str(row["symbol"]).upper(),
                    "exchange": row.get("exchange", "NSE") or "NSE",
                    "segment": row.get("segment", "Equity Delivery") or "Equity Delivery",
                    "position_id": str(row.get("position_id") or "").strip() or None,
                    "stop_loss": _positive_optional_float(row.get("stop_loss")),
                    "target_price": _positive_optional_float(row.get("target_price")),
                    "buy_trade_id": int(row["id"]),
                    "notes": str(row.get("notes") or ""),
                }
            )
            continue

        if side != "SELL":
            continue

        remaining_sell_qty = qty
        sell_key_candidates = [key]
        if key[1] == "":
            sell_key_candidates = [k for k in lots_by_key if k[0] == key[0] and k[1] == ""]

        for sell_key in sell_key_candidates:
            lots = lots_by_key.get(sell_key, [])
            while lots and remaining_sell_qty > 0:
                match = min(lots[0]["qty"], remaining_sell_qty)
                lots[0]["qty"] -= match
                remaining_sell_qty -= match
                if lots[0]["qty"] <= 0:
                    lots.pop(0)
            if remaining_sell_qty <= 0:
                break

    lots = [lot for group in lots_by_key.values() for lot in group if lot["qty"] > 0]
    if symbol:
        sym = symbol.upper().strip()
        lots = [lot for lot in lots if lot["symbol"] == sym]
    return lots


def _aggregate_lot_notes(lots: list[dict]) -> str:
    """Distinct non-empty notes from lots, joined like Position.notes."""
    notes_list: list[str] = []
    for lot in lots:
        n = (lot.get("notes") or "").strip()
        if n and n not in notes_list:
            notes_list.append(n)
    return " | ".join(notes_list)


def _open_exit_order_lots(trades_df: pd.DataFrame) -> list[dict]:
    """Return remaining BUY lots that still carry a stop loss or target price."""
    return [
        lot
        for lot in _remaining_buy_lots(trades_df)
        if lot.get("stop_loss") is not None or lot.get("target_price") is not None
    ]


def _auto_execute_exit_orders(conn, trades_df: pd.DataFrame, cs: ChargeSettings) -> list[str]:
    """Create SELL trades when live LTP reaches a BUY lot's stop loss or target.

    Uses an uncached live quote so a stale LTP cache cannot delay exits.
    Fill price is the live LTP once the level is breached (market-style exit).
    """
    from live_price import fetch_live_price

    executed: list[str] = []
    now = datetime.now(IST)

    for lot in _open_exit_order_lots(trades_df):
        # Bypass Streamlit LTP cache — exits must see the latest price.
        ltp = fetch_live_price(lot["symbol"], lot["exchange"])
        if ltp is None:
            continue

        trigger_name = None
        trigger_level = None
        if lot["stop_loss"] is not None and float(ltp) <= lot["stop_loss"]:
            trigger_name = "stop loss"
            trigger_level = lot["stop_loss"]
        elif lot["target_price"] is not None and float(ltp) >= lot["target_price"]:
            trigger_name = "target"
            trigger_level = lot["target_price"]

        if trigger_level is None:
            continue

        fill_price = float(ltp)
        charges = compute_charges(
            "SELL",
            int(lot["qty"]),
            fill_price,
            lot["segment"],
            cs,
            exchange=lot["exchange"],
        )
        trade = {
            "traded_at": now.isoformat(),
            "symbol": lot["symbol"],
            "exchange": lot["exchange"],
            "segment": lot["segment"],
            "side": "SELL",
            "qty": int(lot["qty"]),
            "price": fill_price,
            "position_id": lot["position_id"],
            "notes": (
                f"Auto SELL: {trigger_name} {_fmt_inr(float(trigger_level))} "
                f"hit for BUY #{lot['buy_trade_id']} (filled @ LTP {_fmt_inr(fill_price)})"
            ),
            "stop_loss": None,
            "target_price": None,
            "gross": charges.gross,
            "charges": charges.total,
            "net_cash": net_cash_flow("SELL", charges),
        }
        tid = insert_trade(conn, trade)
        executed.append(
            f"Auto SELL #{tid}: {lot['qty']} {lot['symbol']} at {_fmt_inr(fill_price)} ({trigger_name})"
        )

    return executed


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
    if "Unrealized P&L" in df.columns:
        if hasattr(styler, "map"):
            styler = styler.map(_color_pnl_cell, subset=["Unrealized P&L"])
        else:
            styler = styler.applymap(_color_pnl_cell, subset=["Unrealized P&L"])
    return styler.format(
        {
            "Avg cost": "₹{:,.2f}",
            "LTP": "₹{:,.2f}",
            "Stop loss": _display_optional_price,
            "Target": _display_optional_price,
            "vs Stop %": _display_optional_pct,
            "vs Target %": _display_optional_pct,
            "Market value": "₹{:,.2f}",
            "Unrealized P&L": "₹{:+,.2f}",
            "Days held": lambda x: _human_days(float(x)) if x is not None else "—",
        }
    )


def _fmt_pct(value: Optional[float], signed: bool = True) -> str:
    if value is None:
        return "—"
    if signed:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def _human_days(days: float) -> str:
    """Format days as human-friendly string: '3d', '2w', '1y', or '<1d'."""
    try:
        d = float(days)
    except Exception:
        return "—"
    if d < 1:
        return "<1d"
    if d < 7:
        return f"{int(round(d))}d"
    if d < 30:
        weeks = int(round(d / 7))
        return f"{weeks}w"
    if d < 365:
        months = int(round(d / 30))
        return f"{months}m"
    years = int(round(d / 365))
    return f"{years}y"


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
    trades_df: Optional[pd.DataFrame] = None,
    starting: Optional[float] = None,
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

    # Equity curve and portfolio-level diagnostics (if trades + starting provided)
    if trades_df is not None and starting is not None:
        closed_df = perf.get("closed_df")
        eq_df = _compute_equity_timeline(trades_df, starting)
        if not eq_df.empty:
            st.markdown("#### Equity curve & portfolio diagnostics")
            c1, c2 = st.columns([3, 1])
            with c1:
                st.line_chart(eq_df["equity"], use_container_width=True)
            with c2:
                # terminal equity
                last_equity = float(eq_df["equity"].iloc[-1])
                total_pnl = last_equity - starting
                realized = realized_pnl(trades_df) if not trades_df.empty else 0.0
                unrealized = total_pnl - realized
                # Max drawdown
                mdd = _max_drawdown_pct(eq_df["equity"])
                # Win rate
                wr = _win_rate_pct(closed_df)

                _pnl_metric(st.container(), "Realized P&L", realized)
                _pnl_metric(st.container(), "Unrealized P&L", unrealized)
                _metric_pct(st.container(), "Max drawdown", mdd, "Peak-to-trough %")
                if wr is not None:
                    # color positive win rate green
                    color = PNL_COLOR_PROFIT if wr > 0 else PNL_COLOR_NEUTRAL
                    st.markdown(
                        f'<p style="margin:0;font-size:0.875rem;color:{PNL_COLOR_LABEL};">Win rate</p>'
                        f'<p style="margin:0;font-size:1.25rem;font-weight:700;color:{color};">{wr:.2f}%</p>',
                        unsafe_allow_html=True,
                    )

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



def _open_position_hold_days(symbol: str, target_qty: int, trades_df: pd.DataFrame) -> float:
    """Estimate days held for open position using FIFO remaining lots.

    Returns number of calendar days since oldest remaining buy lot (0 if unknown).
    """
    if trades_df is None or trades_df.empty or target_qty <= 0:
        return 0
    chron = trades_df.sort_values(["traded_at", "id"]) 
    sym_trades = chron[chron["symbol"].str.upper() == symbol.upper()]
    if sym_trades.empty:
        return 0

    lots: list[dict] = []
    for _, row in sym_trades.iterrows():
        side = str(row["side"]).upper()
        qty = int(row["qty"])
        price = float(row["price"])
        traded_at = row["traded_at"]
        if side == "BUY":
            lots.append({"qty": qty, "price": price, "traded_at": traded_at})
            continue
        if side == "SELL":
            remaining = qty
            while remaining > 0 and lots:
                lot = lots[0]
                match = min(remaining, int(lot["qty"]))
                lot["qty"] -= match
                remaining -= match
                if lot["qty"] <= 0:
                    lots.pop(0)
            # if sells exceed buys, we just drop

    # after processing, lots are remaining open lots
    rem_qty = sum(int(l["qty"]) for l in lots)
    if rem_qty <= 0:
        return 0
    # compute weighted-average days held across remaining lots
    now = pd.Timestamp.now(tz="UTC")
    weighted_days = 0.0
    total_qty = 0
    for lot in lots:
        q = int(lot["qty"])
        if q <= 0:
            continue
        ts = pd.to_datetime(lot["traded_at"], utc=True, errors="coerce")
        if pd.isna(ts):
            continue
        days = max((now - ts).days, 0)
        weighted_days += days * q
        total_qty += q
    if total_qty <= 0:
        return 0
    avg_days = weighted_days / total_qty
    return float(avg_days)


def _build_open_position_rows(positions: list, trades_df: pd.DataFrame | None = None) -> tuple[list[dict], float, float, int]:
    """Live LTP rows plus unrealized MTM and holdings value. Optionally compute Days held from trades_df."""
    mtm = 0.0
    holdings_value = 0.0
    live_quote_count = 0
    pos_rows: list[dict] = []
    quotes = _fetch_quotes_parallel(positions)
    lots_by_symbol: dict[str, list[dict]] = {}
    if trades_df is not None and not trades_df.empty:
        for lot in _remaining_buy_lots(trades_df):
            lots_by_symbol.setdefault(lot["symbol"], []).append(lot)
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
        days_held = _open_position_hold_days(p.symbol, p.qty, trades_df) if trades_df is not None else 0.0
        stop_loss, target = _aggregate_open_exit_levels(lots_by_symbol.get(p.symbol, []))
        vs_stop = _pct_distance_from_ltp(float(ltp), stop_loss, above_is_positive=True)
        vs_target = _pct_distance_from_ltp(float(ltp), target, above_is_positive=False)
        pos_rows.append(
            {
                "Symbol": p.symbol,
                "Qty": p.qty,
                "Avg cost": round(p.avg_cost, 2),
                "LTP": round(ltp, 2),
                "Stop loss": round(stop_loss, 2) if stop_loss is not None else None,
                "vs Stop %": round(vs_stop, 2) if vs_stop is not None else None,
                "Target": round(target, 2) if target is not None else None,
                "vs Target %": round(vs_target, 2) if vs_target is not None else None,
                "Quote": quote_note,
                "Market value": round(mv, 2),
                "Unrealized P&L": round(upl, 2),
                "Days held": float(days_held),
                "Notes": (p.notes or "").strip() or "—",
            }
        )
    return pos_rows, mtm, holdings_value, live_quote_count


def _render_open_positions_section(
    conn,
    trades_df: pd.DataFrame,
    pos_rows: list[dict],
    positions: list,
    live_quote_count: int,
) -> None:
    st.subheader("Open positions")
    if pos_rows:
        st.dataframe(
            _style_open_positions_table(pd.DataFrame(pos_rows)),
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "**vs Stop %** = how far LTP is above stop (+ = cushion). "
            "**vs Target %** = how far LTP is below target (+ = still to go). "
            "Levels are qty-weighted from remaining BUY lots with stop/target set. "
            "When LTP hits stop or target, the position is auto-sold (check on each app load / **Refresh LTP**)."
        )
        if live_quote_count < len(positions):
            st.warning(
                f"Live LTP for {live_quote_count}/{len(positions)} positions — "
                "others use avg cost until Yahoo returns a price. Use **Refresh LTP** in the sidebar."
            )

        st.markdown("#### Edit exit levels & notes")
        symbols = [p.symbol for p in positions]
        note_sym = st.selectbox(
            "Open position",
            options=symbols,
            key="open_pos_notes_symbol",
        )
        lots = _remaining_buy_lots(trades_df, note_sym)
        if not lots:
            st.caption(f"No remaining BUY lots for {note_sym}.")
        else:
            trade_ids = [int(lot["buy_trade_id"]) for lot in lots]
            agg_stop, agg_target = _aggregate_open_exit_levels(lots)
            prefill = _aggregate_lot_notes(lots)
            st.caption(
                f"Saving updates stop, target, and notes on remaining BUY trade(s): "
                + ", ".join(f"#{tid}" for tid in trade_ids)
                + ". Leave stop/target at 0 to clear."
            )
            el1, el2 = st.columns(2)
            with el1:
                edit_stop = st.number_input(
                    "Stop loss (₹)",
                    min_value=0.0,
                    value=float(agg_stop) if agg_stop is not None else 0.0,
                    step=0.05,
                    key=f"open_pos_stop_{note_sym}",
                    help="Optional. Set 0 to clear. Auto-sells when LTP ≤ stop.",
                )
            with el2:
                edit_target = st.number_input(
                    "Target price (₹)",
                    min_value=0.0,
                    value=float(agg_target) if agg_target is not None else 0.0,
                    step=0.05,
                    key=f"open_pos_target_{note_sym}",
                    help="Optional. Set 0 to clear. Auto-sells when LTP ≥ target.",
                )
            pos_notes = st.text_area(
                "Notes",
                value=prefill,
                height=100,
                key=f"open_pos_notes_text_{note_sym}",
                placeholder="Thesis, levels, reminders…",
            )
            if st.button("Save exit levels & notes", key="save_open_pos_notes"):
                text = (pos_notes or "").strip()
                updates = {
                    "notes": text,
                    "stop_loss": _optional_order_price(float(edit_stop)),
                    "target_price": _optional_order_price(float(edit_target)),
                }
                for tid in trade_ids:
                    update_trade(conn, tid, updates)
                st.success(
                    f"Updated stop/target/notes on {len(trade_ids)} BUY trade(s) for {note_sym}."
                )
                st.rerun()
    else:
        st.info("No open positions. Place a BUY from the **New trade** tab.")


def _render_charges_summary_section(trades_df: pd.DataFrame, total_pnl: float) -> None:
    """Compact charges snapshot on the main dashboard: overall total + per-trade table."""
    st.markdown("#### 💸 Charges — per trade & overall")

    if trades_df.empty:
        st.caption("No trades yet — charges will appear here once you place a trade.")
        return

    expenses_df = trades_expenses(trades_df)
    summary = consolidated_expense_summary(expenses_df)

    # total_pnl (cash + holdings − starting) is already net of all charges, since every
    # trade's net_cash bakes charges in. Gross P&L is what you'd have made with zero charges.
    gross_pnl_before_charges = total_pnl + summary["total"]

    d1, d2, d3, d4 = st.columns(4)
    d1.metric("Gross P&L (before charges)", _fmt_inr(gross_pnl_before_charges))
    d2.metric("Total charges (all trades)", _fmt_inr(summary["total"]), f"{summary['avg_pct']:.4f}% of turnover")
    _pnl_metric(d3, "Net P&L (after charges)", total_pnl)
    d4.metric("Trades charged", str(len(expenses_df)))

    with st.expander(f"Per-trade charges ({len(expenses_df)} trade{'s' if len(expenses_df) != 1 else ''})", expanded=False):
        cols = [
            "Trade ID", "Date", "Symbol", "Side", "Qty", "Price", "Gross ₹",
            "Brokerage ₹", "STT ₹", "Exchange & SEBI ₹", "Stamp ₹", "GST ₹", "DP ₹",
            "Total Charges ₹",
        ]
        st.dataframe(expenses_df[cols], use_container_width=True, hide_index=True)
        st.caption(
            "Full breakdown, buy/sell split, and profit waterfall are on the **Expenses** tab."
        )


def page_dashboard(conn, starting: float, trades_df: pd.DataFrame) -> None:
    positions = compute_positions(trades_df)
    pos_rows, mtm, holdings_value, live_quote_count = _build_open_position_rows(positions, trades_df)
    _render_open_positions_section(conn, trades_df, pos_rows, positions, live_quote_count)

    st.caption(
        "Open-position P&L uses **live LTP** (Yahoo). Cached ~90s · **Refresh LTP / prices** in sidebar. "
        "**Total P&L** = cash + holdings at LTP − starting capital."
    )

    cash = cash_balance(starting, trades_df)
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

    _render_charges_summary_section(trades_df, total_pnl)

    perf = _build_performance_context(starting, trades_df, use_live_ltp=True)
    closed_df = perf.get("closed_df")
    if closed_df is None:
        closed_df = pd.DataFrame()
    _render_turnover_and_tax(trades_df, closed_df, total_pnl)

    _render_returns_section(
        perf,
        trades_df=trades_df,
        starting=starting,
        show_cycles_table=True,
        cycles_expanded=True,
        use_styled_cycles=True,
    )


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

    sym = normalize_nse_symbol(symbol)
    ltp = fetch_ltp(sym, exchange)
    default_price = round(ltp, 2) if ltp is not None else 1000.0

    with st.form("new_trade", clear_on_submit=True):
        c2, c3 = st.columns(2)
        with c2:
            side = st.selectbox("Side", ["BUY", "SELL"])
            segment = st.selectbox("Segment", ["Equity Delivery", "Equity Intraday"])
        with c3:
            qty = st.number_input("Quantity", min_value=1, value=10, step=1)
            price = st.number_input("Price (₹)", min_value=0.01, value=default_price, step=0.05)
        if ltp is not None:
            st.caption(
                f"**Live LTP** for **{sym}** ({exchange}) — ₹{default_price:,.2f}. "
                "Updates when you change symbol or exchange. Use **Refresh LTP / prices** in the sidebar to force refresh."
            )
        else:
            st.warning(
                f"Could not fetch live price for **{sym}** ({exchange}). "
                "Enter price manually or use **Refresh LTP / prices** in the sidebar."
            )

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
                help="Optional. Leave 0 if not set. Auto-sells when LTP ≤ stop. Editable later on Dashboard.",
            )
        with c7:
            target_price = st.number_input(
                "Target price (₹)",
                min_value=0.0,
                value=0.0,
                step=0.05,
                help="Optional. Leave 0 if not set. Auto-sells when LTP ≥ target. Editable later on Dashboard.",
            )

        notes = st.text_input("Notes", placeholder="Swing entry, support rejection, etc.")
        submitted = st.form_submit_button("Submit order", type="primary")

    if not submitted:
        preview = compute_charges(side, int(qty), float(price), segment, cs, exchange=exchange)
        net = net_cash_flow(side, preview)
        flow = "leaves your account" if net < 0 else "enters your account"
        st.info(
            f"**Order preview** (Zerodha calculator rates) · "
            f"**Trade value:** {_fmt_inr(preview.gross)} · "
            f"**Est. charges:** {_fmt_inr(preview.total)} "
            f"({_format_zerodha_charge_preview(preview)}) · "
            f"**Cash impact:** {_fmt_inr(net)} ({flow})"
        )
        return

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

    charges = compute_charges(side, int(qty), float(price), segment, cs, exchange=exchange)
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
    display["buy_date"] = display["buy_date"].apply(_fmt_trade_datetime)
    display["sell_date"] = display["sell_date"].apply(_fmt_trade_datetime)
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
    # Win/Loss badge column
    if "P&L ₹" in display.columns:
        display["Outcome"] = display["P&L ₹"].apply(lambda x: "Win" if float(x) > 0 else ("Loss" if float(x) < 0 else "Even"))
        # Place Outcome after Symbol
        cols = list(display.columns)
        # move Outcome to be right after Symbol
        try:
            cols.insert(1, cols.pop(cols.index("Outcome")))
            display = display[cols]
        except ValueError:
            pass
    # create Styler after we've finished mutating `display`
    styler = display.style.set_table_styles(dark_table)
    pct_cols = ["Abs. return %", "Return %", "CAGR %"]
    for col in pct_cols:
        if col in display.columns:
            if hasattr(styler, "map"):
                styler = styler.map(_color_return_pct_cell, subset=[col])
            else:
                styler = styler.applymap(_color_return_pct_cell, subset=[col])
    if "P&L ₹" in display.columns:
        if hasattr(styler, "map"):
            styler = styler.map(_color_pnl_cell, subset=["P&L ₹"])
        else:
            styler = styler.applymap(_color_pnl_cell, subset=["P&L ₹"])
    # Style Outcome badges
    def _outcome_style(val: object) -> str:
        try:
            s = str(val)
        except Exception:
            return ""
        if s == "Win":
            return f"background-color: {PNL_COLOR_PROFIT}; color: #071f04; font-weight:700; text-align:center"
        if s == "Loss":
            return f"background-color: {PNL_COLOR_LOSS}; color: #2a0505; font-weight:700; text-align:center"
        return "background-color: transparent; color: #d4d4d8"

    if "Outcome" in display.columns:
        if hasattr(styler, "map"):
            styler = styler.map(_outcome_style, subset=["Outcome"])
        else:
            styler = styler.applymap(_outcome_style, subset=["Outcome"])
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


def _compute_equity_timeline(trades_df: pd.DataFrame, starting_capital: float) -> pd.DataFrame:
    """Compute simple equity timeline at each trade date using cost-basis for holdings."""
    if trades_df.empty:
        return pd.DataFrame(columns=["date", "equity"]).set_index("date")
    chron = trades_df.sort_values(["traded_at", "id"]).copy()
    cash = starting_capital
    positions: dict[str, dict] = {}
    rows = []
    for _, row in chron.iterrows():
        side = str(row["side"]).upper()
        sym = str(row["symbol"]).upper()
        qty = int(row["qty"])
        price = float(row["price"])
        net = float(row.get("net_cash", 0.0))
        # update cash using net_cash recorded (includes charges)
        cash += net
        if sym not in positions:
            positions[sym] = {"qty": 0, "cost_basis": 0.0}
        p = positions[sym]
        if side == "BUY":
            p["qty"] += qty
            p["cost_basis"] += qty * price
        elif side == "SELL":
            # reduce qty and cost basis FIFO-ish using average cost
            if p["qty"] <= 0:
                # nothing to reduce
                pass
            else:
                avg = p["cost_basis"] / p["qty"] if p["qty"] else 0.0
                remove = min(qty, p["qty"])
                p["cost_basis"] -= avg * remove
                p["qty"] -= remove
        holdings_value = sum(v["cost_basis"] for v in positions.values())
        equity = cash + holdings_value
        rows.append({"date": pd.to_datetime(row["traded_at"]), "equity": equity})
    df = pd.DataFrame(rows).drop_duplicates(subset=["date"]).set_index("date").sort_index()
    # include starting point
    start_dt = pd.to_datetime(chron.iloc[0]["traded_at"]) - pd.Timedelta(seconds=1)
    start_row = pd.DataFrame([{"date": start_dt, "equity": starting_capital}]).set_index("date")
    df = pd.concat([start_row, df])
    return df


def _max_drawdown_pct(equity_series: pd.Series) -> float:
    if equity_series.empty:
        return 0.0
    roll_max = equity_series.cummax()
    drawdown = (equity_series - roll_max) / roll_max
    mdd = drawdown.min() * 100.0
    return float(mdd)


def _win_rate_pct(closed_df: pd.DataFrame) -> Optional[float]:
    if closed_df is None or closed_df.empty:
        return None
    wins = (closed_df["pnl_inr"] > 0).sum()
    total = len(closed_df)
    return float(wins / total * 100.0)


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
        trades_df=trades_df,
        starting=starting,
        show_cycles_table=True,
        cycles_expanded=True,
        use_styled_cycles=True,
    )

    st.markdown("#### All trades")
    df = trades_to_df(trades)
    df["traded_at"] = df["traded_at"].apply(_fmt_trade_datetime)
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

    st.markdown("#### Edit trade")
    trade_ids = [int(r["id"]) for r in trades]
    id_labels = {
        int(r["id"]): (
            f"#{int(r['id'])} · {r['symbol']} · {r['side']} · "
            f"{_fmt_trade_datetime(r['traded_at'])}"
        )
        for r in trades
    }
    selected_id = st.selectbox(
        "Trade ID",
        options=trade_ids,
        format_func=lambda tid: id_labels.get(tid, str(tid)),
        key="history_notes_trade_id",
    )
    selected_row = next((r for r in trades if int(r["id"]) == int(selected_id)), None)
    current_notes = str((selected_row or {}).get("notes") or "")
    selected_side = str((selected_row or {}).get("side") or "").upper()
    current_stop = _positive_optional_float((selected_row or {}).get("stop_loss"))
    current_target = _positive_optional_float((selected_row or {}).get("target_price"))

    if selected_side == "BUY":
        st.caption("Stop / target apply to this BUY lot. Auto-sell fires when LTP hits either level.")
        h1, h2 = st.columns(2)
        with h1:
            hist_stop = st.number_input(
                "Stop loss (₹)",
                min_value=0.0,
                value=float(current_stop) if current_stop is not None else 0.0,
                step=0.05,
                key=f"history_stop_{selected_id}",
                help="Optional. Set 0 to clear.",
            )
        with h2:
            hist_target = st.number_input(
                "Target price (₹)",
                min_value=0.0,
                value=float(current_target) if current_target is not None else 0.0,
                step=0.05,
                key=f"history_target_{selected_id}",
                help="Optional. Set 0 to clear.",
            )
    else:
        hist_stop = None
        hist_target = None
        st.caption("Stop / target are set on BUY lots only.")

    edited_notes = st.text_area(
        "Notes",
        value=current_notes,
        height=120,
        key=f"history_notes_text_{selected_id}",
        placeholder="Add context for this fill…",
    )
    if st.button("Save trade", key="history_save_notes"):
        updates: dict = {"notes": (edited_notes or "").strip()}
        if selected_side == "BUY":
            updates["stop_loss"] = _optional_order_price(float(hist_stop or 0))
            updates["target_price"] = _optional_order_price(float(hist_target or 0))
        update_trade(conn, int(selected_id), updates)
        st.success(f"Saved trade #{int(selected_id)}.")
        st.rerun()

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

    st.markdown("#### Brokerage & charges (Zerodha calculator)")
    st.caption(
        "Rates match [Zerodha's brokerage calculator](https://zerodha.com/brokerage-calculator/): "
        "₹0 delivery brokerage, intraday min(₹20, 0.03%), plus STT, exchange, SEBI, GST, stamp duty, and DP on delivery sells."
    )
    cs = _load_charge_settings(conn)
    cs.dp_delivery_sell = st.number_input(
        "DP charges — delivery sell (₹)",
        value=cs.dp_delivery_sell,
        help="Depository charge per delivery sell (Zerodha default ₹15.93 + GST is included in their calculator total).",
    )
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
        auto_exits = _auto_execute_exit_orders(conn, trades_df, cs)
        if auto_exits:
            trades = list_trades(conn)
            trades_df = trades_to_df(trades)
            st.session_state["toast"] = (
                "success",
                " · ".join(auto_exits),
            )

        nav_options = ["Dashboard", "New trade", "Risk / reward", "Expenses", "History", "Settings"]
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
            "All NSE-listed equities supported · virtual portfolio · Zerodha brokerage calculator rates · "
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
        elif tab == "Expenses":
            positions = compute_positions(trades_df)
            _, _, holdings_value, _ = _build_open_position_rows(positions, trades_df)
            terminal_equity = cash_balance(starting, trades_df) + holdings_value
            page_expenses(trades_df, starting, terminal_equity)
        elif tab == "History":
            page_history(conn)
        else:
            page_settings(conn)


if __name__ == "__main__":
    main()
