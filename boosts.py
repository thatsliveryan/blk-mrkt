"""
BLK MRKT — Boost System (Revenue Stream 1 — Artist-Paid Promotion)

Artists pay to amplify live drops in the discovery feed.
CRITICAL RULE: Drop must have velocity > 0 before boosting. Artists cannot
boost dead content. This protects feed quality (Prove It gate).

Boost tiers: Spark ($5/24h), Fire ($25/48h), Inferno ($100/72h)
Boost multiplier: 1.5x – 3x velocity depending on remaining budget.

Routes:
  POST /api/boosts              — create boost + return Stripe checkout URL
  GET  /api/boosts/my           — artist's boosts with performance stats
  GET  /api/boosts/tiers        — available tiers and pricing
  GET  /api/boosts/:id          — single boost detail
  POST /api/boosts/:id/cancel   — cancel an active boost
"""

import json
from datetime import datetime, timezone, timedelta

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list
from engine import calc_velocity

boosts_bp = Blueprint("boosts", prefix="/api/boosts")

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

BOOST_TIERS = {
    "spark":   {"budget_cents": 500,   "label": "Spark",   "duration_hours": 24, "multiplier": 1.5},
    "fire":    {"budget_cents": 2500,  "label": "Fire",    "duration_hours": 48, "multiplier": 2.0},
    "inferno": {"budget_cents": 10000, "label": "Inferno", "duration_hours": 72, "multiplier": 3.0},
}

# Minimum velocity score required to activate a boost (Prove It gate)
BOOST_MIN_VELOCITY = 0.1


def boost_multiplier_for_drop(drop_id):
    """
    Return the velocity multiplier for a drop if it has an active boost.
    Called by the discovery feed to rank boosted drops higher.
    """
    conn = get_db()
    try:
        boost = row_to_dict(conn.execute(
            """SELECT b.*, bt.multiplier
               FROM boosts b
               WHERE b.drop_id = ? AND b.status = 'active'
               ORDER BY b.budget_cents DESC LIMIT 1""",
            (drop_id,)
        ).fetchone())
    finally:
        conn.close()

    if not boost:
        return 1.0

    # Check if boost has expired by time
    if boost.get("expires_at"):
        try:
            exp = datetime.fromisoformat(boost["expires_at"].replace("Z", "+00:00"))
            if datetime.now(timezone.utc) > exp:
                # Expire it
                _expire_boost(boost["id"], boost["drop_id"])
                return 1.0
        except Exception:
            pass

    # Multiplier from tier definition
    tier_key = next((k for k, t in BOOST_TIERS.items() if t["budget_cents"] == boost["budget_cents"]), None)
    return BOOST_TIERS.get(tier_key, {}).get("multiplier", 1.5) if tier_key else 1.5


def _expire_boost(boost_id, drop_id):
    """Mark a boost as completed and remove the boost flag from its drop."""
    conn = get_db()
    try:
        conn.execute("UPDATE boosts SET status = 'completed' WHERE id = ?", (boost_id,))
        # Only clear flag if no other active boosts exist for this drop
        remaining = conn.execute(
            "SELECT COUNT(*) as c FROM boosts WHERE drop_id = ? AND status = 'active'", (drop_id,)
        ).fetchone()["c"]
        if remaining == 0:
            conn.execute("UPDATE drops SET boost_active = 0 WHERE id = ?", (drop_id,))
        conn.commit()
    finally:
        conn.close()


def expire_stale_boosts():
    """Called periodically to expire time-based boosts. Hook into drop state transitions."""
    now = utcnow()
    conn = get_db()
    try:
        expired = rows_to_list(conn.execute(
            "SELECT id, drop_id FROM boosts WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,)
        ).fetchall())
        for b in expired:
            conn.execute("UPDATE boosts SET status = 'completed' WHERE id = ?", (b["id"],))
            remaining = conn.execute(
                "SELECT COUNT(*) as c FROM boosts WHERE drop_id = ? AND status = 'active'", (b["drop_id"],)
            ).fetchone()["c"]
            if remaining == 0:
                conn.execute("UPDATE drops SET boost_active = 0 WHERE id = ?", (b["drop_id"],))
        if expired:
            conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@boosts_bp.route("/tiers", methods=["GET"])
def list_tiers(req):
    """Public — return available boost tiers and pricing."""
    return jsonify({"tiers": BOOST_TIERS})


