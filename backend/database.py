"""SQLite connection management, schema initialization, and demo data seeding."""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = BASE_DIR / "app.db"

DEMO_USERS: list[tuple[str, str]] = [
    ("Alice", "alice@ajaia.dev"),
    ("Bob", "bob@ajaia.dev"),
    ("Sara", "sara@ajaia.dev"),
]

WELCOME_DOC_TITLE = "Welcome to Ajaia Docs"
WELCOME_DOC_HTML = (
    "<h1>Welcome to Ajaia Docs</h1>"
    "<p>Ajaia Docs is a lightweight collaborative document editor built with "
    "<strong>FastAPI</strong>, <strong>SQLite</strong>, and <strong>Quill.js</strong>.</p>"
    "<h2>What You Can Do</h2>"
    "<ul>"
    "<li>Create and format rich-text documents with headings, lists, and inline styles.</li>"
    "<li>Rename documents inline from the header bar and save changes persistently.</li>"
    "<li>Import existing <strong>.txt</strong>, <strong>.md</strong>, and <strong>.docx</strong> files directly into editable documents.</li>"
    "<li>Share documents with teammates and switch users from the top bar to test permissions.</li>"
    "</ul>"
    "<h3>Quick Tip</h3>"
    "<p>Click <em>Share</em> in the top-right corner to grant <strong>Bob</strong> or "
    "<strong>Sara</strong> access to this document, then switch users in the header dropdown!</p>"
)


def utc_now_iso() -> str:
    """Return the current UTC timestamp formatted as an ISO-8601 string."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db_path() -> Path:
    """Resolve the active SQLite database path (overridable via AJAIA_DB_PATH)."""
    override = os.environ.get("AJAIA_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def create_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection with Row access and foreign key enforcement."""
    resolved_path = Path(db_path) if db_path is not None else get_db_path()
    conn = sqlite3.connect(str(resolved_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def db_session(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Provide a transactional SQLite connection that commits on success and closes cleanly."""
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path | str | None = None) -> None:
    """Create database tables if missing and seed demo users plus a welcome document."""
    with db_session(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                content_html TEXT NOT NULL DEFAULT '',
                owner_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS document_shares (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(document_id, user_id),
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            """
        )

        user_count = conn.execute("SELECT COUNT(*) AS count FROM users").fetchone()["count"]
        if user_count == 0:
            conn.executemany(
                "INSERT INTO users (name, email) VALUES (?, ?)",
                DEMO_USERS,
            )

            alice_row = conn.execute(
                "SELECT id FROM users WHERE email = ?",
                ("alice@ajaia.dev",),
            ).fetchone()
            doc_count = conn.execute("SELECT COUNT(*) AS count FROM documents").fetchone()["count"]
            if alice_row and doc_count == 0:
                now = utc_now_iso()
                conn.execute(
                    """
                    INSERT INTO documents (title, content_html, owner_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (WELCOME_DOC_TITLE, WELCOME_DOC_HTML, alice_row["id"], now, now),
                )
