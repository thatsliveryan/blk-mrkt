"""
BLK MRKT — Core Engine
Drop lifecycle manager + engagement velocity scoring.
"""

from datetime import datetime, timezone
from models import get_db, rows_to_list, row_to_dict

# ---------------------------------------------------------------------------
# Velocity cache — simple in-memory TTL cache
# ---------------------------------------------------------------------------
_velocity_cache = {}  # drop_id → (score, timestamp)
VELOCITY_TTL = 300    # 5 minutes


def _parse_ts(ts_str):
    """Parse an ISO timestamp string to a UTC datetime."""
    if not ts_str:
        return None
    # Handle both with and without Z suffix
    ts_str = ts_str.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts_str).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _hours_since(ts_str):
    """Hours elapsed since a given ISO timestamp. Minimum 0.1 to avoid division by zero."""
    dt = _parse_ts(ts_str)
    if not dt:
        return 1.0
    delta = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return max(delta, 0.1)


# ---------------------------------------------------------------------------
# Drop Lifecycle
# ---------------------------------------------------------------------------

def transition_drop_states():
    """
    Check all drops and transition:
      scheduled → live   (when starts_at <= now)
      live → expired     (when expires_at <= now OR remaining_supply <= 0)
    Returns count of transitions made.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    transitions = 0

    try:
        # scheduled → live
        cur = conn.execute(
            "UPDATE drops SET status = 'live' WHERE status = 'scheduled' AND starts_at <= ?",
            (now,),
        )
        transitions += cur.rowcount

        # live → expired (time-based)
        cur = conn.execute(
            "UPDATE drops SET status = 'expired' WHERE status = 'live' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        transitions += cur.rowcount

        # live → expired (supply-based)
        cur = conn.execute(
            "UPDATE drops SET status = 'expired' WHERE status = 'live' AND total_supply IS NOT NULL AND remaining_supply <= 0",
        )
        transitions += cur.rowcount

        conn.commit()
    finally:
        conn.close()

    return transitions


def get_drop_status_info(drop):
    """Add computed fields to a drop dict: countdown_seconds, is_sold_out, supply_pct."""
    if not drop:
        return drop

    now = datetime.now(timezone.utc)

    # Countdown
    if drop.get("expires_at"):
        exp = _parse_ts(drop["expires_at"])
        if exp and exp > now:
            drop["countdown_seconds"] = int((exp - now).total_seconds())
        else:
            drop["countdown_seconds"] = 0
    else:
        drop["countdown_seconds"] = None

    # Supply info
    if drop.get("total_supply") is not None:
        drop["is_sold_out"] = drop["remaining_supply"] <= 0
        drop["supply_pct"] = round(
            ((drop["total_supply"] - drop["remaining_supply"]) / drop["total_supply"]) * 100, 1
        ) if drop["total_supply"] > 0 else 100
    else:
        drop["is_sold_out"] = False
        drop["supply_pct"] = 0

    return drop


# ---------------------------------------------------------------------------
# Engagement Velocity
# ---------------------------------------------------------------------------

def calc_velocity(drop_id, starts_at):
    """
    velocity = (plays*1 + replays*2 + saves*3 + shares*5 + profile_clicks*2) / hours_since_drop
    Uses 5-min TTL cache.
    """
    import time as _time
    now = _time.time()

    cached = _velocity_cache.get(drop_id)
    if cached and (now - cached[1]) < VELOCITY_TTL:
        return cached[0]

    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT action, COUNT(*) as cnt
            FROM drop_engagement
            WHERE drop_id = ?
            GROUP BY action
            """,
            (drop_id,),
        ).fetchall()
    finally:
        conn.close()

    weights = {"play": 1, "replay": 2, "save": 3, "share": 5, "profile_click": 2}
    score = 0
    total_engagements = 0
    for r in rows:
        w = weights.get(r["action"], 1)
        score += r["cnt"] * w
        total_engagements += r["cnt"]

    hours = _hours_since(starts_at)
    velocity = round(score / hours, 2) if hours > 0 else 0

    _velocity_cache[drop_id] = (velocity, now)

    return velocity


def calc_velocity_bulk(drop_ids_starts):
    """Calculate velocity for multiple drops efficiently.
    drop_ids_starts: list of (drop_id, starts_at) tuples.
    Returns dict of drop_id → velocity.
    """
    if not drop_ids_starts:
        return {}

    import time as _time
    now = _time.time()

    results = {}
    uncached = []

    for drop_id, starts_at in drop_ids_starts:
        cached = _velocity_cache.get(drop_id)
        if cached and (now - cached[1]) < VELOCITY_TTL:
            results[drop_id] = cached[0]
        else:
            uncached.append((drop_id, starts_at))

    if not uncached:
        return results

    conn = get_db()
    try:
        placeholders = ",".join("?" for _ in uncached)
        ids = [d[0] for d in uncached]
        rows = conn.execute(
            f"""
            SELECT drop_id, action, COUNT(*) as cnt
            FROM drop_engagement
            WHERE drop_id IN ({placeholders})
            GROUP BY drop_id, action
            """,
            ids,
        ).fetchall()
    finally:
        conn.close()

    weights = {"play": 1, "replay": 2, "save": 3, "share": 5, "profile_click": 2}
    scores = {}
    for r in rows:
        did = r["drop_id"]
        scores[did] = scores.get(did, 0) + r["cnt"] * weights.get(r["action"], 1)

    starts_map = {d[0]: d[1] for d in uncached}
    for drop_id in [d[0] for d in uncached]:
        score = scores.get(drop_id, 0)
        hours = _hours_since(starts_map[drop_id])
        vel = round(score / hours, 2) if hours > 0 else 0
        results[drop_id] = vel
        _velocity_cache[drop_id] = (vel, now)

    return results


def get_engagement_stats(drop_id):
    """Get engagement breakdown for a single drop."""
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT action, COUNT(*) as cnt FROM drop_engagement WHERE drop_id = ? GROUP BY action",
            (drop_id,),
        ).fetchall()
    finally:
        conn.close()

    stats = {"plays": 0, "replays": 0, "saves": 0, "shares": 0, "profile_clicks": 0}
    for r in rows:
        key = r["action"] + "s" if r["action"] != "profile_click" else "profile_clicks"
        if r["action"] == "play":
            key = "plays"
        elif r["action"] == "replay":
            key = "replays"
        elif r["action"] == "save":
            key = "saves"
        elif r["action"] == "share":
            key = "shares"
        elif r["action"] == "profile_click":
            key = "profile_clicks"
        stats[key] = r["cnt"]

    return stats
