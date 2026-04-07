"""
BLK MRKT — Database schema and query helpers.
SQLite for MVP, designed for clean Postgres migration.
"""

import sqlite3
import uuid
import os
from datetime import datetime, timezone
from config import DB_PATH

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('artist', 'fan', 'curator', 'label', 'admin')),
    city TEXT,
    bio TEXT DEFAULT '',
    avatar_url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS drops (
    id TEXT PRIMARY KEY,
    artist_id TEXT NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    audio_path TEXT,
    cover_image_path TEXT,
    drop_type TEXT NOT NULL CHECK(drop_type IN ('open', 'timed', 'limited', 'tiered', 'rare')),
    total_supply INTEGER,
    remaining_supply INTEGER,
    access_price REAL NOT NULL DEFAULT 0,
    own_price REAL,
    starts_at TEXT NOT NULL,
    expires_at TEXT,
    status TEXT NOT NULL DEFAULT 'scheduled' CHECK(status IN ('scheduled', 'live', 'locked', 'expired')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS drop_access (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    drop_id TEXT NOT NULL REFERENCES drops(id),
    access_type TEXT NOT NULL CHECK(access_type IN ('stream', 'own')),
    acquired_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    price_paid REAL NOT NULL DEFAULT 0,
    UNIQUE(user_id, drop_id, access_type)
);

CREATE TABLE IF NOT EXISTS drop_engagement (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL REFERENCES users(id),
    drop_id TEXT NOT NULL REFERENCES drops(id),
    action TEXT NOT NULL CHECK(action IN ('play', 'replay', 'save', 'share', 'profile_click')),
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    metadata TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scenes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT,
    description TEXT DEFAULT '',
    created_by TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS drop_scenes (
    drop_id TEXT NOT NULL REFERENCES drops(id),
    scene_id TEXT NOT NULL REFERENCES scenes(id),
    PRIMARY KEY (drop_id, scene_id)
);

CREATE TABLE IF NOT EXISTS labels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    owner_id TEXT NOT NULL REFERENCES users(id),
    bio TEXT DEFAULT '',
    city TEXT,
    logo_url TEXT DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS label_artists (
    label_id TEXT NOT NULL REFERENCES labels(id),
    artist_id TEXT NOT NULL REFERENCES users(id),
    invited_by TEXT REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'pending', 'removed')),
    joined_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (label_id, artist_id)
);

CREATE INDEX IF NOT EXISTS idx_drops_status ON drops(status);
CREATE INDEX IF NOT EXISTS idx_drops_artist ON drops(artist_id);
CREATE INDEX IF NOT EXISTS idx_drops_starts ON drops(starts_at);
CREATE INDEX IF NOT EXISTS idx_drop_access_user ON drop_access(user_id);
CREATE INDEX IF NOT EXISTS idx_drop_access_drop ON drop_access(drop_id);
CREATE INDEX IF NOT EXISTS idx_drop_engagement_drop ON drop_engagement(drop_id);
CREATE INDEX IF NOT EXISTS idx_drop_engagement_user ON drop_engagement(user_id);
"""


def get_db():
    """Get a database connection with WAL mode and foreign keys enabled."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Initialize the database schema."""
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def new_id():
    """Generate a new UUID."""
    return str(uuid.uuid4())


def utcnow():
    """ISO-formatted UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def rows_to_list(rows):
    """Convert a list of sqlite3.Row to list of dicts."""
    return [dict(r) for r in rows]


def query_one(sql, params=()):
    conn = get_db()
    try:
        row = conn.execute(sql, params).fetchone()
        return row_to_dict(row)
    finally:
        conn.close()


def query_all(sql, params=()):
    conn = get_db()
    try:
        rows = conn.execute(sql, params).fetchall()
        return rows_to_list(rows)
    finally:
        conn.close()


def execute(sql, params=()):
    conn = get_db()
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def execute_returning(sql, params=()):
    """Execute and return lastrowid."""
    conn = get_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
