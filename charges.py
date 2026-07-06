"""Indian equity charges for paper trading (Zerodha brokerage calculator rates)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChargeSettings:
    """Configurable overrides; Zerodha statutory rates are fixed in compute_charges."""

    dp_delivery_sell: float = 15.93


@dataclass
class TradeCharges:
    gross: float
    brokerage: float
    stt: float
    exchange_sebi: float
    stamp: float
    gst: float
    dp: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange_sebi + self.stamp + self.gst + self.dp


def _round2(value: float) -> float:
    return round(float(value), 2)


def _round0(value: float) -> float:
    return round(float(value))


def _zerodha_brokerage_leg(turnover: float) -> float:
    """Intraday/F&O: lower of ₹20 or 0.03% per executed order (Zerodha tariff)."""
    return min(20.0, _round2(turnover * 0.0003))


def _exchange_turnover_charge(turnover: float, exchange: str) -> float:
    """NSE equity txn charge + IPFT; BSE uses a higher txn rate (zerodha.com/static/js/brokerage.js)."""
    ex = exchange.upper()
    if ex == "BSE":
        return _round2(turnover * 0.0000375)
    return _round2(turnover * (0.0000297 + 0.000001))


def compute_charges(
    side: str,
    qty: int,
    price: float,
    segment: str,
    settings: ChargeSettings | None = None,
    exchange: str = "NSE",
) -> TradeCharges:
    """Per-order charges using Zerodha's public brokerage calculator formulas."""
    settings = settings or ChargeSettings()
    is_sell = side.upper() == "SELL"
    is_intraday = segment == "Equity Intraday"
    gross = float(qty) * float(price)
    turnover = gross

    if is_intraday:
        brokerage = _zerodha_brokerage_leg(turnover)
        stt = _round0(gross * 0.00025) if is_sell else 0.0
        stamp = _round0(gross * 0.00003) if not is_sell else 0.0
        dp = 0.0
    else:
        brokerage = 0.0
        stt = _round0(turnover * 0.001)
        stamp = _round0(gross * 0.00015) if not is_sell else 0.0
        dp = settings.dp_delivery_sell if is_sell else 0.0

    exchange_charge = _exchange_turnover_charge(turnover, exchange)
    sebi = _round2(turnover * 0.000001)
    exchange_sebi = _round2(exchange_charge + sebi)
    gst = _round2(0.18 * (brokerage + exchange_charge + sebi))

    return TradeCharges(
        gross=gross,
        brokerage=brokerage,
        stt=stt,
        exchange_sebi=exchange_sebi,
        stamp=stamp,
        gst=gst,
        dp=dp,
    )


def net_cash_flow(side: str, charges: TradeCharges) -> float:
    """Cash impact: negative for BUY, positive for SELL."""
    if side.upper() == "BUY":
        return -(charges.gross + charges.total)
    return charges.gross - charges.total
