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


def _fifo_completed_cycles(sym_trades: pd.DataFrame, position_id: str | None) -> list[dict[str, Any]]:
    """Each sell matched to prior buys (FIFO) yields one completed round-trip row."""
    if sym_trades.empty:
        return []

    symbol = str(sym_trades["symbol"].iloc[0]).upper()
    lots: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []
    chron = sym_trades.sort_values(["traded_at", "id"])

    for _, row in chron.iterrows():
        side = str(row["side"]).upper()
        qty = int(row["qty"])
        price = float(row["price"])
        charges = float(row.get("charges", 0.0) or 0.0)
        traded_at = row["traded_at"]

        if side == "BUY":
            lots.append(
                {
                    "qty": qty,
                    "price": price,
                    "charges": charges,
                    "traded_at": traded_at,
                }
            )
            continue
        if side != "SELL":
            continue

        remaining = qty
        sell_charges = charges
        sell_date = traded_at
        while remaining > 0 and lots:
            lot = lots[0]
            match = min(remaining, int(lot["qty"]))
            if match <= 0:
                break
            buy_charge_alloc = lot["charges"] * (match / lot["qty"]) if lot["qty"] else 0.0
            sell_charge_alloc = sell_charges * (match / qty) if qty else 0.0
            pnl_inr = (price - lot["price"]) * match - buy_charge_alloc - sell_charge_alloc
            cost_basis = lot["price"] * match
            return_pct = (pnl_inr / cost_basis * 100.0) if cost_basis else 0.0
            cycles.append(
                {
                    "symbol": symbol,
                    "position_id": position_id or "",
                    "qty": match,
                    "buy_avg": round(lot["price"], 2),
                    "sell_avg": round(price, 2),
                    "buy_date": lot["traded_at"],
                    "sell_date": sell_date,
                    "pnl_inr": round(pnl_inr, 2),
                    "return_pct": round(return_pct, 2),
                    "charges": round(buy_charge_alloc + sell_charge_alloc, 2),
                }
            )
            lot["qty"] -= match
            lot["charges"] -= buy_charge_alloc
            remaining -= match
            if lot["qty"] <= 0:
                lots.pop(0)
    return cycles


def completed_round_trips(trades_df: pd.DataFrame) -> pd.DataFrame:
    """Closed buy→sell cycles with realized P&L and return % (FIFO; position_id groups isolated)."""
    columns = [
        "symbol",
        "position_id",
        "qty",
        "buy_avg",
        "sell_avg",
        "buy_date",
        "sell_date",
        "pnl_inr",
        "return_pct",
        "charges",
    ]
    if trades_df.empty:
        return pd.DataFrame(columns=columns)

    rows: list[dict[str, Any]] = []
    pid_series = (
        trades_df["position_id"].fillna("").astype(str).str.strip()
        if "position_id" in trades_df.columns
        else pd.Series([""] * len(trades_df))
    )
    with_pid = trades_df[pid_series != ""]
    without_pid = trades_df[pid_series == ""]

    for pid, grp in with_pid.groupby("position_id"):
        rows.extend(_fifo_completed_cycles(grp, str(pid)))

    if not without_pid.empty:
        chron = without_pid.sort_values(["traded_at", "id"])
        for _, grp in chron.groupby(chron["symbol"].str.upper()):
            rows.extend(_fifo_completed_cycles(grp, None))

    if not rows:
        return pd.DataFrame(columns=columns)

    out = pd.DataFrame(rows)
    return out.sort_values(["sell_date", "symbol"], ascending=[False, True]).reset_index(drop=True)


def _fifo_realized_pnl(trades_df: pd.DataFrame) -> float:
    """Match sells to buys in chronological order per symbol (no position_id)."""
    if trades_df.empty:
        return 0.0

    total = 0.0
    chron = trades_df.sort_values(["traded_at", "id"])
    for _, sym_trades in chron.groupby(chron["symbol"].str.upper()):
        for cycle in _fifo_completed_cycles(sym_trades, None):
            total += float(cycle["pnl_inr"])
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
        for pid, grp in with_pid.groupby("position_id"):
            for cycle in _fifo_completed_cycles(grp, str(pid)):
                total += float(cycle["pnl_inr"])

    total += _fifo_realized_pnl(fifo_df)
    return total
