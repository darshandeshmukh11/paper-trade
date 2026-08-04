"""Expense analysis page for paper trading app."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import streamlit as st

from charges import compute_charges, ChargeSettings
from portfolio import completed_round_trips, realized_pnl, trades_to_df


def _fmt_inr(x: float) -> str:
    return f"₹{x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x:.4f}%"


def trades_expenses(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Generate per-trade expense breakdown from trades dataframe."""
    if trades_df.empty:
        return pd.DataFrame()
    
    rows: list[dict] = []
    settings = ChargeSettings()
    
    for _, row in trades_df.iterrows():
        trade_id = int(row["id"])
        symbol = str(row["symbol"]).upper()
        side = str(row["side"]).upper()
        qty = int(row["qty"])
        price = float(row["price"])
        gross = float(row.get("gross", qty * price))
        exchange = str(row.get("exchange", "NSE"))
        segment = str(row.get("segment", "Equity Delivery"))
        traded_at = row.get("traded_at", "")
        
        charges = compute_charges(side, qty, price, segment, settings, exchange=exchange)
        
        rows.append({
            "Trade ID": trade_id,
            "Date": str(traded_at)[:10] if traded_at else "—",
            "Symbol": symbol,
            "Side": side,
            "Qty": qty,
            "Price": round(price, 2),
            "Gross ₹": round(gross, 2),
            "Brokerage ₹": round(charges.brokerage, 2),
            "STT ₹": round(charges.stt, 2),
            "Exchange & SEBI ₹": round(charges.exchange_sebi, 2),
            "Stamp ₹": round(charges.stamp, 2),
            "GST ₹": round(charges.gst, 2),
            "DP ₹": round(charges.dp, 2),
            "Total Charges ₹": round(charges.total, 2),
        })
    
    return pd.DataFrame(rows)


def consolidated_expense_summary(expenses_df: pd.DataFrame) -> dict:
    """Compute consolidated expense totals."""
    if expenses_df.empty:
        return {
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_sebi": 0.0,
            "stamp": 0.0,
            "gst": 0.0,
            "dp": 0.0,
            "total": 0.0,
            "gross_value": 0.0,
            "avg_pct": 0.0,
        }
    
    total_brokerage = float(expenses_df["Brokerage ₹"].sum())
    total_stt = float(expenses_df["STT ₹"].sum())
    total_exchange_sebi = float(expenses_df["Exchange & SEBI ₹"].sum())
    total_stamp = float(expenses_df["Stamp ₹"].sum())
    total_gst = float(expenses_df["GST ₹"].sum())
    total_dp = float(expenses_df["DP ₹"].sum())
    total_charges = float(expenses_df["Total Charges ₹"].sum())
    gross_value = float(expenses_df["Gross ₹"].sum())
    
    avg_pct = (total_charges / gross_value * 100) if gross_value > 0 else 0.0
    
    return {
        "brokerage": total_brokerage,
        "stt": total_stt,
        "exchange_sebi": total_exchange_sebi,
        "stamp": total_stamp,
        "gst": total_gst,
        "dp": total_dp,
        "total": total_charges,
        "gross_value": gross_value,
        "avg_pct": avg_pct,
    }


def buy_sell_expense_split(expenses_df: pd.DataFrame) -> dict:
    """Split expenses by buy vs sell side."""
    if expenses_df.empty:
        return {"BUY": {}, "SELL": {}}
    
    result = {}
    for side in ["BUY", "SELL"]:
        side_df = expenses_df[expenses_df["Side"] == side]
        
        if side_df.empty:
            result[side] = {
                "trades": 0,
                "brokerage": 0.0,
                "stt": 0.0,
                "exchange_sebi": 0.0,
                "stamp": 0.0,
                "gst": 0.0,
                "dp": 0.0,
                "total": 0.0,
                "gross_value": 0.0,
            }
        else:
            result[side] = {
                "trades": len(side_df),
                "brokerage": float(side_df["Brokerage ₹"].sum()),
                "stt": float(side_df["STT ₹"].sum()),
                "exchange_sebi": float(side_df["Exchange & SEBI ₹"].sum()),
                "stamp": float(side_df["Stamp ₹"].sum()),
                "gst": float(side_df["GST ₹"].sum()),
                "dp": float(side_df["DP ₹"].sum()),
                "total": float(side_df["Total Charges ₹"].sum()),
                "gross_value": float(side_df["Gross ₹"].sum()),
            }
    
    return result


