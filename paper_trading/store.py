"""SQLite persistence for paper trades."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "paper_trades.db"


def _db_path() -> Path:
    import os

    custom = os.environ.get("PAPER_DB_PATH")
    if custom:
        return Path(custom)
    return DEFAULT_DB


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            traded_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'NSE',
            segment TEXT NOT NULL DEFAULT 'Equity Delivery',
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price REAL NOT NULL,
            position_id TEXT,
            notes TEXT,
            gross REAL NOT NULL,
            charges REAL NOT NULL,
            net_cash REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
        CREATE INDEX IF NOT EXISTS idx_trades_position ON trades(position_id);
        """
    )
    defaults = {
        "starting_capital": "500000",
        "charge_settings": json.dumps(
            {
                "brokerage_per_order": 20,
                "gst_on_brokerage": 0.18,
                "stt_delivery_sell": 0.001,
                "stt_intraday_sell": 0.00025,
                "exchange_txn_pct": 0.0000345,
                "sebi_pct": 0.000001,
                "stamp_duty_buy": 0.00015,
                "dp_delivery_sell": 15.93,
            }
        ),
    }
    for k, v in defaults.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
            (k, v),
        )


def init_db(path: Optional[Path] = None) -> Path:
    db = path or _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        _ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return db


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    db = init_db(path)
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def insert_trade(conn: sqlite3.Connection, trade: dict[str, Any]) -> int:
    cur = conn.execute(
        """
        INSERT INTO trades (
            traded_at, symbol, exchange, segment, side, qty, price,
            position_id, notes, gross, charges, net_cash, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            trade["traded_at"],
            trade["symbol"].upper().strip(),
            trade.get("exchange", "NSE"),
            trade.get("segment", "Equity Delivery"),
            trade["side"].upper(),
            int(trade["qty"]),
            float(trade["price"]),
            trade.get("position_id") or None,
            trade.get("notes") or "",
            float(trade["gross"]),
            float(trade["charges"]),
            float(trade["net_cash"]),
            trade.get("created_at") or datetime.now(IST).isoformat(),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_trades(conn: sqlite3.Connection, limit: int = 500) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM trades ORDER BY traded_at DESC, id DESC LIMIT ?",
            (limit,),
        )
    )


def delete_trade(conn: sqlite3.Connection, trade_id: int) -> None:
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()


def export_all(conn: sqlite3.Connection) -> dict[str, Any]:
    settings = {
        r["key"]: r["value"]
        for r in conn.execute("SELECT key, value FROM settings")
    }
    trades = [dict(r) for r in conn.execute("SELECT * FROM trades ORDER BY id")]
    return {"version": 1, "exported_at": datetime.now(IST).isoformat(), "settings": settings, "trades": trades}


def import_backup(conn: sqlite3.Connection, data: dict[str, Any], replace: bool = False) -> int:
    if replace:
        conn.execute("DELETE FROM trades")
    for k, v in data.get("settings", {}).items():
        set_setting(conn, k, str(v) if not isinstance(v, str) else v)
    count = 0
    for t in data.get("trades", []):
        row = {k: v for k, v in t.items() if k != "id"}
        insert_trade(conn, row)
        count += 1
    conn.commit()
    return count
