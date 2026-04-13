"""
BLK MRKT — Seed Script

Creates demo content so the platform feels alive on first launch.
Idempotent: re-running will not create duplicate users/drops.

Usage:
  python seed.py               # run directly
  GET /api/admin/reseed?secret=<ADMIN_SECRET>   # via API

Demo accounts (all password: blkmrkt2025):
  admin@blkmrkt.com   — admin
  nova@blkmrkt.com    — artist (Nova Hex)
  saint@blkmrkt.com   — artist (Saint Cipher)
  lune@blkmrkt.com    — artist (LUNE)
  draco@blkmrkt.com   — artist (DRACO)
  fan1@blkmrkt.com    — fan (First Listener)
  fan2@blkmrkt.com    — fan (Underground Freq)
"""

import os
import struct
import math
import time

import bcrypt
from models import get_db, new_id, utcnow, init_db
from config import AUDIO_DIR, COVERS_DIR


DEMO_PASSWORD = "blkmrkt2025"


# ---------------------------------------------------------------------------
# Minimal WAV generator — no dependencies
# ---------------------------------------------------------------------------

def _generate_wav(freq_hz: float = 440.0, duration_s: float = 8.0,
                  sample_rate: int = 22050) -> bytes:
    """
    Generate a minimal mono WAV file with a sine wave tone.
    Produces realistic-looking audio files without any audio library.
    """
    n_samples = int(sample_rate * duration_s)
    samples = []
    for i in range(n_samples):
        # Sine wave with gentle fade-in / fade-out
        t = i / sample_rate
        fade = min(t / 0.3, 1.0, (duration_s - t) / 0.5)
        sample = int(32767 * fade * math.sin(2 * math.pi * freq_hz * t))
        samples.append(max(-32768, min(32767, sample)))

    pcm = struct.pack(f"<{n_samples}h", *samples)
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 36 + data_size, b"WAVE",
        b"fmt ", 16, 1, 1,       # PCM, mono
        sample_rate, sample_rate * 2, 2, 16,
        b"data", data_size,
    )
    return header + pcm


# ---------------------------------------------------------------------------
# Minimal SVG cover generator — pure Python
# ---------------------------------------------------------------------------

