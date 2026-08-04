"""Detailed expense analysis and consolidated charges summary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pandas as pd


@dataclass
class TradeExpenseBreakdown:
    """Per-trade expense breakdown matching trade_id."""
    
    trade_id: int
    symbol: str
    side: str
    qty: int
    price: float
    gross: float
    brokerage: float
    stt: float
    exchange_sebi: float
    stamp: float
    gst: float
    dp: float
    total_charges: float


def trades_expenses(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Generate per-trade expense breakdown from trades dataframe.
    
    Returns DataFrame with columns:
    - trade_id, symbol, side, qty, price, gross
    - brokerage, stt, exchange_sebi, stamp, gst, dp, total_charges
    """
    if trades_df.empty:
        return pd.DataFrame()
    
    # Extract charges from individual trade fields if available
    # Otherwise, we need to recompute from segments/sides
    from charges import compute_charges, ChargeSettings
    
    rows: list[dict[str, Any]] = []
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
        
        # Compute charges
        charges = compute_charges(side, qty, price, segment, settings, exchange=exchange)
        
        rows.append({
            "trade_id": trade_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price": round(price, 2),
            "gross": round(gross, 2),
            "brokerage": round(charges.brokerage, 2),
            "stt": round(charges.stt, 2),
            "exchange_sebi": round(charges.exchange_sebi, 2),
            "stamp": round(charges.stamp, 2),
            "gst": round(charges.gst, 2),
            "dp": round(charges.dp, 2),
            "total_charges": round(charges.total, 2),
        })
    
    return pd.DataFrame(rows)


def consolidated_expense_summary(expenses_df: pd.DataFrame) -> dict[str, float]:
    """Compute consolidated expense totals across all trades.
    
    Returns dict with aggregate totals for each charge type.
    """
    if expenses_df.empty:
        return {
            "total_brokerage": 0.0,
            "total_stt": 0.0,
            "total_exchange_sebi": 0.0,
            "total_stamp": 0.0,
            "total_gst": 0.0,
            "total_dp": 0.0,
            "total_all_charges": 0.0,
            "gross_traded_value": 0.0,
            "avg_charges_pct": 0.0,
        }
    
    total_brokerage = float(expenses_df["brokerage"].sum())
    total_stt = float(expenses_df["stt"].sum())
    total_exchange_sebi = float(expenses_df["exchange_sebi"].sum())
    total_stamp = float(expenses_df["stamp"].sum())
    total_gst = float(expenses_df["gst"].sum())
    total_dp = float(expenses_df["dp"].sum())
    total_charges = float(expenses_df["total_charges"].sum())
    gross_value = float(expenses_df["gross"].sum())
    
    avg_pct = (total_charges / gross_value * 100) if gross_value > 0 else 0.0
    
    return {
        "total_brokerage": round(total_brokerage, 2),
        "total_stt": round(total_stt, 2),
        "total_exchange_sebi": round(total_exchange_sebi, 2),
        "total_stamp": round(total_stamp, 2),
        "total_gst": round(total_gst, 2),
        "total_dp": round(total_dp, 2),
        "total_all_charges": round(total_charges, 2),
        "gross_traded_value": round(gross_value, 2),
        "avg_charges_pct": round(avg_pct, 4),
    }


def buy_sell_expense_split(expenses_df: pd.DataFrame) -> dict[str, dict[str, float]]:
    """Split expenses by buy vs sell side.
    
    Returns dict with 'BUY' and 'SELL' keys, each containing charge breakdowns.
    """
    if expenses_df.empty:
        empty = {
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_sebi": 0.0,
            "stamp": 0.0,
            "gst": 0.0,
            "dp": 0.0,
            "total": 0.0,
            "gross_value": 0.0,
        }
        return {"BUY": empty.copy(), "SELL": empty.copy()}
    
    result = {}
    for side in ["BUY", "SELL"]:
        mask = expenses_df["side"] == side
        side_df = expenses_df.loc[mask]
        
        if side_df.empty:
            result[side] = {
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
                "brokerage": round(float(side_df["brokerage"].sum()), 2),
                "stt": round(float(side_df["stt"].sum()), 2),
                "exchange_sebi": round(float(side_df["exchange_sebi"].sum()), 2),
                "stamp": round(float(side_df["stamp"].sum()), 2),
                "gst": round(float(side_df["gst"].sum()), 2),
                "dp": round(float(side_df["dp"].sum()), 2),
                "total": round(float(side_df["total_charges"].sum()), 2),
                "gross_value": round(float(side_df["gross"].sum()), 2),
            }
    
    return result


