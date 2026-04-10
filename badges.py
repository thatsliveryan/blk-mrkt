"""
BLK MRKT — Fan Badge System (System 5)

Badges are automatically awarded after claims. Six badge types:
  - first_10        : Fan claimed a drop in the first 10 fans
  - first_100       : Fan claimed a drop in the first 100 fans
  - early_listener  : Claimed a drop within 1 hour of it going live
  - drop_hunter     : Claimed 10+ unique drops total
  - scene_regular   : Claimed 5+ drops from the same scene
  - sold_out_witness: Claimed a drop that later sold out

Badges are idempotent (UNIQUE constraint on user_id, badge_type, badge_data).

Routes:
  GET /api/badges                 — current user's badges
  GET /api/badges/user/:user_id   — public badge display for any user
  POST /api/badges/check          — manually trigger badge check (debug/admin)
"""

import json
from datetime import datetime, timezone

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list

badges_bp = Blueprint("badges", prefix="/api/badges")


# ---------------------------------------------------------------------------
# Badge definitions
# ---------------------------------------------------------------------------

BADGE_DEFINITIONS = {
    "first_10": {
        "label": "First 10",
        "emoji": "🔟",
        "description": "One of the first 10 fans to claim a drop.",
        "color": "#FFD700",
    },
    "first_100": {
        "label": "First 100",
        "emoji": "💯",
        "description": "One of the first 100 fans to claim a drop.",
        "color": "#C0C0C0",
    },
    "early_listener": {
        "label": "Early Listener",
        "emoji": "⚡",
        "description": "Claimed a drop within 1 hour of it going live.",
        "color": "#FF4444",
    },
    "drop_hunter": {
        "label": "Drop Hunter",
        "emoji": "🎯",
        "description": "Claimed 10 or more unique drops.",
        "color": "#44BBFF",
    },
    "scene_regular": {
        "label": "Scene Regular",
        "emoji": "🎪",
        "description": "Claimed 5+ drops from the same scene.",
        "color": "#AA44FF",
    },
    "sold_out_witness": {
        "label": "Sold Out Witness",
        "emoji": "🏆",
        "description": "Claimed a drop that completely sold out.",
        "color": "#FF8800",
    },
}


