"""Portfolio math from trade history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

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
            buy_dt = _to_datetime(lot["traded_at"])
            sell_dt = _to_datetime(sell_date)
            hold_days = max((sell_dt - buy_dt).days, 0)
            abs_return_pct = return_pct
            cagr_pct = _cagr_from_return(return_pct / 100.0, hold_days)
            cycles.append(
                {
                    "symbol": symbol,
                    "position_id": position_id or "",
                    "qty": match,
                    "buy_avg": round(lot["price"], 2),
                    "sell_avg": round(price, 2),
                    "buy_date": lot["traded_at"],
                    "sell_date": sell_date,
                    "hold_days": hold_days,
                    "pnl_inr": round(pnl_inr, 2),
                    "return_pct": round(return_pct, 2),
                    "abs_return_pct": round(abs_return_pct, 2),
                    "cagr_pct": round(cagr_pct, 2) if cagr_pct is not None else None,
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
        "hold_days",
        "pnl_inr",
        "return_pct",
        "abs_return_pct",
        "cagr_pct",
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


def _to_datetime(value: Any) -> datetime:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    if pd.isna(ts):
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.to_pydatetime()


def _cagr_from_return(total_return: float, hold_days: int) -> Optional[float]:
    """CAGR % from holding-period return and calendar days held."""
    if hold_days <= 0:
        return None
    if total_return <= -1.0:
        return None
    years = hold_days / 365.25
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0
    return cagr * 100.0


def _xirr(cashflows: list[float], dates: list[datetime], guess: float = 0.1) -> Optional[float]:
    """Money-weighted annual return (Excel XIRR-style, 365-day year)."""
    if len(cashflows) < 2 or len(dates) != len(cashflows):
        return None
    if not any(a < 0 for a in cashflows) or not any(a > 0 for a in cashflows):
        return None

    d0 = dates[0]

    def npv(rate: float) -> float:
        if rate <= -1.0:
            return float("inf")
        total = 0.0
        for amount, dt in zip(cashflows, dates):
            years = (dt - d0).days / 365.0
            total += amount / ((1.0 + rate) ** years)
        return total

    rate = guess
    for _ in range(64):
        f = npv(rate)
        if abs(f) < 1e-7:
            return rate * 100.0
        delta = 1e-5
        f1 = npv(rate + delta)
        deriv = (f1 - f) / delta
        if abs(deriv) < 1e-12:
            break
        rate -= f / deriv
        if rate < -0.999:
            rate = -0.999
        if rate > 10.0:
            rate = 10.0
    return None


def portfolio_cashflows(
    trades_df: pd.DataFrame,
    starting_capital: float,
    terminal_value: float,
    as_of: Optional[datetime] = None,
) -> tuple[list[datetime], list[float]]:
    """Cash flows for portfolio XIRR: initial capital, trades, ending equity."""
    as_of = as_of or datetime.now(timezone.utc)
    if trades_df.empty:
        return [as_of, as_of], [-starting_capital, terminal_value]

    chron = trades_df.sort_values(["traded_at", "id"])
    start = _to_datetime(chron.iloc[0]["traded_at"])
    dates = [start]
    amounts = [-float(starting_capital)]

    for _, row in chron.iterrows():
        dates.append(_to_datetime(row["traded_at"]))
        amounts.append(float(row["net_cash"]))

    dates.append(as_of)
    amounts.append(float(terminal_value))
    return dates, amounts


def portfolio_cagr(
    starting_capital: float,
    terminal_value: float,
    start_date: datetime,
    end_date: datetime,
) -> Optional[float]:
    """Portfolio CAGR % from start value, end value, and calendar span."""
    if starting_capital <= 0 or terminal_value <= 0:
        return None
    days = max((end_date - start_date).days, 1)
    years = days / 365.25
    if years <= 0:
        return None
    return ((terminal_value / starting_capital) ** (1.0 / years) - 1.0) * 100.0


def performance_metrics(
    trades_df: pd.DataFrame,
    starting_capital: float,
    terminal_value: float,
    as_of: Optional[datetime] = None,
) -> dict[str, Any]:
    """Absolute return, XIRR, CAGR, and closed-trade holding stats."""
    as_of = as_of or datetime.now(timezone.utc)
    abs_return_pct = (
        ((terminal_value - starting_capital) / starting_capital) * 100.0
        if starting_capital
        else None
    )

    if trades_df.empty:
        return {
            "abs_return_pct": abs_return_pct,
            "xirr_pct": None,
            "cagr_pct": None,
            "portfolio_days": 0,
            "closed_trades": 0,
            "avg_hold_days": None,
            "avg_closed_abs_return_pct": None,
            "avg_closed_cagr_pct": None,
            "closed_df": completed_round_trips(trades_df),
        }

    chron = trades_df.sort_values(["traded_at", "id"])
    start_date = _to_datetime(chron.iloc[0]["traded_at"])
    portfolio_days = max((as_of - start_date).days, 0)

    dates, amounts = portfolio_cashflows(trades_df, starting_capital, terminal_value, as_of)
    xirr_pct = _xirr(amounts, dates)
    cagr_pct = portfolio_cagr(starting_capital, terminal_value, start_date, as_of)

    closed = completed_round_trips(trades_df)
    closed_count = len(closed)
    avg_hold = float(closed["hold_days"].mean()) if closed_count else None
    avg_abs = float(closed["abs_return_pct"].mean()) if closed_count else None
    cagr_series = closed["cagr_pct"].dropna() if closed_count else pd.Series(dtype=float)
    avg_cagr = float(cagr_series.mean()) if not cagr_series.empty else None

    return {
        "abs_return_pct": abs_return_pct,
        "xirr_pct": xirr_pct,
        "cagr_pct": cagr_pct,
        "portfolio_days": portfolio_days,
        "closed_trades": closed_count,
        "avg_hold_days": avg_hold,
        "avg_closed_abs_return_pct": avg_abs,
        "avg_closed_cagr_pct": avg_cagr,
        "closed_df": closed,
    }