def total_profit_breakdown(
    realized_pnl: float,
    total_pnl: float,
    expenses_df: pd.DataFrame,
    ltcg_tax: float = 0.0,
) -> dict[str, float]:
    """Comprehensive profit breakdown after all deductions.
    
    Args:
        realized_pnl: Realized P&L from closed trades
        total_pnl: Total portfolio P&L (realized + unrealized)
        expenses_df: DataFrame of all trade expenses
        ltcg_tax: Long-term capital gains tax paid
    
    Returns dict with:
        - gross_pnl (before any deductions)
        - realized_pnl
        - unrealized_pnl
        - total_expenses (all charges)
        - ltcg_tax
        - net_profit_after_charges (realized only)
        - net_profit_after_all (realized + unrealized - taxes)
    """
    expenses = consolidated_expense_summary(expenses_df)
    total_expenses = expenses["total_all_charges"]
    unrealized_pnl = total_pnl - realized_pnl
    
    return {
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "gross_pnl_total": round(total_pnl, 2),
        "total_expenses": round(total_expenses, 2),
        "ltcg_tax": round(ltcg_tax, 2),
        "net_profit_after_charges": round(realized_pnl - total_expenses, 2),
        "net_profit_after_all_taxes": round(total_pnl - total_expenses - ltcg_tax, 2),
    }


def efficiency_metrics(
    trades_df: pd.DataFrame,
    expenses_df: pd.DataFrame,
    realized_pnl: float,
) -> dict[str, float]:
    """Calculate trading efficiency metrics.
    
    - Win rate (% of profitable trades)
    - Profit factor (sum of wins / sum of losses)
    - Avg profit per trade
    - Expense to turnover ratio
    """
    if trades_df.empty or expenses_df.empty:
        return {
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_per_trade": 0.0,
            "expense_to_turnover_pct": 0.0,
            "total_trades": 0,
        }
    
    # Closed trades (realized trades)
    from portfolio import completed_round_trips
    
    closed_df = completed_round_trips(trades_df)
    total_trades = len(closed_df)
    
    if total_trades == 0:
        return {
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "avg_pnl_per_trade": 0.0,
            "expense_to_turnover_pct": 0.0,
            "total_trades": total_trades,
        }
    
    # Win rate
    wins = int((closed_df["pnl_inr"] > 0).sum())
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0
    
    # Profit factor
    winning_trades = closed_df[closed_df["pnl_inr"] > 0]["pnl_inr"].sum()
    losing_trades = abs(closed_df[closed_df["pnl_inr"] < 0]["pnl_inr"].sum())
    profit_factor = (winning_trades / losing_trades) if losing_trades > 0 else 0.0
    
    # Avg P&L per trade
    avg_pnl = realized_pnl / total_trades if total_trades > 0 else 0.0
    
    # Expense to turnover ratio
    total_expenses = expenses_df["total_charges"].sum()
    total_gross = expenses_df["gross"].sum()
    exp_to_turnover = (total_expenses / total_gross * 100) if total_gross > 0 else 0.0
    
    return {
        "win_rate_pct": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2),
        "avg_pnl_per_trade": round(avg_pnl, 2),
        "expense_to_turnover_pct": round(exp_to_turnover, 4),
        "total_trades": total_trades,
    }