@boosts_bp.route("", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def create_boost(req):
    """Create a boost. Returns Stripe checkout URL (or activates immediately in dev)."""
    data = req.get_json(silent=True) or {}
    drop_id      = (data.get("drop_id") or "").strip()
    tier_name    = data.get("tier", "spark")
    target_city  = (data.get("target_city") or "").strip() or None
    target_scene = (data.get("target_scene_id") or "").strip() or None
    duration_override = data.get("duration_hours")

    if not drop_id:
        return jsonify({"error": "drop_id is required"}, 400), 400
    if tier_name not in BOOST_TIERS:
        return jsonify({"error": f"tier must be one of: {', '.join(BOOST_TIERS)}"}, 400), 400

    tier = BOOST_TIERS[tier_name]
    duration_hours = int(duration_override) if duration_override else tier["duration_hours"]
    budget_cents = tier["budget_cents"]

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute(
            "SELECT id, artist_id, status, starts_at, title FROM drops WHERE id = ?", (drop_id,)
        ).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        if drop["artist_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "You can only boost your own drops"}, 403), 403
        if drop["status"] != "live":
            return jsonify({"error": "Only live drops can be boosted"}, 400), 400

        # Prove It gate
        velocity = calc_velocity(drop_id, drop["starts_at"])
        if velocity < BOOST_MIN_VELOCITY and srv.g.user_role != "admin":
            return jsonify({
                "error": "TRACTION_REQUIRED",
                "message": (
                    f"This drop needs real organic engagement before it can be boosted. "
                    f"Current velocity: {velocity}. Get some plays, saves, or shares first."
                ),
                "velocity": velocity,
                "required": BOOST_MIN_VELOCITY,
            }, 402), 402

        # Block duplicate active boost
        existing = row_to_dict(conn.execute(
            "SELECT id FROM boosts WHERE drop_id = ? AND status = 'active'", (drop_id,)
        ).fetchone())
        if existing:
            return jsonify({"error": "This drop already has an active boost"}, 409), 409

        boost_id = new_id()
        conn.execute(
            """INSERT INTO boosts
               (id, drop_id, artist_id, budget_cents, target_city, target_scene_id,
                duration_hours, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
            (boost_id, drop_id, srv.g.user_id, budget_cents, target_city, target_scene, duration_hours)
        )
        conn.commit()

        artist = row_to_dict(conn.execute(
            "SELECT id, username FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
    finally:
        conn.close()

    # Create Stripe checkout
    from config import STRIPE_SECRET_KEY
    if STRIPE_SECRET_KEY:
        from payments import create_boost_checkout_session
        checkout_url, err = create_boost_checkout_session(
            boost_id, drop["title"], budget_cents, artist
        )
        if err:
            return jsonify({"error": f"Stripe error: {err}"}, 502), 502
        return jsonify({
            "boost_id": boost_id,
            "checkout_url": checkout_url,
            "tier": tier_name,
            "budget_cents": budget_cents,
            "duration_hours": duration_hours,
        }, 201), 201
    else:
        # Dev mode — activate immediately without Stripe
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(hours=duration_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        started_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = get_db()
        try:
            conn.execute(
                "UPDATE boosts SET status = 'active', started_at = ?, expires_at = ? WHERE id = ?",
                (started_at, expires_at, boost_id)
            )
            conn.execute("UPDATE drops SET boost_active = 1 WHERE id = ?", (drop_id,))
            conn.commit()
        finally:
            conn.close()
        return jsonify({
            "boost_id": boost_id,
            "checkout_url": None,
            "message": "Boost activated (dev mode — no Stripe configured)",
            "status": "active",
            "tier": tier_name,
            "expires_at": expires_at,
        }, 201), 201


@boosts_bp.route("/my", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def my_boosts(req):
    """Artist's boost history with performance stats."""
    expire_stale_boosts()
    conn = get_db()
    try:
        boosts = rows_to_list(conn.execute(
            """SELECT b.*, d.title as drop_title, d.status as drop_status,
                      d.starts_at as drop_starts_at
               FROM boosts b JOIN drops d ON b.drop_id = d.id
               WHERE b.artist_id = ?
               ORDER BY b.created_at DESC LIMIT 50""",
            (srv.g.user_id,)
        ).fetchall())

        # Annotate with tier name
        for b in boosts:
            for tname, t in BOOST_TIERS.items():
                if t["budget_cents"] == b["budget_cents"]:
                    b["tier_name"] = tname
                    b["tier_label"] = t["label"]
                    b["multiplier"] = t["multiplier"]
                    break
    finally:
        conn.close()

    return jsonify({"boosts": boosts, "tiers": BOOST_TIERS})


@boosts_bp.route("/:boost_id", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def get_boost(req, boost_id):
    """Single boost detail with performance stats."""
    conn = get_db()
    try:
        boost = row_to_dict(conn.execute(
            """SELECT b.*, d.title as drop_title, d.status as drop_status
               FROM boosts b JOIN drops d ON b.drop_id = d.id
               WHERE b.id = ?""",
            (boost_id,)
        ).fetchone())
    finally:
        conn.close()

    if not boost:
        return jsonify({"error": "Boost not found"}, 404), 404
    if boost["artist_id"] != srv.g.user_id and srv.g.user_role != "admin":
        return jsonify({"error": "Forbidden"}, 403), 403

    return jsonify({"boost": boost, "tiers": BOOST_TIERS})


@boosts_bp.route("/:boost_id/cancel", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def cancel_boost(req, boost_id):
    """Cancel an active or pending boost."""
    conn = get_db()
    try:
        boost = row_to_dict(conn.execute(
            "SELECT * FROM boosts WHERE id = ?", (boost_id,)
        ).fetchone())
        if not boost:
            return jsonify({"error": "Boost not found"}, 404), 404
        if boost["artist_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Forbidden"}, 403), 403
        if boost["status"] not in ("active", "pending"):
            return jsonify({"error": "Boost cannot be cancelled in its current state"}, 400), 400

        conn.execute("UPDATE boosts SET status = 'cancelled' WHERE id = ?", (boost_id,))
        remaining = conn.execute(
            "SELECT COUNT(*) as c FROM boosts WHERE drop_id = ? AND status = 'active'",
            (boost["drop_id"],)
        ).fetchone()["c"]
        if remaining == 0:
            conn.execute("UPDATE drops SET boost_active = 0 WHERE id = ?", (boost["drop_id"],))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Boost cancelled", "boost_id": boost_id})