def page_expenses(trades_df: pd.DataFrame, starting: float, terminal_equity: float) -> None:
    """Render detailed expense analysis page."""
    st.subheader("Trading Expenses & Charges Analysis")
    
    if trades_df.empty:
        st.info("No trades yet. Start trading to see expense analysis.")
        return
    
    # Generate expense breakdown
    expenses_df = trades_expenses(trades_df)
    summary = consolidated_expense_summary(expenses_df)
    split = buy_sell_expense_split(expenses_df)
    closed_df = completed_round_trips(trades_df)
    total_pnl = terminal_equity - starting
    realized = realized_pnl(trades_df)
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 1: CONSOLIDATED CHARGES SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### 📊 Consolidated Charges (All Trades)")
    
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Gross Traded Value", _fmt_inr(summary["gross_value"]))
    col2.metric("Total Charges", _fmt_inr(summary["total"]), f"{_fmt_pct(summary['avg_pct'])}")
    col3.metric("Avg Charge %", _fmt_pct(summary["avg_pct"]))
    col4.metric("Total Trades", str(len(expenses_df)))
    
    # Charge breakdown columns
    cb1, cb2, cb3 = st.columns(3)
    cb1.metric("Brokerage", _fmt_inr(summary["brokerage"]))
    cb2.metric("STT (tax)", _fmt_inr(summary["stt"]))
    cb3.metric("Exchange & SEBI", _fmt_inr(summary["exchange_sebi"]))
    
    cb4, cb5, cb6 = st.columns(3)
    cb4.metric("Stamp Duty", _fmt_inr(summary["stamp"]))
    cb5.metric("GST", _fmt_inr(summary["gst"]))
    cb6.metric("DP (Delivery)", _fmt_inr(summary["dp"]))
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 2: BUY VS SELL EXPENSE SPLIT
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### 🔀 Expenses by Side (Buy vs Sell)")
    
    buy_data = split["BUY"]
    sell_data = split["SELL"]
    
    sb1, sb2 = st.columns(2)
    
    with sb1:
        st.markdown("#### 📈 BUY Trades")
        st.metric("Trades", buy_data.get("trades", 0))
        st.metric("Gross Value", _fmt_inr(buy_data.get("gross_value", 0)))
        st.metric("Total Charges", _fmt_inr(buy_data.get("total", 0)))
        
        buy_breakdown = {
            "Brokerage": buy_data.get("brokerage", 0),
            "Stamp Duty": buy_data.get("stamp", 0),
            "Exchange & SEBI": buy_data.get("exchange_sebi", 0),
            "GST": buy_data.get("gst", 0),
        }
        for label, value in buy_breakdown.items():
            st.caption(f"{label}: {_fmt_inr(value)}")
    
    with sb2:
        st.markdown("#### 📉 SELL Trades")
        st.metric("Trades", sell_data.get("trades", 0))
        st.metric("Gross Value", _fmt_inr(sell_data.get("gross_value", 0)))
        st.metric("Total Charges", _fmt_inr(sell_data.get("total", 0)))
        
        sell_breakdown = {
            "Brokerage": sell_data.get("brokerage", 0),
            "STT": sell_data.get("stt", 0),
            "Exchange & SEBI": sell_data.get("exchange_sebi", 0),
            "GST": sell_data.get("gst", 0),
            "DP": sell_data.get("dp", 0),
        }
        for label, value in sell_breakdown.items():
            st.caption(f"{label}: {_fmt_inr(value)}")
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 3: PROFIT BREAKDOWN (AFTER ALL DEDUCTIONS)
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### 💰 Profit Breakdown (After Deductions)")
    
    from portfolio import ltcg_tax_summary, DEFAULT_LTCG_RATE
    
    tax_summary = ltcg_tax_summary(closed_df, total_pnl, ltcg_rate=DEFAULT_LTCG_RATE)
    ltcg_tax = tax_summary.get("ltcg_tax", 0.0)
    
    unrealized = total_pnl - realized
    net_after_charges = realized - summary["total"]
    net_after_all = total_pnl - summary["total"] - ltcg_tax
    
    pb1, pb2 = st.columns(2)
    
    with pb1:
        st.markdown("#### Gross P&L")
        color_g = "green" if total_pnl > 0 else "red" if total_pnl < 0 else "gray"
        st.markdown(f'<p style="color: {color_g}; font-size: 1.5rem; font-weight: bold;">{_fmt_inr(total_pnl)}</p>', unsafe_allow_html=True)
        
        st.markdown("##### Components")
        st.caption(f"Realized: {_fmt_inr(realized)}")
        st.caption(f"Unrealized: {_fmt_inr(unrealized)}")
    
    with pb2:
        st.markdown("#### Net P&L (After Deductions)")
        color_n = "green" if net_after_all > 0 else "red" if net_after_all < 0 else "gray"
        st.markdown(f'<p style="color: {color_n}; font-size: 1.5rem; font-weight: bold;">{_fmt_inr(net_after_all)}</p>', unsafe_allow_html=True)
        
        st.markdown("##### Deductions")
        st.caption(f"Trading charges: {_fmt_inr(summary['total'])}")
        st.caption(f"LTCG tax (15%): {_fmt_inr(ltcg_tax)}")
    
    # Detailed waterfall
    st.markdown("#### Detailed Profit Waterfall")
    waterfall = pd.DataFrame({
        "Component": [
            "Gross Profit",
            "Trading Charges",
            "After Charges",
            "LTCG Tax",
            "Net Profit",
        ],
        "Amount ₹": [
            total_pnl,
            -summary["total"],
            net_after_charges,
            -ltcg_tax,
            net_after_all,
        ],
    })
    st.dataframe(waterfall, use_container_width=True, hide_index=True)
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 4: PER-TRADE EXPENSE DETAILS
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### 🔍 Per-Trade Expense Details")
    st.caption("Click on column headers to sort. Analyze which trades had highest expenses.")
    
    display_df = expenses_df.copy()
    st.dataframe(
        display_df,
        use_container_width=True,
        hide_index=True,
    )
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 5: EFFICIENCY METRICS
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### ⚡ Trading Efficiency Metrics")
    
    if not closed_df.empty:
        win_count = (closed_df["pnl_inr"] > 0).sum()
        total_trades = len(closed_df)
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
        
        winning_sum = closed_df[closed_df["pnl_inr"] > 0]["pnl_inr"].sum()
        losing_sum = abs(closed_df[closed_df["pnl_inr"] < 0]["pnl_inr"].sum())
        profit_factor = (winning_sum / losing_sum) if losing_sum > 0 else 0.0
        
        avg_pnl = realized / total_trades if total_trades > 0 else 0.0
        exp_to_turnover = (summary["total"] / summary["gross_value"] * 100) if summary["gross_value"] > 0 else 0.0
        
        em1, em2, em3, em4 = st.columns(4)
        em1.metric("Win Rate", f"{win_rate:.2f}%", f"{win_count}/{total_trades} trades")
        em2.metric("Profit Factor", f"{profit_factor:.2f}x", "Wins/Losses ratio")
        em3.metric("Avg P&L/Trade", _fmt_inr(avg_pnl))
        em4.metric("Expense Ratio", _fmt_pct(exp_to_turnover), "Charges as % of turnover")
    else:
        st.info("No closed trades yet to calculate efficiency metrics.")
    
    # ═══════════════════════════════════════════════════════════════════════
    # SECTION 6: KEY INSIGHTS
    # ═══════════════════════════════════════════════════════════════════════
    st.markdown("### 💡 Insights")
    
    insights = []
    
    if summary["avg_pct"] > 0.5:
        insights.append(f"🔴 **High expenses**: Your charges are {_fmt_pct(summary['avg_pct'])} of turnover. Consider reviewing segment choices (intraday has brokerage; delivery doesn't).")
    elif summary["avg_pct"] > 0.2:
        insights.append(f"🟡 **Moderate expenses**: Charges are {_fmt_pct(summary['avg_pct'])} of turnover.")
    else:
        insights.append(f"🟢 **Low expenses**: Charges are {_fmt_pct(summary['avg_pct'])} of turnover—efficient trading.")
    
    if buy_data.get("total", 0) > 0 and sell_data.get("total", 0) > 0:
        buy_ratio = (buy_data["total"] / buy_data.get("gross_value", 1) * 100) if buy_data.get("gross_value") else 0
        sell_ratio = (sell_data["total"] / sell_data.get("gross_value", 1) * 100) if sell_data.get("gross_value") else 0
        if sell_ratio > buy_ratio:
            insights.append(f"📊 **Sell-side costs higher**: SELL charges are {_fmt_pct(sell_ratio)} vs BUY {_fmt_pct(buy_ratio)} (due to STT + DP on delivery sells).")
    
    if net_after_all < total_pnl:
        tax_impact_pct = ((summary["total"] + ltcg_tax) / total_pnl * 100) if total_pnl > 0 else 0
        insights.append(f"📉 **Total deductions**: Charges + taxes reduce profit by {tax_impact_pct:.1f}%.")
    
    for insight in insights:
        st.markdown(f"- {insight}")
