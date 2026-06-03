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


def _pooler_url(project_ref: str, password: str, region: str) -> str:
    from urllib.parse import quote

    user = f"postgres.{project_ref}"
    host = f"aws-0-{region}.pooler.supabase.com"
    pass_q = quote(password, safe="")
    user_q = quote(user, safe="")
    return (
        f"postgresql://{user_q}:{pass_q}@{host}:6543/postgres?sslmode=require"
    )


def _build_url_from_parts() -> Optional[str]:
    """Split secrets — best for Streamlit Cloud and special characters in password."""
    password = _read_secret("SUPABASE_PASSWORD")
    if _invalid_password(password):
        return None

    host = _read_secret("SUPABASE_HOST")
    region = _read_secret("SUPABASE_REGION")
    project_ref = _read_secret("SUPABASE_PROJECT_REF")

    if not host and project_ref and region:
        return _pooler_url(project_ref, password, region)

    if not host:
        return None

    from urllib.parse import quote

    user = _read_secret("SUPABASE_USER")
    if not user and project_ref:
        user = f"postgres.{project_ref}"
    user = user or "postgres"
    port = _read_secret("SUPABASE_PORT") or "6543"
    dbname = _read_secret("SUPABASE_DB") or "postgres"
    user_q = quote(user, safe="")
    pass_q = quote(password, safe="")
    return (
        f"postgresql://{user_q}:{pass_q}@{host}:{port}/{dbname}?sslmode=require"
    )


def normalize_database_url(raw: str) -> str:
    """Validate and fix common Supabase URI mistakes from Streamlit secrets."""
    url = _clean_secret(raw)
    if not url:
        raise ValueError("SUPABASE_DB_URL is empty.")

    if "[YOUR-PASSWORD]" in url or "YOUR-PASSWORD" in url.upper():
        raise ValueError(
            "SUPABASE_DB_URL still contains [YOUR-PASSWORD]. "
            "Replace it with your real database password from "
            "Supabase → Project Settings → Database → Database password."
        )

    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError(
            "SUPABASE_DB_URL must start with postgresql:// "
            "(use Transaction pooler URI, port 6543 — not the template from the docs)."
        )

    from urllib.parse import quote, unquote, urlparse, urlunparse

    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(
            "SUPABASE_DB_URL looks invalid (no host). "
            "Use split secrets: SUPABASE_PROJECT_REF, SUPABASE_REGION, SUPABASE_PASSWORD."
        )

    raw_password = unquote(parsed.password or "")
    if _invalid_password(raw_password):
        raise ValueError(
            "Database password is missing or still a placeholder. "
            "Set your real password in SUPABASE_DB_URL or SUPABASE_PASSWORD."
        )

    # Direct URL (db.xxx.supabase.co:5432) often fails on Streamlit Cloud → use pooler.
    project_ref = _project_ref_from_host(parsed.hostname)
    port = parsed.port or 5432
    if project_ref and port == 5432 and "pooler.supabase.com" not in (parsed.hostname or ""):
        region = _read_secret("SUPABASE_REGION") or "ap-southeast-1"
        return _pooler_url(project_ref, raw_password, region)

    if parsed.username:
        user = quote(unquote(parsed.username), safe="")
        password = quote(raw_password, safe="")
        host = parsed.hostname
        netloc = f"{user}:{password}@{host}"
        if parsed.port:
            netloc += f":{parsed.port}"
        query = parsed.query or ""
        if "sslmode" not in query:
            query = f"{query}&sslmode=require".lstrip("&") if query else "sslmode=require"
        url = urlunparse(
            (parsed.scheme, netloc, parsed.path or "/postgres", parsed.params, query, "")
        )
    elif "sslmode=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}sslmode=require"

    return url


def database_url() -> Optional[str]:
    """Postgres URI from Streamlit secrets or env (Supabase → Settings → Database)."""
    for key in ("SUPABASE_DB_URL", "DATABASE_URL", "POSTGRES_URL"):
        url = _read_secret(key)
        if url:
            try:
                return normalize_database_url(url)
            except ValueError:
                raise
            except Exception as exc:
                raise ValueError(
                    f"Could not parse {key}. Check for special characters in the password "
                    "(use SUPABASE_PASSWORD + SUPABASE_HOST instead). Details: {exc}"
                ) from exc
    built = _build_url_from_parts()
    if built:
        return built
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
        from psycopg import ProgrammingError
        from psycopg.rows import dict_row

        try:
            conn = psycopg.connect(
                url,
                row_factory=dict_row,
                autocommit=False,
                connect_timeout=15,
            )
        except ProgrammingError as exc:
            raise ValueError(
                "Invalid Supabase connection string. Use Transaction pooler (port 6543) "
                "or split secrets: SUPABASE_PROJECT_REF, SUPABASE_REGION, SUPABASE_PASSWORD."
            ) from exc
        except Exception as exc:
            name = type(exc).__name__
            hint = (
                "Wrong database password, or direct db.*.supabase.co:5432 URL (use pooler 6543). "
                "Easiest fix — set these in Streamlit secrets (no URL):\n"
                "SUPABASE_PROJECT_REF = \"jrtqpdjrsxnmsdxguqlg\"\n"
                "SUPABASE_REGION = \"ap-southeast-1\"\n"
                "SUPABASE_PASSWORD = \"your-database-password\""
            )
            raise ValueError(f"Supabase connection failed ({name}). {hint}") from exc
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
