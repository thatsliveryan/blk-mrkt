"""
BLK MRKT — Boost System (Revenue Stream 1)

Artists pay to amplify their drops. Boosts only scale content that already
has real organic engagement — you can't buy your way to trending with zero
real traction. Minimum velocity score required to activate a boost.
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list
from engine import calc_velocity

boosts_bp = Blueprint("boosts", prefix="/api/boosts")

# Minimum velocity score required before a drop can be boosted
BOOST_MIN_VELOCITY = 0.5

BOOST_TIERS = {
    "spark":    {"price": 5,   "label": "Spark",    "duration_hours": 6},
    "fire":     {"price": 25,  "label": "Fire",     "duration_hours": 24},
    "inferno":  {"price": 100, "label": "Inferno",  "duration_hours": 72},
}


@boosts_bp.route("", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def create_boost(req):
    """Create a boost for one of the artist's drops."""
    data = req.get_json(silent=True) or {}
    drop_id     = data.get("drop_id", "").strip()
    tier_name   = data.get("tier", "spark")
    target_city = data.get("target_city", "").strip() or None
    target_scene = data.get("target_scene_id", "").strip() or None

    if not drop_id:
        return jsonify({"error": "drop_id is required"}, 400), 400
    if tier_name not in BOOST_TIERS:
        return jsonify({"error": f"tier must be one of: {', '.join(BOOST_TIERS)}"}, 400), 400

    tier = BOOST_TIERS[tier_name]

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute(
            "SELECT id, artist_id, status, starts_at, title FROM drops WHERE id = ?",
            (drop_id,)
        ).fetchone())

        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        if drop["artist_id"] != srv.g.user_id and getattr(srv.g, "role", "") != "admin":
            return jsonify({"error": "You can only boost your own drops"}, 403), 403
        if drop["status"] != "live":
            return jsonify({"error": "Only live drops can be boosted"}, 400), 400

        # Prove It gate — must have real organic traction
        velocity = calc_velocity(drop_id, drop["starts_at"])
        if velocity < BOOST_MIN_VELOCITY and getattr(srv.g, "role", "") != "admin":
            return jsonify({
                "error": "TRACTION_REQUIRED",
                "message": (
                    f"This drop needs a velocity score of {BOOST_MIN_VELOCITY}+ before it can be boosted. "
                    f"Current score: {velocity}. Get real engagement first."
                ),
                "velocity": velocity,
                "required": BOOST_MIN_VELOCITY,
            }, 402), 402

        # Check for existing active boost on this drop
        existing = row_to_dict(conn.execute(
            "SELECT id FROM boosts WHERE drop_id = ? AND status = 'active'",
            (drop_id,)
        ).fetchone())
        if existing:
            return jsonify({"error": "This drop already has an active boost"}, 409), 409

        boost_id = new_id()
        conn.execute(
            """INSERT INTO boosts (id, drop_id, artist_id, budget, target_city, target_scene_id, status)
               VALUES (?, ?, ?, ?, ?, ?, 'active')""",
            (boost_id, drop_id, srv.g.user_id, tier["price"], target_city, target_scene)
        )
        # Mark the drop as boosted
        conn.execute("UPDATE drops SET boost_active = 1 WHERE id = ?", (drop_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "boost": {
            "id": boost_id,
            "drop_id": drop_id,
            "tier": tier_name,
            "budget": tier["price"],
            "target_city": target_city,
            "target_scene_id": target_scene,
            "status": "active",
            "message": (
                f"Boost activated! Your drop '{drop['title']}' is now being amplified "
                f"for up to {tier['duration_hours']} hours."
            )
        }
    }, 201), 201


@boosts_bp.route("/my", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def my_boosts(req):
    """List all boosts for the current artist."""
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
    finally:
        conn.close()

    return jsonify({"boosts": boosts, "tiers": BOOST_TIERS})


@boosts_bp.route("/:boost_id", methods=["PATCH"])
@require_auth
@require_role("artist", "admin")
def update_boost(req, boost_id):
    """Pause or cancel a boost."""
    data = req.get_json(silent=True) or {}
    status = data.get("status")
    if status not in ("paused", "exhausted"):
        return jsonify({"error": "status must be 'paused' or 'exhausted'"}, 400), 400

    conn = get_db()
    try:
        boost = row_to_dict(conn.execute(
            "SELECT * FROM boosts WHERE id = ?", (boost_id,)
        ).fetchone())
        if not boost:
            return jsonify({"error": "Boost not found"}, 404), 404
        if boost["artist_id"] != srv.g.user_id and getattr(srv.g, "role", "") != "admin":
            return jsonify({"error": "Forbidden"}, 403), 403

        conn.execute("UPDATE boosts SET status = ? WHERE id = ?", (status, boost_id))
        if status in ("paused", "exhausted"):
            conn.execute("UPDATE drops SET boost_active = 0 WHERE id = ?", (boost["drop_id"],))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": f"Boost {status}", "boost_id": boost_id})


@boosts_bp.route("/tiers", methods=["GET"])
def list_tiers(req):
    """Return available boost tier pricing."""
    return jsonify({"tiers": BOOST_TIERS})