def _generate_svg_cover(title: str, artist: str, color1: str, color2: str) -> bytes:
    """Generate a minimal SVG cover image."""
    # Truncate for display
    t = title[:22] + ("…" if len(title) > 22 else "")
    a = artist[:18] + ("…" if len(artist) > 18 else "")
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" viewBox="0 0 600 600">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:{color1};stop-opacity:1" />
      <stop offset="100%" style="stop-color:{color2};stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="600" height="600" fill="url(#bg)"/>
  <rect x="40" y="40" width="520" height="520" rx="8" fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="1"/>
  <circle cx="300" cy="230" r="90" fill="none" stroke="rgba(255,255,255,0.2)" stroke-width="2"/>
  <circle cx="300" cy="230" r="55" fill="rgba(0,0,0,0.4)"/>
  <polygon points="285,210 325,230 285,250" fill="rgba(255,255,255,0.7)"/>
  <text x="300" y="395" font-family="Arial,sans-serif" font-size="28" font-weight="bold"
        fill="white" text-anchor="middle" dominant-baseline="middle">{t}</text>
  <text x="300" y="435" font-family="Arial,sans-serif" font-size="18"
        fill="rgba(255,255,255,0.7)" text-anchor="middle" dominant-baseline="middle">{a}</text>
  <text x="300" y="545" font-family="Arial,sans-serif" font-size="13"
        fill="rgba(255,255,255,0.35)" text-anchor="middle">BLK MRKT</text>
</svg>"""
    return svg.encode("utf-8")


# ---------------------------------------------------------------------------
# Seed data definitions
# ---------------------------------------------------------------------------

SCENES = [
    {"name": "East Atlanta Drill", "city": "Atlanta", "desc": "Raw trap, drill, and street rap from ATL's eastside."},
    {"name": "Chicago Underground", "city": "Chicago", "desc": "Drill origins, juke, footwork, and experimental south side sounds."},
    {"name": "Brooklyn Dark Ambient", "city": "New York", "desc": "Lo-fi, abstract, and dystopian electronic from BK."},
    {"name": "Houston Slowed & Throwed", "city": "Houston", "desc": "Chopped not slopped — DJ Screw disciples and new wave."},
    {"name": "LA Beat Scene", "city": "Los Angeles", "desc": "Low end theory descendants, instrumental hip-hop, and neo-soul."},
    {"name": "Detroit Techno", "city": "Detroit", "desc": "Underground club music rooted in the birthplace of techno."},
    {"name": "UK Grime / Afroswing", "city": "London", "desc": "Grime, UK drill, Afroswing, and next-wave sounds from London."},
    {"name": "Philly Soul & Funk", "city": "Philadelphia", "desc": "Neo-soul, gospel-influenced R&B, and classic Philly sounds."},
]

ARTISTS = [
    {
        "email": "nova@blkmrkt.com",
        "username": "Nova Hex",
        "city": "Atlanta",
        "bio": "Dark trap from the eastside. 808s and encrypted frequencies.",
        "color1": "#1a0a2e", "color2": "#6c1b8c",
        "scene": "East Atlanta Drill",
    },
    {
        "email": "saint@blkmrkt.com",
        "username": "Saint Cipher",
        "city": "Chicago",
        "bio": "Drill instrumentals and abstract beats. Coded in the underground.",
        "color1": "#0d1b2a", "color2": "#1b4332",
        "scene": "Chicago Underground",
    },
    {
        "email": "lune@blkmrkt.com",
        "username": "LUNE",
        "city": "New York",
        "bio": "Ambient producer. Sound design for the hours between 2 and 5am.",
        "color1": "#0a0a1a", "color2": "#1a2040",
        "scene": "Brooklyn Dark Ambient",
    },
    {
        "email": "draco@blkmrkt.com",
        "username": "DRACO",
        "city": "Houston",
        "bio": "Slowed everything down and found God in the reverb.",
        "color1": "#1a0505", "color2": "#4a1010",
        "scene": "Houston Slowed & Throwed",
    },
]

DROPS_BY_ARTIST = {
    "Nova Hex": [
        {"title": "NEON SERMON", "type": "limited", "supply": 100, "price": 2.99, "freq": 220},
        {"title": "ENCRYPTED FREQUENCY", "type": "timed", "supply": None, "price": 0, "freq": 180},
        {"title": "TRAP GEOMETRY", "type": "rare", "supply": 25, "price": 9.99, "freq": 260},
        {"title": "HEXCODE 808", "type": "open", "supply": None, "price": 0, "freq": 110},
    ],
    "Saint Cipher": [
        {"title": "CODED PSALMS", "type": "limited", "supply": 50, "price": 4.99, "freq": 196},
        {"title": "UNDERGROUND SIGNAL", "type": "open", "supply": None, "price": 0, "freq": 165},
        {"title": "CRYPT ARITHMETIC", "type": "timed", "supply": None, "price": 1.99, "freq": 294},
    ],
    "LUNE": [
        {"title": "3AM ARCHITECTURE", "type": "rare", "supply": 10, "price": 14.99, "freq": 528},
        {"title": "SLEEP PARALYSIS SESSIONS", "type": "limited", "supply": 75, "price": 3.99, "freq": 432},
        {"title": "VOID FREQUENCY", "type": "open", "supply": None, "price": 0, "freq": 396},
    ],
    "DRACO": [
        {"title": "PURPLE REVERB", "type": "open", "supply": None, "price": 0, "freq": 144},
        {"title": "SLOW BAPTISM", "type": "limited", "supply": 200, "price": 1.99, "freq": 108},
        {"title": "MEMORIAL DAY LEAK", "type": "rare", "supply": 33, "price": 7.77, "freq": 174},
    ],
}

FAN_ACCOUNTS = [
    {"email": "fan1@blkmrkt.com", "username": "First Listener", "city": "Atlanta"},
    {"email": "fan2@blkmrkt.com", "username": "Underground Freq", "city": "Chicago"},
    {"email": "fan3@blkmrkt.com", "username": "NightOwl404", "city": "New York"},
]

ADMIN_ACCOUNT = {
    "email": "admin@blkmrkt.com",
    "username": "BLK MRKT Admin",
    "role": "admin",
}


# ---------------------------------------------------------------------------
# Core seed function
# ---------------------------------------------------------------------------

def run_seed() -> dict:
    init_db()
    conn = get_db()
    result = {"scenes": 0, "artists": 0, "fans": 0, "drops": 0, "follows": 0, "engagements": 0}

    try:
        pw_hash = bcrypt.hashpw(DEMO_PASSWORD.encode(), bcrypt.gensalt()).decode()

        # ---- Admin ----
        existing = conn.execute("SELECT id FROM users WHERE email = ?", (ADMIN_ACCOUNT["email"],)).fetchone()
        if not existing:
            admin_id = new_id()
            conn.execute(
                """INSERT INTO users (id, email, username, password_hash, role, email_verified, tos_agreed)
                   VALUES (?, ?, ?, ?, 'admin', 1, 1)""",
                (admin_id, ADMIN_ACCOUNT["email"], ADMIN_ACCOUNT["username"], pw_hash),
            )
        else:
            admin_id = existing["id"]

        # ---- Scenes ----
        scene_ids = {}
        for s in SCENES:
            existing = conn.execute("SELECT id FROM scenes WHERE name = ?", (s["name"],)).fetchone()
            if existing:
                scene_ids[s["name"]] = existing["id"]
                continue
            sid = new_id()
            conn.execute(
                "INSERT INTO scenes (id, name, city, description, created_by) VALUES (?, ?, ?, ?, ?)",
                (sid, s["name"], s["city"], s["desc"], admin_id),
            )
            scene_ids[s["name"]] = sid
            result["scenes"] += 1

        # ---- Artists ----
        artist_ids = {}
        for a in ARTISTS:
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (a["email"],)).fetchone()
            if existing:
                artist_ids[a["username"]] = existing["id"]
                continue
            uid = new_id()
            conn.execute(
                """INSERT INTO users
                   (id, email, username, password_hash, role, city, bio, email_verified, tos_agreed)
                   VALUES (?, ?, ?, ?, 'artist', ?, ?, 1, 1)""",
                (uid, a["email"], a["username"], pw_hash, a["city"], a["bio"]),
            )
            artist_ids[a["username"]] = uid
            result["artists"] += 1

        # ---- Fans ----
        fan_ids = []
        for f in FAN_ACCOUNTS:
            existing = conn.execute("SELECT id FROM users WHERE email = ?", (f["email"],)).fetchone()
            if existing:
                fan_ids.append(existing["id"])
                continue
            uid = new_id()
            conn.execute(
                """INSERT INTO users
                   (id, email, username, password_hash, role, city, email_verified, tos_agreed)
                   VALUES (?, ?, ?, ?, 'fan', ?, 1, 1)""",
                (uid, f["email"], f["username"], pw_hash, f["city"]),
            )
            fan_ids.append(uid)
            result["fans"] += 1

        conn.commit()

        # ---- Drops ----
        os.makedirs(AUDIO_DIR, exist_ok=True)
        os.makedirs(COVERS_DIR, exist_ok=True)

        artist_meta = {a["username"]: a for a in ARTISTS}
        drop_ids_by_artist = {}

        for artist_name, drops in DROPS_BY_ARTIST.items():
            if artist_name not in artist_ids:
                continue
            artist_id = artist_ids[artist_name]
            meta = artist_meta[artist_name]
            scene_id = scene_ids.get(meta["scene"])
            drop_ids_by_artist[artist_name] = []

            for d in drops:
                existing = conn.execute(
                    "SELECT id FROM drops WHERE artist_id = ? AND title = ?",
                    (artist_id, d["title"]),
                ).fetchone()
                if existing:
                    drop_ids_by_artist[artist_name].append(existing["id"])
                    continue

                drop_id = new_id()
                price = d["price"]

                # Generate audio file
                wav_path = os.path.join(AUDIO_DIR, f"{drop_id}.wav")
                if not os.path.exists(wav_path):
                    wav_data = _generate_wav(freq_hz=d["freq"], duration_s=12.0)
                    with open(wav_path, "wb") as fout:
                        fout.write(wav_data)

                # Generate cover SVG
                cover_path = os.path.join(COVERS_DIR, f"{drop_id}.svg")
                if not os.path.exists(cover_path):
                    svg_data = _generate_svg_cover(d["title"], artist_name, meta["color1"], meta["color2"])
                    with open(cover_path, "wb") as fout:
                        fout.write(svg_data)

                # Set starts_at slightly in the past so drops are "live"
                conn.execute(
                    """INSERT INTO drops
                       (id, artist_id, title, description, audio_path, cover_image_path,
                        drop_type, total_supply, remaining_supply, access_price,
                        starts_at, status, city)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','-1 hour'), 'live', ?)""",
                    (drop_id, artist_id,
                     d["title"],
                     f"A {d['type']} drop from {artist_name}.",
                     f"/data/audio/{drop_id}.wav",
                     f"/data/covers/{drop_id}.svg",
                     d["type"],
                     d["supply"], d["supply"],
                     price,
                     meta["city"]),
                )

                if scene_id:
                    conn.execute(
                        "INSERT OR IGNORE INTO drop_scenes (drop_id, scene_id) VALUES (?, ?)",
                        (drop_id, scene_id),
                    )

                drop_ids_by_artist[artist_name].append(drop_id)
                result["drops"] += 1

        conn.commit()

        # ---- Follows: fans follow all artists ----
        all_artist_ids = list(artist_ids.values())
        for fan_id in fan_ids:
            for art_id in all_artist_ids:
                existing = conn.execute(
                    "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
                    (fan_id, art_id),
                ).fetchone()
                if not existing:
                    conn.execute(
                        "INSERT INTO follows (follower_id, following_id) VALUES (?, ?)",
                        (fan_id, art_id),
                    )
                    conn.execute(
                        "UPDATE users SET follower_count = follower_count + 1 WHERE id = ?",
                        (art_id,),
                    )
                    result["follows"] += 1

        conn.commit()

        # ---- Seed engagement events for velocity ----
        # Each fan plays and saves a few drops
        import random
        random.seed(42)
        all_drop_ids = [did for dlist in drop_ids_by_artist.values() for did in dlist]
        actions = ["play", "play", "play", "save", "share"]

        for fan_id in fan_ids:
            sampled = random.sample(all_drop_ids, min(8, len(all_drop_ids)))
            for drop_id in sampled:
                action = random.choice(actions)
                conn.execute(
                    "INSERT INTO drop_engagement (user_id, drop_id, action, metadata) VALUES (?, ?, ?, '{}')",
                    (fan_id, drop_id, action),
                )
                result["engagements"] += 1

        conn.commit()

    finally:
        conn.close()

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("🌱 Seeding BLK MRKT demo data...")
    res = run_seed()
    print(f"  ✅ Scenes:      {res['scenes']}")
    print(f"  ✅ Artists:     {res['artists']}")
    print(f"  ✅ Fans:        {res['fans']}")
    print(f"  ✅ Drops:       {res['drops']}")
    print(f"  ✅ Follows:     {res['follows']}")
    print(f"  ✅ Engagements: {res['engagements']}")
    print("\n  Demo login: nova@blkmrkt.com / blkmrkt2025")
    print("  Admin login: admin@blkmrkt.com / blkmrkt2025")
