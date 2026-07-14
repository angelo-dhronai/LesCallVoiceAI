"""Dialect-thin database access for the multi-tenant layer.

Dev/prod default is SQLite (WAL) in this directory; production can move to
Postgres by setting LESCALL_DB_URL=postgres://... — same schema.sql (via the
{PK} placeholder), same qmark ("?") placeholders in callers, translated here.

Usage:
    from shared.db.database import get_db
    with get_db() as db:
        row = db.execute("SELECT * FROM agents WHERE slug=?", (slug,)).fetchone()

get_db() commits on clean exit, rolls back on exception, and ensures the schema
exists (idempotent, once per process). Callers in the live call path must wrap
their own try/except — this module raises normally; the never-crash policy
belongs to the caller (see resolve_agent_slug).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
SCHEMA_PATH = _HERE / "schema.sql"
DEFAULT_SQLITE_PATH = _HERE / "lescall.db"

_PK_SQLITE = "INTEGER PRIMARY KEY AUTOINCREMENT"
_PK_POSTGRES = "BIGSERIAL PRIMARY KEY"

# Idempotent column additions for schema evolution: existing tables don't get
# new columns from CREATE IF NOT EXISTS, so append ALTERs here (never edit old
# ones). Errors for already-present columns are swallowed.
MIGRATIONS: list[str] = [
    "ALTER TABLE phone_numbers ADD COLUMN verified INTEGER DEFAULT 0",
    # Settings-page profile fields; phone is the destination for BYON test calls.
    "ALTER TABLE users ADD COLUMN phone TEXT",
    "ALTER TABLE users ADD COLUMN company TEXT",
    # Call-recording delivery (GCS sync + public tokenized download links).
    # The unique index lives here, not schema.sql: on pre-existing DBs the
    # schema script runs before these ALTERs, when the column doesn't exist.
    "ALTER TABLE calls ADD COLUMN download_token TEXT",
    "ALTER TABLE calls ADD COLUMN recording_url TEXT",
    "ALTER TABLE calls ADD COLUMN gcs_object TEXT",
    "ALTER TABLE calls ADD COLUMN gcs_uploaded_at TIMESTAMP",
    "ALTER TABLE calls ADD COLUMN follow_up TEXT",
    "ALTER TABLE calls ADD COLUMN collected_data TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_calls_download_token"
    " ON calls(download_token)",
    # Contacts phone book: groups + gender/details, call→contact binding.
    # contact_groups itself is created by schema.sql (CREATE IF NOT EXISTS).
    "ALTER TABLE contacts ADD COLUMN gender TEXT",
    "ALTER TABLE contacts ADD COLUMN details TEXT",
    "ALTER TABLE contacts ADD COLUMN group_id INTEGER",
    "ALTER TABLE calls ADD COLUMN contact_id INTEGER",
    # Same 10 significant digits under one owner = same person, regardless of
    # +91/0-prefix formatting (verified duplicate-free before adding).
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_owner_sig"
    " ON contacts(owner, sig_digits)",
    "CREATE INDEX IF NOT EXISTS idx_calls_contact ON calls(contact_id)",
]

_init_lock = threading.Lock()
_initialized_urls: set[str] = set()


def db_url() -> str:
    return os.environ.get("LESCALL_DB_URL", f"sqlite:///{DEFAULT_SQLITE_PATH}")


def _is_postgres(url: str) -> bool:
    return url.startswith(("postgres://", "postgresql://"))


def _schema_sql(url: str) -> str:
    raw = SCHEMA_PATH.read_text(encoding="utf-8")
    return raw.replace("{PK}", _PK_POSTGRES if _is_postgres(url) else _PK_SQLITE)


class _QmarkPgConnection:
    """Adapts psycopg so callers can keep sqlite-style "?" placeholders."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=()):
        return self._conn.execute(sql.replace("?", "%s"), params)

    def executescript(self, sql: str):
        return self._conn.execute(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def _connect(url: str):
    if _is_postgres(url):
        import psycopg
        from psycopg.rows import dict_row

        class _TextLoader(psycopg.adapt.Loader):
            """Return timestamps as strings, matching what SQLite callers get
            (rows are json.dumps'd all over the dashboard)."""

            def load(self, data):
                return bytes(data).decode()

        conn = psycopg.connect(url, row_factory=dict_row)
        for pgtype in ("timestamp", "timestamptz", "date"):
            conn.adapters.register_loader(pgtype, _TextLoader)
        return _QmarkPgConnection(conn)

    path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(conn, url: str | None = None) -> None:
    """Create tables/indexes and apply idempotent migrations. Safe to re-run."""
    url = url or db_url()
    conn.executescript(_schema_sql(url))
    conn.commit()
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
            conn.commit()
        except Exception as exc:  # column already exists — expected on re-run
            if not re.search(r"duplicate|already exists", str(exc), re.I):
                raise
            # Postgres aborts the transaction on any error; clear it so the
            # next migration statement can run.
            conn.rollback()


def _ensure_initialized(url: str) -> None:
    if url in _initialized_urls:
        return
    with _init_lock:
        if url in _initialized_urls:
            return
        conn = _connect(url)
        try:
            init_db(conn, url)
        finally:
            conn.close()
        _initialized_urls.add(url)


@contextmanager
def get_db(url: str | None = None):
    """Yield a connection; commit on success, roll back on exception."""
    url = url or db_url()
    # :memory: gets a fresh empty DB per connection, so init must happen on
    # THIS connection, not a throwaway one.
    in_memory = not _is_postgres(url) and url.endswith(":memory:")
    if not in_memory:
        _ensure_initialized(url)
    conn = _connect(url)
    try:
        if in_memory:
            init_db(conn, url)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
