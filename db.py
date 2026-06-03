"""Database backend: local SQLite or Supabase Postgres."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

Connection = Union[sqlite3.Connection, "PgConnection"]

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = APP_DIR / "paper_trades.db"


def _load_local_secrets() -> None:
    """Load secrets.toml from app folder (flat layout, no .streamlit/)."""
    path = APP_DIR / "secrets.toml"
    if not path.is_file():
        return
    try:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore
        with open(path, "rb") as f:
            data = tomllib.load(f)
        for key, val in data.items():
            if isinstance(val, str) and not os.environ.get(key):
                os.environ[key] = val
    except Exception:
        pass


_load_local_secrets()


def _read_secret(name: str) -> str:
    val = os.environ.get(name, "") or ""
    if val:
        return val.strip()
    try:
        import streamlit as st

        return (st.secrets.get(name, "") or "").strip()
    except Exception:
        return ""


def database_url() -> Optional[str]:
    """Postgres URI from Streamlit secrets or env (Supabase → Settings → Database)."""
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL"):
        url = _read_secret(key)
        if url:
            return url
    return None


def use_supabase() -> bool:
    return database_url() is not None


def _sqlite_path() -> Path:
    custom = os.environ.get("PAPER_DB_PATH")
    if custom:
        return Path(custom)
    return DEFAULT_DB


class PgConnection:
    """sqlite3-like wrapper around psycopg for store.py."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> Any:
        sql = sql.replace("?", "%s")
        return self._conn.execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


@contextmanager
def open_connection() -> Iterator[Connection]:
    url = database_url()
    if url:
        import psycopg
        from psycopg.rows import dict_row

        conn = psycopg.connect(url, row_factory=dict_row, autocommit=False)
        wrapper = PgConnection(conn)
        try:
            yield wrapper
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    else:
        db = _sqlite_path()
        db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, dict):
        return row
    return dict(row)
