"""Approximate Indian equity charges for paper trading."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChargeSettings:
    brokerage_per_order: float = 20.0
    gst_on_brokerage: float = 0.18
    stt_delivery_sell: float = 0.001
    stt_intraday_sell: float = 0.00025
    exchange_txn_pct: float = 0.0000345
    sebi_pct: float = 0.000001
    stamp_duty_buy: float = 0.00015
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


def compute_charges(
    side: str,
    qty: int,
    price: float,
    segment: str,
    settings: ChargeSettings,
) -> TradeCharges:
    gross = float(qty) * float(price)
    brokerage = settings.brokerage_per_order
    is_sell = side.upper() == "SELL"
    is_intraday = segment == "Equity Intraday"

    stt = 0.0
    if is_sell:
        rate = settings.stt_intraday_sell if is_intraday else settings.stt_delivery_sell
        stt = gross * rate

    exchange_sebi = gross * (settings.exchange_txn_pct + settings.sebi_pct)
    stamp = gross * settings.stamp_duty_buy if not is_sell else 0.0
    gst = brokerage * settings.gst_on_brokerage
    dp = settings.dp_delivery_sell if (is_sell and not is_intraday) else 0.0

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
