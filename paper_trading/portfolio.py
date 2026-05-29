"""Portfolio math from trade history."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Optional

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


def turnover_total(trades_df: pd.DataFrame) -> float:
    """Total turnover (sum of absolute gross) for all trades.

    trades_df is expected to have a numeric `gross` column (qty * price).
    """
    if trades_df is None or trades_df.empty:
        return 0.0
    return float(trades_df["gross"].abs().sum())


def monthly_performance(
    trades_df: pd.DataFrame,
    starting_capital: float,
    ltp_lookup: Optional[Callable[[str], Optional[float]]] = None,
) -> pd.DataFrame:
    """Compute monthly performance summary.

    Returns a DataFrame indexed by YYYY-MM string with columns:
      - start_equity
      - end_equity
      - monthly_return_pct
      - monthly_turnover_value
      - monthly_turnover_pct
      - trades_count

    If ltp_lookup is provided, it will be called as ltp_lookup(symbol) to obtain
    a market price for open positions at each month end; otherwise position
    valuation falls back to cost_basis (conservative proxy).
    """
    if trades_df is None or trades_df.empty:
        return pd.DataFrame(
            columns=[
                "start_equity",
                "end_equity",
                "monthly_return_pct",
                "monthly_turnover_value",
                "monthly_turnover_pct",
                "trades_count",
            ]
        )

    df = trades_df.copy()
    # ensure datetime for traded_at
    df["traded_at"] = pd.to_datetime(df["traded_at"]) if df["traded_at"].dtype == object else df["traded_at"]
    df = df.sort_values("traded_at")

    first = df["traded_at"].iloc[0].to_period("M").to_timestamp("M")
    last = df["traded_at"].iloc[-1].to_period("M").to_timestamp("M")
    months = pd.date_range(start=first, end=last, freq="M")

    rows: List[dict[str, Any]] = []
    prev_equity = starting_capital
    cumulative_net_cash = 0.0

    for m in months:
        # trades up to month end (inclusive)
        up_to = df[df["traded_at"] <= m]
        # cash at month end
        cumulative_net_cash = float(up_to["net_cash"].sum())
        cash = starting_capital + cumulative_net_cash
        # compute open positions as of month end
        positions_df = up_to.copy()
        positions = compute_positions(positions_df) if not positions_df.empty else []
        holdings_value = 0.0
        for p in positions:
            if ltp_lookup:
                price = ltp_lookup(p.symbol) or p.avg_cost
            else:
                price = p.avg_cost
            holdings_value += p.qty * price
        end_equity = cash + holdings_value

        # monthly turnover = sum abs(gross) for trades that happened within this month
        month_start = (m - pd.offsets.MonthEnd(1)).replace(day=1) if False else m.to_period("M").to_timestamp("M").replace(day=1)
        # simpler: select trades with period equals m's period
        trades_in_month = df[df["traded_at"].dt.to_period("M") == m.to_period("M")]
        monthly_turnover_value = float(trades_in_month["gross"].abs().sum())
        monthly_turnover_pct = (monthly_turnover_value / float(starting_capital)) * 100.0 if starting_capital else 0.0
        trades_count = int(len(trades_in_month))

        start_equity = prev_equity
        monthly_return_pct = (end_equity / start_equity - 1.0) * 100.0 if start_equity else 0.0

        rows.append(
            {
                "month": m.to_period("M").strftime("%Y-%m"),
                "start_equity": round(start_equity, 2),
                "end_equity": round(end_equity, 2),
                "monthly_return_pct": round(monthly_return_pct, 4),
                "monthly_turnover_value": round(monthly_turnover_value, 2),
                "monthly_turnover_pct": round(monthly_turnover_pct, 4),
                "trades_count": trades_count,
            }
        )
        prev_equity = end_equity

    out = pd.DataFrame(rows).set_index("month")
    return out