def _award_badge(conn, user_id, badge_type, badge_data=None):
    """
    Award a badge if the user doesn't already have it.
    badge_data is extra context (e.g. drop_id, scene_id).
    Returns True if badge was newly awarded.
    """
    data_str = json.dumps(badge_data or {}, sort_keys=True)
    try:
        conn.execute(
            """INSERT OR IGNORE INTO badges (id, user_id, badge_type, badge_data, earned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (new_id(), user_id, badge_type, data_str, utcnow())
        )
        return conn.execute(
            "SELECT changes() as c"
        ).fetchone()["c"] > 0
    except Exception:
        return False


def check_and_award_badges(user_id, drop_id, conn=None):
    """
    Run all badge checks for a user after they claim a drop.
    Called from payments.py webhook and drops.py claim_access.
    conn: pass an open connection for in-transaction calls, or None to open one.
    """
    close_conn = conn is None
    if close_conn:
        conn = get_db()

    try:
        # Get the claim record
        access = row_to_dict(conn.execute(
            """SELECT da.*, d.starts_at, d.total_supply, d.remaining_supply, d.status as drop_status
               FROM drop_access da JOIN drops d ON da.drop_id = d.id
               WHERE da.user_id = ? AND da.drop_id = ?
               ORDER BY da.acquired_at DESC LIMIT 1""",
            (user_id, drop_id)
        ).fetchone())

        if not access:
            return []

        awarded = []
        fan_number = access.get("fan_number", 999)

        # Badge: First 10
        if fan_number and fan_number <= 10:
            if _award_badge(conn, user_id, "first_10", {"drop_id": drop_id, "fan_number": fan_number}):
                awarded.append("first_10")

        # Badge: First 100
        if fan_number and fan_number <= 100:
            if _award_badge(conn, user_id, "first_100", {"drop_id": drop_id, "fan_number": fan_number}):
                awarded.append("first_100")

        # Badge: Early Listener — claimed within 1 hour of drop going live
        if access.get("starts_at") and access.get("acquired_at"):
            try:
                starts = datetime.fromisoformat(access["starts_at"].replace("Z", "+00:00"))
                acquired = datetime.fromisoformat(access["acquired_at"].replace("Z", "+00:00"))
                diff_hours = (acquired - starts).total_seconds() / 3600
                if 0 <= diff_hours <= 1:
                    if _award_badge(conn, user_id, "early_listener", {"drop_id": drop_id}):
                        awarded.append("early_listener")
            except Exception:
                pass

        # Badge: Drop Hunter — user has claimed 10+ unique drops
        total_claims = conn.execute(
            "SELECT COUNT(DISTINCT drop_id) as c FROM drop_access WHERE user_id = ?",
            (user_id,)
        ).fetchone()["c"]
        if total_claims >= 10:
            if _award_badge(conn, user_id, "drop_hunter", {}):
                awarded.append("drop_hunter")

        # Badge: Scene Regular — 5+ claims from same scene
        scene_counts = rows_to_list(conn.execute(
            """SELECT ds.scene_id, COUNT(*) as cnt
               FROM drop_access da
               JOIN drop_scenes ds ON da.drop_id = ds.drop_id
               WHERE da.user_id = ?
               GROUP BY ds.scene_id
               HAVING cnt >= 5""",
            (user_id,)
        ).fetchall())
        for sc in scene_counts:
            if _award_badge(conn, user_id, "scene_regular", {"scene_id": sc["scene_id"]}):
                awarded.append("scene_regular")

        # Badge: Sold Out Witness — claimed a drop that is now sold out (or expired due to supply)
        is_sold_out = (
            access.get("drop_status") == "expired" and
            access.get("total_supply") is not None and
            (access.get("remaining_supply") or 0) <= 0
        )
        # Also check current drop state in case it sold out since claim
        if not is_sold_out:
            current_drop = row_to_dict(conn.execute(
                "SELECT status, total_supply, remaining_supply FROM drops WHERE id = ?",
                (drop_id,)
            ).fetchone())
            if current_drop:
                is_sold_out = (
                    current_drop["status"] == "expired" and
                    current_drop["total_supply"] is not None and
                    (current_drop["remaining_supply"] or 0) <= 0
                )
        if is_sold_out:
            if _award_badge(conn, user_id, "sold_out_witness", {"drop_id": drop_id}):
                awarded.append("sold_out_witness")

        if awarded or close_conn:
            conn.commit()

        return awarded

    finally:
        if close_conn:
            conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@badges_bp.route("", methods=["GET"])
@require_auth
def my_badges(req):
    """Return current user's badges with full definitions."""
    conn = get_db()
    try:
        rows = rows_to_list(conn.execute(
            """SELECT * FROM badges WHERE user_id = ? ORDER BY earned_at DESC""",
            (srv.g.user_id,)
        ).fetchall())
    finally:
        conn.close()

    badges = []
    for row in rows:
        badge_type = row["badge_type"]
        definition = BADGE_DEFINITIONS.get(badge_type, {})
        try:
            badge_data = json.loads(row.get("badge_data", "{}"))
        except Exception:
            badge_data = {}
        badges.append({
            **row,
            "badge_data": badge_data,
            "label": definition.get("label", badge_type),
            "emoji": definition.get("emoji", "🏅"),
            "description": definition.get("description", ""),
            "color": definition.get("color", "#666"),
        })

    return jsonify({
        "badges": badges,
        "count": len(badges),
        "definitions": BADGE_DEFINITIONS,
    })


@badges_bp.route("/user/:user_id", methods=["GET"])
def user_badges(req, user_id):
    """Public badge display for any user profile."""
    conn = get_db()
    try:
        rows = rows_to_list(conn.execute(
            """SELECT badge_type, badge_data, earned_at FROM badges
               WHERE user_id = ? ORDER BY earned_at DESC""",
            (user_id,)
        ).fetchall())
    finally:
        conn.close()

    badges = []
    for row in rows:
        badge_type = row["badge_type"]
        definition = BADGE_DEFINITIONS.get(badge_type, {})
        try:
            badge_data = json.loads(row.get("badge_data", "{}"))
        except Exception:
            badge_data = {}
        badges.append({
            **row,
            "badge_data": badge_data,
            "label": definition.get("label", badge_type),
            "emoji": definition.get("emoji", "🏅"),
            "description": definition.get("description", ""),
            "color": definition.get("color", "#666"),
        })

    return jsonify({
        "badges": badges,
        "count": len(badges),
    })


@badges_bp.route("/check", methods=["POST"])
@require_auth
def manual_check(req):
    """Manually trigger badge checks for a specific drop. Useful for backfilling."""
    data = req.get_json(silent=True) or {}
    drop_id = data.get("drop_id", "").strip()

    if not drop_id:
        return jsonify({"error": "drop_id required"}, 400), 400

    awarded = check_and_award_badges(srv.g.user_id, drop_id)
    return jsonify({
        "awarded": awarded,
        "count": len(awarded),
        "definitions": {k: BADGE_DEFINITIONS[k] for k in awarded if k in BADGE_DEFINITIONS},
    })


@badges_bp.route("/definitions", methods=["GET"])
def badge_definitions(req):
    """Public — return all badge definitions."""
    return jsonify({"definitions": BADGE_DEFINITIONS})
