"""Database backend: local SQLite or Supabase Postgres."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

Connection = Union[sqlite3.Connection, "PgConnection"]

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DB = APP_DIR / "paper_trades.db"


def _load_local_secrets() -> None:
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
        return _clean_secret(val)
    try:
        import streamlit as st

        raw = st.secrets.get(name, "") or ""
        if isinstance(raw, str) and raw.strip():
            return _clean_secret(raw)
    except Exception:
        pass
    return ""


def _clean_secret(val: str) -> str:
    s = val.strip().strip("\n\r")
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        s = s[1:-1].strip()
    return s


def _invalid_password(password: str) -> bool:
    if not password:
        return True
    p = password.strip().upper()
    return p in {
        "[YOUR-PASSWORD]",
        "YOUR-PASSWORD",
        "[PASSWORD]",
        "PASSWORD",
        "YOUR_PASSWORD",
    } or "YOUR-PASSWORD" in p


def _project_ref_from_host(host: str) -> Optional[str]:
    m = re.match(r"^db\.([a-z0-9]+)\.supabase\.co$", host or "")
    return m.group(1) if m else None


def _postgres_password() -> str:
    from urllib.parse import unquote, urlparse

    pw = _read_secret("SUPABASE_PASSWORD")
    if pw and not _invalid_password(pw):
        return pw
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL"):
        url = _clean_secret(_read_secret(key))
        if not url or "[YOUR-PASSWORD]" in url:
            continue
        parsed = urlparse(url)
        raw = unquote(parsed.password or "")
        if raw and not _invalid_password(raw):
            return raw
    return ""


def _project_ref() -> str:
    ref = _read_secret("SUPABASE_PROJECT_REF")
    if ref:
        return ref
    for key in ("SUPABASE_DB_URL", "DATABASE_URL"):
        url = _read_secret(key)
        if url:
            from urllib.parse import urlparse

            parsed = urlparse(_clean_secret(url))
            if parsed.username and parsed.username.startswith("postgres."):
                return parsed.username.split(".", 1)[1]
            if parsed.hostname:
                found = _project_ref_from_host(parsed.hostname)
                if found:
                    return found
    return ""


def _region() -> str:
    return _read_secret("SUPABASE_REGION") or "ap-southeast-1"


def postgres_connect_candidates() -> list[dict[str, Any]]:
    """Connection attempts ordered for Streamlit Cloud (session pooler first)."""
    password = _postgres_password()
    if _invalid_password(password):
        return []

    ref = _project_ref()
    region = _region()
    host_override = _read_secret("SUPABASE_HOST")
    port_override = _read_secret("SUPABASE_PORT")
    user_override = _read_secret("SUPABASE_USER")
    dbname = _read_secret("SUPABASE_DB") or "postgres"

    out: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    def add(**kwargs: Any) -> None:
        key = (kwargs.get("host"), kwargs.get("port"), kwargs.get("user"))
        if key in seen:
            return
        seen.add(key)
        out.append(kwargs)

    if host_override:
        add(
            host=host_override,
            port=int(port_override or 5432),
            user=user_override or (f"postgres.{ref}" if ref else "postgres"),
            password=password,
            dbname=dbname,
        )

    if ref and region:
        for prefix in ("aws-0", "aws-1"):
            pooler = f"{prefix}-{region}.pooler.supabase.com"
            add(
                host=pooler,
                port=5432,
                user=f"postgres.{ref}",
                password=password,
                dbname=dbname,
                label=f"Session pooler {pooler}:5432",
            )
            add(
                host=pooler,
                port=6543,
                user=f"postgres.{ref}",
                password=password,
                dbname=dbname,
                label=f"Transaction pooler {pooler}:6543",
            )

    if ref:
        add(
            host=f"db.{ref}.supabase.co",
            port=5432,
            user="postgres",
            password=password,
            dbname=dbname,
            label="Direct (may not work on Streamlit Cloud)",
        )

    return out


def use_supabase() -> bool:
    if _postgres_password() and not _invalid_password(_postgres_password()):
        return True
    if _read_secret("SUPABASE_HOST") and _postgres_password():
        return True
    if _project_ref() and _postgres_password():
        return True
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL"):
        if _read_secret(key):
            return True
    return False


def database_url() -> Optional[str]:
    """Legacy helper — prefer postgres_connect_candidates."""
    cands = postgres_connect_candidates()
    if not cands:
        return None
    c = cands[0]
    from urllib.parse import quote

    user = quote(str(c["user"]), safe="")
    pw = quote(str(c["password"]), safe="")
    host = c["host"]
    port = c["port"]
    db = c.get("dbname", "postgres")
    return f"postgresql://{user}:{pw}@{host}:{port}/{db}?sslmode=require"


def _connect_postgres() -> tuple[Any, str]:
    """Try candidates; return (connection, label)."""
    import psycopg
    from psycopg.rows import dict_row

    cands = postgres_connect_candidates()
    if not cands:
        raise ValueError(
            "Supabase is not configured. In Streamlit secrets set:\n"
            "SUPABASE_PROJECT_REF = jrtqpdjrsxnmsdxguqlg\n"
            "SUPABASE_REGION = ap-southeast-1\n"
            "SUPABASE_PASSWORD = (Database password from Supabase settings)\n\n"
            "Run schema.sql in Supabase SQL Editor first."
        )

    errors: list[str] = []
    for cand in cands:
        label = cand.pop("label", f"{cand['host']}:{cand['port']}")
        try:
            conn = psycopg.connect(
                host=cand["host"],
                port=cand["port"],
                user=cand["user"],
                password=cand["password"],
                dbname=cand.get("dbname", "postgres"),
                sslmode="require",
                connect_timeout=15,
                row_factory=dict_row,
                autocommit=False,
            )
            return conn, label
        except Exception as exc:
            errors.append(f"{label}: {type(exc).__name__}")

    raise ValueError(
        "Could not reach Supabase. Tried:\n- "
        + "\n- ".join(errors)
        + "\n\nCheck SUPABASE_PASSWORD (reset in Supabase → Database). "
        "Copy the exact Session pooler host from Supabase → Connect → ORMs → "
        "set SUPABASE_HOST + SUPABASE_USER + SUPABASE_PASSWORD + SUPABASE_PORT=5432. "
        "Confirm schema.sql was run in SQL Editor."
    )


def _sqlite_path() -> Path:
    custom = os.environ.get("PAPER_DB_PATH")
    if custom:
        return Path(custom)
    return DEFAULT_DB


class PgConnection:
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
    if use_supabase():
        conn, _label = _connect_postgres()
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
