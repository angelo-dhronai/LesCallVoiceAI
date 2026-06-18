"""Lightweight SQLite data-access layer shared across lescall services.

No ORM, no third-party deps — stdlib sqlite3 only, so the live pipeline venv,
the dashboard API, and the outbound engine can all import it without new installs.

Usage:
    from shared.db.database import get_db, init_db
    init_db()                      # idempotent; creates tables from schema.sql
    with get_db() as db:
        row = db.execute("SELECT * FROM agents WHERE id=?", (1,)).fetchone()
        agent = dict(row)          # rows are sqlite3.Row -> dict-able
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Repo root = two levels up from this file (shared/db/database.py -> repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("LESCALL_DB_PATH", _REPO_ROOT / "data" / "lescall.db"))
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def get_db():
    """Transactional connection context. Commits on success, rolls back on error."""
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
    """Create tables from schema.sql. Idempotent (CREATE TABLE IF NOT EXISTS)."""
    schema = SCHEMA_PATH.read_text()
    with get_db() as conn:
        conn.executescript(schema)


if __name__ == "__main__":
    init_db()
    print(f"Initialized DB at {DB_PATH}")
