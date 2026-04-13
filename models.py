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

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    drop_id TEXT REFERENCES drops(id),
    amount_cents INTEGER NOT NULL,
    stripe_session_id TEXT,
    stripe_payment_intent TEXT,
    type TEXT NOT NULL CHECK(type IN ('drop_purchase', 'ownership_purchase', 'boost', 'subscription')),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'completed', 'failed', 'refunded')),
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id TEXT NOT NULL REFERENCES users(id),
    following_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (follower_id, following_id)
);

CREATE TABLE IF NOT EXISTS badges (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    badge_type TEXT NOT NULL,
    badge_data TEXT DEFAULT '{}',
    earned_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(user_id, badge_type, badge_data)
);

CREATE TABLE IF NOT EXISTS boosts (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    artist_id TEXT NOT NULL REFERENCES users(id),
    budget_cents INTEGER NOT NULL,
    spent_cents INTEGER NOT NULL DEFAULT 0,
    target_city TEXT,
    target_scene_id TEXT REFERENCES scenes(id),
    duration_hours INTEGER NOT NULL DEFAULT 24,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'active', 'completed', 'cancelled')),
    started_at TEXT,
    expires_at TEXT,
    stripe_session_id TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS artist_subscriptions (
    id TEXT PRIMARY KEY,
    fan_id TEXT NOT NULL REFERENCES users(id),
    artist_id TEXT NOT NULL REFERENCES users(id),
    tier TEXT NOT NULL DEFAULT 'basic' CHECK(tier IN ('basic', 'premium')),
    price_monthly REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'cancelled')),
    started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    cancelled_at TEXT,
    UNIQUE(fan_id, artist_id)
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

CREATE TABLE IF NOT EXISTS dmca_reports (
    id TEXT PRIMARY KEY,
    drop_id TEXT NOT NULL REFERENCES drops(id),
    claimant_name TEXT NOT NULL,
    claimant_email TEXT NOT NULL,
    original_work TEXT NOT NULL,
    statement_confirmed INTEGER NOT NULL DEFAULT 0,
    perjury_confirmed INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    counter_statement TEXT,
    counter_filed_at TEXT,
    admin_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS email_verifications (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS password_resets (
    token TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id),
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_transactions_drop ON transactions(drop_id);
CREATE INDEX IF NOT EXISTS idx_transactions_stripe ON transactions(stripe_session_id);
CREATE INDEX IF NOT EXISTS idx_follows_follower ON follows(follower_id);
CREATE INDEX IF NOT EXISTS idx_follows_following ON follows(following_id);
CREATE INDEX IF NOT EXISTS idx_badges_user ON badges(user_id);
CREATE INDEX IF NOT EXISTS idx_boosts_drop ON boosts(drop_id);
CREATE INDEX IF NOT EXISTS idx_boosts_artist ON boosts(artist_id);
CREATE INDEX IF NOT EXISTS idx_drops_status ON drops(status);
CREATE INDEX IF NOT EXISTS idx_drops_artist ON drops(artist_id);
CREATE INDEX IF NOT EXISTS idx_drops_starts ON drops(starts_at);
CREATE INDEX IF NOT EXISTS idx_drop_access_user ON drop_access(user_id);
CREATE INDEX IF NOT EXISTS idx_drop_access_drop ON drop_access(drop_id);
CREATE INDEX IF NOT EXISTS idx_drop_engagement_drop ON drop_engagement(drop_id);
CREATE INDEX IF NOT EXISTS idx_drop_engagement_user ON drop_engagement(user_id);
CREATE INDEX IF NOT EXISTS idx_dmca_drop ON dmca_reports(drop_id);
CREATE INDEX IF NOT EXISTS idx_email_verif_user ON email_verifications(user_id);
CREATE INDEX IF NOT EXISTS idx_password_resets_user ON password_resets(user_id);
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

    # Migration: add columns that may not exist in older DBs
    migrations = [
        # Phase 1 migrations
        "ALTER TABLE drops ADD COLUMN city TEXT",
        "ALTER TABLE drops ADD COLUMN boost_active INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE drop_access ADD COLUMN fan_number INTEGER",
        "ALTER TABLE users ADD COLUMN suspended INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN follower_count INTEGER NOT NULL DEFAULT 0",
        # Phase 2 migrations
        "ALTER TABLE users ADD COLUMN stripe_customer_id TEXT",
        "ALTER TABLE drops ADD COLUMN stripe_price_id TEXT",
        "ALTER TABLE boosts ADD COLUMN stripe_session_id TEXT",
        "ALTER TABLE boosts ADD COLUMN impressions INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE boosts ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE boosts ADD COLUMN started_at TEXT",
        "ALTER TABLE boosts ADD COLUMN expires_at TEXT",
        "ALTER TABLE boosts ADD COLUMN duration_hours INTEGER NOT NULL DEFAULT 24",
        # Phase 3 migrations
        "ALTER TABLE users ADD COLUMN stripe_connect_id TEXT",
        "ALTER TABLE users ADD COLUMN stripe_onboarded INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN tos_agreed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE drops ADD COLUMN dmca_review INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE drops ADD COLUMN r2_audio_key TEXT",
        "ALTER TABLE drops ADD COLUMN r2_cover_key TEXT",
        "ALTER TABLE transactions ADD COLUMN refunded_at TEXT",
        "ALTER TABLE transactions ADD COLUMN refund_reason TEXT",
        "ALTER TABLE transactions ADD COLUMN stripe_connect_id TEXT",
    ]
    for sql in migrations:
        try:
            conn.execute(sql)
        except Exception:
            pass  # Column already exists

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
