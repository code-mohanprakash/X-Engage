"""
SQLite database operations for the Twitter Engagement Agent.
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "database.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id TEXT PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    author_handle TEXT NOT NULL,
    author_name TEXT,
    author_followers INTEGER DEFAULT 0,
    author_verified INTEGER DEFAULT 0,
    text TEXT NOT NULL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    replies INTEGER DEFAULT 0,
    retweets INTEGER DEFAULT 0,
    created_at TIMESTAMP,
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    source TEXT,
    score REAL DEFAULT 0,
    status TEXT DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    comment_type TEXT NOT NULL,
    text TEXT NOT NULL,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    issues TEXT,
    FOREIGN KEY (post_id) REFERENCES posts(id)
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    comment_id INTEGER NOT NULL,
    option_chosen TEXT,
    custom_text TEXT,
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (comment_id) REFERENCES comments(id)
);

CREATE TABLE IF NOT EXISTS account_checks (
    handle TEXT PRIMARY KEY,
    last_checked_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comment_performance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT NOT NULL,
    comment_id INTEGER NOT NULL,
    likes_after_24h INTEGER DEFAULT 0,
    replies_after_24h INTEGER DEFAULT 0,
    measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (post_id) REFERENCES posts(id),
    FOREIGN KEY (comment_id) REFERENCES comments(id)
);
"""


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()

    # ── Posts ────────────────────────────────────────────────────────────────

    def post_exists(self, post_id: str) -> bool:
        row = self.execute("SELECT 1 FROM posts WHERE id = ?", (post_id,)).fetchone()
        return row is not None

    def url_exists(self, url: str) -> bool:
        row = self.execute("SELECT 1 FROM posts WHERE url = ?", (url,)).fetchone()
        return row is not None

    def insert_post(self, post: dict):
        self.execute("""
            INSERT OR IGNORE INTO posts
              (id, url, author_handle, author_name, author_followers, author_verified,
               text, views, likes, replies, retweets, created_at, source, score, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'pending')
        """, (
            post["id"], post["url"], post["author_handle"], post.get("author_name"),
            post.get("author_followers", 0), int(post.get("author_verified", False)),
            post["text"], post.get("views", 0), post.get("likes", 0),
            post.get("replies", 0), post.get("retweets", 0),
            post.get("created_at"), post.get("source"), post.get("score", 0),
        ))
        self.commit()

    def update_post_status(self, post_id: str, status: str):
        self.execute("UPDATE posts SET status = ? WHERE id = ?", (status, post_id))
        self.commit()

    def get_post(self, post_id: str) -> Optional[dict]:
        row = self.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None

    def get_already_seen_ids(self) -> set:
        rows = self.execute("SELECT id FROM posts").fetchall()
        return {r["id"] for r in rows}

    # ── Comments ─────────────────────────────────────────────────────────────

    def insert_comment(self, post_id: str, comment_type: str, text: str, issues: list) -> int:
        cur = self.execute("""
            INSERT INTO comments (post_id, comment_type, text, issues)
            VALUES (?,?,?,?)
        """, (post_id, comment_type, text, json.dumps(issues)))
        self.commit()
        return cur.lastrowid

    def get_comment(self, comment_id: int) -> Optional[dict]:
        row = self.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        return dict(row) if row else None

    def get_comments_for_post(self, post_id: str) -> list:
        rows = self.execute(
            "SELECT * FROM comments WHERE post_id = ? ORDER BY id", (post_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Approvals ────────────────────────────────────────────────────────────

    def insert_approval(self, post_id: str, comment_id: int, option: str, custom_text: str = None) -> int:
        cur = self.execute("""
            INSERT INTO approvals (post_id, comment_id, option_chosen, custom_text)
            VALUES (?,?,?,?)
        """, (post_id, comment_id, option, custom_text))
        self.commit()
        return cur.lastrowid

    def mark_posted(self, approval_id: int):
        self.execute(
            "UPDATE approvals SET posted_at = CURRENT_TIMESTAMP WHERE id = ?",
            (approval_id,)
        )
        self.commit()

    # ── Account check tracking ────────────────────────────────────────────────

    def get_last_check_time(self, handle: str) -> Optional[datetime]:
        row = self.execute(
            "SELECT last_checked_at FROM account_checks WHERE handle = ?", (handle,)
        ).fetchone()
        if row and row["last_checked_at"]:
            return datetime.fromisoformat(row["last_checked_at"])
        return None

    def update_last_check_time(self, handle: str):
        self.execute("""
            INSERT INTO account_checks (handle, last_checked_at)
            VALUES (?, CURRENT_TIMESTAMP)
            ON CONFLICT(handle) DO UPDATE SET last_checked_at = CURRENT_TIMESTAMP
        """, (handle,))
        self.commit()

    # ── Watchlist management ──────────────────────────────────────────────────

    def add_to_watchlist(self, handle: str, priority: str = "medium", check_every_hours: int = 6) -> bool:
        """
        Add a handle to the watched accounts list.
        Returns True if newly added, False if already present.
        """
        # Check account_checks table (DB side)
        existing = self.execute(
            "SELECT 1 FROM account_checks WHERE handle = ?", (handle,)
        ).fetchone()
        if existing:
            return False  # already tracked

        # Insert with no last_checked_at so it gets picked up on next run
        self.execute(
            "INSERT OR IGNORE INTO account_checks (handle, last_checked_at) VALUES (?, NULL)",
            (handle,)
        )
        self.commit()
        return True

    def get_all_watched_handles(self) -> list:
        rows = self.execute("SELECT handle FROM account_checks").fetchall()
        return [r["handle"] for r in rows]

    # ── Rate limiting ─────────────────────────────────────────────────────────

    def count_approved_last_hour(self) -> int:
        row = self.execute("""
            SELECT COUNT(*) as cnt FROM approvals
            WHERE approved_at >= datetime('now', '-1 hour')
        """).fetchone()
        return row["cnt"] if row else 0

    def count_approved_today(self) -> int:
        row = self.execute("""
            SELECT COUNT(*) as cnt FROM approvals
            WHERE DATE(approved_at) = DATE('now')
        """).fetchone()
        return row["cnt"] if row else 0

    # ── Daily report data ─────────────────────────────────────────────────────

    def daily_stats(self) -> dict:
        today_posts = self.execute("""
            SELECT COUNT(*) as cnt FROM posts WHERE DATE(discovered_at) = DATE('now')
        """).fetchone()["cnt"]

        today_approved = self.execute("""
            SELECT COUNT(*) as cnt FROM approvals WHERE DATE(approved_at) = DATE('now')
        """).fetchone()["cnt"]

        today_posted = self.execute("""
            SELECT COUNT(*) as cnt FROM approvals
            WHERE DATE(posted_at) = DATE('now')
        """).fetchone()["cnt"]

        return {
            "posts_discovered": today_posts,
            "approved": today_approved,
            "posted": today_posted,
        }
