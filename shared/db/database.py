"""Data-access layer — works on both SQLite and PostgreSQL.

Backend is chosen by the DATABASE_URL env var:
  * postgresql://user:pass@host:5432/dbname  -> PostgreSQL (psycopg3), for multi-node prod
  * unset                                    -> SQLite file at data/lescall.db (single-node / fallback)

A thin wrapper keeps the call sites identical across backends: queries use `?`
placeholders (translated to `%s` for Postgres) and rows come back dict-able.
Inserts use `RETURNING id` (SQLite >= 3.35 and Postgres both support it) instead
of lastrowid. Timestamps use CURRENT_TIMESTAMP.

Dependency-light: psycopg is imported lazily and only when DATABASE_URL is set,
so the SQLite path (used by the live pipeline venv) needs no extra packages.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

# SQLite location (only used when DATABASE_URL is unset).
DB_PATH = Path(os.environ.get("LESCALL_DB_PATH", _REPO_ROOT / "data" / "lescall.db"))


class Conn:
    """Uniform connection wrapper over sqlite3 / psycopg3.

    .execute(sql, params) accepts `?` placeholders and returns a cursor whose
    rows behave like dicts (`row["col"]`, `dict(row)`). Iterable like a cursor.
    """

    def __init__(self, raw, is_pg: bool):
        self._raw = raw
        self._is_pg = is_pg

    def execute(self, sql: str, params=()):
        if self._is_pg:
            cur = self._raw.cursor()
            cur.execute(sql.replace("?", "%s"), tuple(params))
            return cur
        return self._raw.execute(sql, tuple(params))

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()


def _connect() -> Conn:
    if IS_POSTGRES:
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False)
        return Conn(raw, True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(DB_PATH), timeout=30.0)
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA foreign_keys = ON")
    raw.execute("PRAGMA journal_mode = WAL")
    return Conn(raw, False)


@contextmanager
def get_db():
    """Transactional connection. Commits on success, rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Create tables from schema.sql (idempotent), dialect-aware."""
    pk = "SERIAL PRIMARY KEY" if IS_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    schema = SCHEMA_PATH.read_text().replace("{PK}", pk)
    # Strip full-line SQL comments, then split into individual statements.
    code = "\n".join(ln for ln in schema.splitlines() if not ln.lstrip().startswith("--"))
    statements = [s.strip() for s in code.split(";") if s.strip()]
    with get_db() as conn:
        for stmt in statements:
            conn.execute(stmt)


def backend() -> str:
    return "postgresql" if IS_POSTGRES else "sqlite"


if __name__ == "__main__":
    init_db()
    print(f"Initialized {backend()} DB"
          + (f" at {DB_PATH}" if not IS_POSTGRES else f" via {DATABASE_URL.split('@')[-1]}"))
