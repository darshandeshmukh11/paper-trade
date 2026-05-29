"""Portfolio math from trade history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class Position:
    symbol: str
    exchange: str
    segment: str
    qty: int
    avg_cost: float
    cost_basis: float


def trades_to_df(trades: list[Any]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    return pd.DataFrame([dict(r) for r in trades])


def compute_positions(trades_df: pd.DataFrame) -> list[Position]:
    if trades_df.empty:
        return []
    positions: dict[str, dict[str, Any]] = {}
    chron = trades_df.sort_values(["traded_at", "id"])
    for _, row in chron.iterrows():
        sym = str(row["symbol"]).upper()
        side = str(row["side"]).upper()
        qty = int(row["qty"])
        price = float(row["price"])
        if sym not in positions:
            positions[sym] = {
                "symbol": sym,
                "exchange": row.get("exchange", "NSE"),
                "segment": row.get("segment", "Equity Delivery"),
                "qty": 0,
                "cost_basis": 0.0,
            }
        p = positions[sym]
        if side == "BUY":
            p["cost_basis"] += qty * price
            p["qty"] += qty
        elif side == "SELL":
            if qty > p["qty"]:
                raise ValueError(f"Cannot sell {qty} of {sym}: only {p['qty']} held")
            if p["qty"] > 0:
                avg = p["cost_basis"] / p["qty"]
                p["cost_basis"] -= avg * qty
            p["qty"] -= qty
    out: list[Position] = []
    for p in positions.values():
        if p["qty"] <= 0:
            continue
        avg_cost = p["cost_basis"] / p["qty"] if p["qty"] else 0.0
        out.append(
            Position(
                symbol=p["symbol"],
                exchange=p["exchange"],
                segment=p["segment"],
                qty=int(p["qty"]),
                avg_cost=avg_cost,
                cost_basis=p["cost_basis"],
            )
        )
    return sorted(out, key=lambda x: x.symbol)


def cash_balance(starting_capital: float, trades_df: pd.DataFrame) -> float:
    if trades_df.empty:
        return starting_capital
    return starting_capital + float(trades_df["net_cash"].sum())


def realized_pnl(trades_df: pd.DataFrame) -> float:
    """Sum of sell gross minus matched cost — simplified via round-trips by position_id."""
    if trades_df.empty or "position_id" not in trades_df.columns:
        return 0.0
    total = 0.0
    for pid, grp in trades_df.dropna(subset=["position_id"]).groupby("position_id"):
        if not str(pid).strip():
            continue
        buys = grp[grp["side"].str.upper() == "BUY"]
        sells = grp[grp["side"].str.upper() == "SELL"]
        if buys.empty or sells.empty:
            continue
        bqty = int(buys["qty"].sum())
        sqty = int(sells["qty"].sum())
        qty = min(bqty, sqty)
        if qty <= 0:
            continue
        buy_avg = float((buys["qty"] * buys["price"]).sum() / bqty)
        sell_avg = float((sells["qty"] * sells["price"]).sum() / sqty)
        charges = float(grp["charges"].sum())
        total += (sell_avg - buy_avg) * qty - charges
    return total
