"""Persistence: local SQLite, or Supabase Postgres when SUPABASE_DB_URL is set."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional, Union
from zoneinfo import ZoneInfo

from db import (
    Connection,
    PgConnection,
    open_connection,
    row_to_dict,
    use_supabase,
    _sqlite_path,
)

IST = ZoneInfo("Asia/Kolkata")

_DEFAULT_CHARGES = {
    "brokerage_per_order": 20,
    "gst_on_brokerage": 0.18,
    "stt_delivery_sell": 0.001,
    "stt_intraday_sell": 0.00025,
    "exchange_txn_pct": 0.0000345,
    "sebi_pct": 0.000001,
    "stamp_duty_buy": 0.00015,
    "dp_delivery_sell": 15.93,
}


def _is_pg(conn: Connection) -> bool:
    return isinstance(conn, PgConnection)


def _ensure_sqlite_schema(conn: sqlite3.Connection) -> None:
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
    trade_cols = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
    if "stop_loss" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN stop_loss REAL")
    if "target_price" not in trade_cols:
        conn.execute("ALTER TABLE trades ADD COLUMN target_price REAL")


def _postgres_tables_exist(conn: PgConnection) -> bool:
    row = conn.execute(
        """
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'trades'
        )
        """
    ).fetchone()
    data = row_to_dict(row)
    return bool(data.get("exists"))


def _ensure_postgres_schema(conn: PgConnection) -> None:
    # Schema should be created via schema.sql in Supabase SQL Editor.
    # Skip DDL on pooler if tables already exist (transaction mode dislikes DDL).
    if _postgres_tables_exist(conn):
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id BIGSERIAL PRIMARY KEY,
            traded_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            exchange TEXT NOT NULL DEFAULT 'NSE',
            segment TEXT NOT NULL DEFAULT 'Equity Delivery',
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            price DOUBLE PRECISION NOT NULL,
            position_id TEXT,
            notes TEXT,
            gross DOUBLE PRECISION NOT NULL,
            charges DOUBLE PRECISION NOT NULL,
            net_cash DOUBLE PRECISION NOT NULL,
            created_at TEXT NOT NULL,
            stop_loss DOUBLE PRECISION,
            target_price DOUBLE PRECISION
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_position ON trades (position_id)")


def _seed_defaults(conn: Connection) -> None:
    defaults = {
        "starting_capital": "1000000",
        "charge_settings": json.dumps(_DEFAULT_CHARGES),
    }
    if _is_pg(conn):
        for k, v in defaults.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT (key) DO NOTHING",
                (k, v),
            )
    else:
        for k, v in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (k, v),
            )


def init_db(path: Optional[Path] = None) -> Union[Path, str]:
    """Ensure schema exists. Returns display label for sidebar."""
    if use_supabase():
        with open_connection() as conn:
            _ensure_postgres_schema(conn)
            _seed_defaults(conn)
        return "Supabase (PostgreSQL)"

    db = path or _sqlite_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        _ensure_sqlite_schema(conn)
        _seed_defaults(conn)
        conn.commit()
    finally:
        conn.close()
    return db


@contextmanager
def connect(path: Optional[Path] = None) -> Iterator[Connection]:
    if use_supabase():
        with open_connection() as conn:
            _ensure_postgres_schema(conn)
            _seed_defaults(conn)
            yield conn
    else:
        db = path or _sqlite_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            _ensure_sqlite_schema(conn)
            _seed_defaults(conn)
            yield conn
            conn.commit()
        finally:
            conn.close()


def get_setting(conn: Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return str(row_to_dict(row)["value"])


def set_setting(conn: Connection, key: str, value: str) -> None:
    if _is_pg(conn):
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            (key, value),
        )
    else:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
    conn.commit()


def insert_trade(conn: Connection, trade: dict[str, Any]) -> int:
    stop_loss = trade.get("stop_loss")
    target_price = trade.get("target_price")
    params = (
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
        float(stop_loss) if stop_loss is not None else None,
        float(target_price) if target_price is not None else None,
    )
    if _is_pg(conn):
        cur = conn.execute(
            """
            INSERT INTO trades (
                traded_at, symbol, exchange, segment, side, qty, price,
                position_id, notes, gross, charges, net_cash, created_at,
                stop_loss, target_price
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            params,
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["id"])
    cur = conn.execute(
        """
        INSERT INTO trades (
            traded_at, symbol, exchange, segment, side, qty, price,
            position_id, notes, gross, charges, net_cash, created_at,
            stop_loss, target_price
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        params,
    )
    conn.commit()
    return int(cur.lastrowid)


def list_trades(conn: Connection, limit: int = 500) -> list[Any]:
    cur = conn.execute(
        "SELECT * FROM trades ORDER BY traded_at DESC, id DESC LIMIT ?",
        (limit,),
    )
    return list(cur.fetchall())


def delete_trade(conn: Connection, trade_id: int) -> None:
    conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    conn.commit()


def delete_all_trades(conn: Connection) -> None:
    conn.execute("DELETE FROM trades")
    conn.commit()


def export_all(conn: Connection) -> dict[str, Any]:
    settings = {
        row_to_dict(r)["key"]: row_to_dict(r)["value"]
        for r in conn.execute("SELECT key, value FROM settings").fetchall()
    }
    trades = [
        row_to_dict(r)
        for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    ]
    return {
        "version": 1,
        "exported_at": datetime.now(IST).isoformat(),
        "settings": settings,
        "trades": trades,
    }


def import_backup(conn: Connection, data: dict[str, Any], replace: bool = False) -> int:
    if replace:
        delete_all_trades(conn)
    for k, v in data.get("settings", {}).items():
        set_setting(conn, k, str(v) if not isinstance(v, str) else v)
    count = 0
    for t in data.get("trades", []):
        row = {k: v for k, v in t.items() if k != "id"}
        insert_trade(conn, row)
        count += 1
    return count


def storage_label() -> str:
    if use_supabase():
        return "Supabase"
    return _sqlite_path().name
