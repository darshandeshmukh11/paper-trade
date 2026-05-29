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


def _round_trip_pnl(grp: pd.DataFrame) -> float:
    """Net P&L for a matched buy/sell group (by position_id)."""
    buys = grp[grp["side"].str.upper() == "BUY"]
    sells = grp[grp["side"].str.upper() == "SELL"]
    if buys.empty or sells.empty:
        return 0.0
    bqty = int(buys["qty"].sum())
    sqty = int(sells["qty"].sum())
    qty = min(bqty, sqty)
    if qty <= 0:
        return 0.0
    buy_avg = float((buys["qty"] * buys["price"]).sum() / bqty)
    sell_avg = float((sells["qty"] * sells["price"]).sum() / sqty)
    charges = float(grp["charges"].sum())
    return (sell_avg - buy_avg) * qty - charges


def _fifo_realized_pnl(trades_df: pd.DataFrame) -> float:
    """Match sells to buys in chronological order per symbol (no position_id)."""
    if trades_df.empty:
        return 0.0

    total = 0.0
    chron = trades_df.sort_values(["traded_at", "id"])
    for symbol, sym_trades in chron.groupby(chron["symbol"].str.upper()):
        lots: list[dict[str, float]] = []
        for _, row in sym_trades.iterrows():
            side = str(row["side"]).upper()
            qty = int(row["qty"])
            price = float(row["price"])
            charges = float(row.get("charges", 0.0) or 0.0)
            if side == "BUY":
                lots.append({"qty": qty, "price": price, "charges": charges})
                continue
            if side != "SELL":
                continue

            remaining = qty
            sell_charges = charges
            while remaining > 0 and lots:
                lot = lots[0]
                match = min(remaining, int(lot["qty"]))
                if match <= 0:
                    break
                buy_charge_alloc = lot["charges"] * (match / lot["qty"]) if lot["qty"] else 0.0
                sell_charge_alloc = sell_charges * (match / qty) if qty else 0.0
                total += (price - lot["price"]) * match - buy_charge_alloc - sell_charge_alloc
                lot["qty"] -= match
                lot["charges"] -= buy_charge_alloc
                remaining -= match
                if lot["qty"] <= 0:
                    lots.pop(0)
    return total


def realized_pnl(trades_df: pd.DataFrame) -> float:
    """Realized P&L from closed round-trips.

    Trades with a non-empty ``position_id`` are grouped explicitly; all other
    trades are matched FIFO per symbol (buy then sell chronologically).
    """
    if trades_df.empty:
        return 0.0

    total = 0.0
    fifo_df = trades_df
    if "position_id" in trades_df.columns:
        pid_series = trades_df["position_id"].fillna("").astype(str).str.strip()
        with_pid = trades_df[pid_series != ""]
        fifo_df = trades_df[pid_series == ""]
        for _, grp in with_pid.groupby("position_id"):
            total += _round_trip_pnl(grp)

    total += _fifo_realized_pnl(fifo_df)
    return total
