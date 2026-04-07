"""
BLK MRKT — Artist Subscriptions (Revenue Stream 2)

Fans subscribe to artists for early access and exclusive content.
Tiers: basic ($2.99/mo), premium ($9.99/mo).
Platform takes 20% commission.
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list

subs_bp = Blueprint("subscriptions", prefix="/api/subscriptions")

PLATFORM_CUT = 0.20  # 20% commission

TIERS = {
    "basic":   {"price": 2.99,  "label": "Early Access",   "perks": ["Early access to drops", "Fan badge on profile"]},
    "premium": {"price": 9.99,  "label": "Day One",        "perks": ["Early access to drops", "Fan badge", "Private drops", "Direct message"]},
}


@subs_bp.route("", methods=["POST"])
@require_auth
def subscribe(req):
    """Fan subscribes to an artist."""
    data = req.get_json(silent=True) or {}
    artist_id = data.get("artist_id", "").strip()
    tier      = data.get("tier", "basic")

    if not artist_id:
        return jsonify({"error": "artist_id is required"}, 400), 400
    if tier not in TIERS:
        return jsonify({"error": f"tier must be one of: {', '.join(TIERS)}"}, 400), 400
    if artist_id == srv.g.user_id:
        return jsonify({"error": "You cannot subscribe to yourself"}, 400), 400

    price = TIERS[tier]["price"]

    conn = get_db()
    try:
        artist = row_to_dict(conn.execute(
            "SELECT id, username, role FROM users WHERE id = ? AND role = 'artist'",
            (artist_id,)
        ).fetchone())
        if not artist:
            return jsonify({"error": "Artist not found"}, 404), 404

        existing = row_to_dict(conn.execute(
            "SELECT * FROM artist_subscriptions WHERE fan_id = ? AND artist_id = ?",
            (srv.g.user_id, artist_id)
        ).fetchone())

        if existing:
            if existing["status"] == "active":
                return jsonify({"error": "Already subscribed", "subscription": existing}, 409), 409
            # Reactivate cancelled subscription
            conn.execute(
                "UPDATE artist_subscriptions SET status = 'active', tier = ?, price_monthly = ?, started_at = ?, cancelled_at = NULL WHERE fan_id = ? AND artist_id = ?",
                (tier, price, utcnow(), srv.g.user_id, artist_id)
            )
            conn.commit()
            return jsonify({"message": f"Resubscribed to {artist['username']}", "tier": tier, "price": price})

        sub_id = new_id()
        conn.execute(
            "INSERT INTO artist_subscriptions (id, fan_id, artist_id, tier, price_monthly) VALUES (?, ?, ?, ?, ?)",
            (sub_id, srv.g.user_id, artist_id, tier, price)
        )
        # Increment artist follower count
        conn.execute("UPDATE users SET follower_count = follower_count + 1 WHERE id = ?", (artist_id,))
        conn.commit()
    finally:
        conn.close()

    artist_cut = round(price * (1 - PLATFORM_CUT), 2)
    return jsonify({
        "subscription": {
            "id": sub_id,
            "artist_id": artist_id,
            "artist_username": artist["username"],
            "tier": tier,
            "tier_label": TIERS[tier]["label"],
            "price_monthly": price,
            "artist_earns": artist_cut,
            "perks": TIERS[tier]["perks"],
            "status": "active",
        }
    }, 201), 201


@subs_bp.route("", methods=["GET"])
@require_auth
def my_subscriptions(req):
    """Get current user's subscriptions (as fan) or subscribers (as artist)."""
    view = req.args.get("view", "fan")  # 'fan' = who I subscribe to, 'artist' = my subscribers

    conn = get_db()
    try:
        if view == "artist":
            rows = rows_to_list(conn.execute(
                """SELECT s.*, u.username as fan_username, u.avatar_url as fan_avatar
                   FROM artist_subscriptions s JOIN users u ON s.fan_id = u.id
                   WHERE s.artist_id = ? AND s.status = 'active'
                   ORDER BY s.started_at DESC""",
                (srv.g.user_id,)
            ).fetchall())
            total_monthly = sum(r["price_monthly"] * (1 - PLATFORM_CUT) for r in rows)
            return jsonify({
                "subscribers": rows,
                "count": len(rows),
                "monthly_revenue": round(total_monthly, 2),
            })
        else:
            rows = rows_to_list(conn.execute(
                """SELECT s.*, u.username as artist_username, u.avatar_url as artist_avatar,
                          u.city as artist_city, u.bio as artist_bio
                   FROM artist_subscriptions s JOIN users u ON s.artist_id = u.id
                   WHERE s.fan_id = ? AND s.status = 'active'
                   ORDER BY s.started_at DESC""",
                (srv.g.user_id,)
            ).fetchall())
            return jsonify({"subscriptions": rows, "count": len(rows), "tiers": TIERS})
    finally:
        conn.close()


@subs_bp.route("/:artist_id/cancel", methods=["POST"])
@require_auth
def cancel_subscription(req, artist_id):
    """Fan cancels subscription to an artist."""
    conn = get_db()
    try:
        cur = conn.execute(
            "UPDATE artist_subscriptions SET status = 'cancelled', cancelled_at = ? WHERE fan_id = ? AND artist_id = ? AND status = 'active'",
            (utcnow(), srv.g.user_id, artist_id)
        )
        if cur.rowcount == 0:
            return jsonify({"error": "No active subscription found"}, 404), 404
        conn.execute("UPDATE users SET follower_count = MAX(0, follower_count - 1) WHERE id = ?", (artist_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Subscription cancelled"})


@subs_bp.route("/check/:artist_id", methods=["GET"])
@require_auth
def check_subscription(req, artist_id):
    """Check if current user is subscribed to an artist."""
    conn = get_db()
    try:
        sub = row_to_dict(conn.execute(
            "SELECT * FROM artist_subscriptions WHERE fan_id = ? AND artist_id = ? AND status = 'active'",
            (srv.g.user_id, artist_id)
        ).fetchone())
    finally:
        conn.close()

    return jsonify({
        "subscribed": sub is not None,
        "subscription": sub,
        "tiers": TIERS,
    })


@subs_bp.route("/tiers", methods=["GET"])
def list_tiers(req):
    return jsonify({"tiers": TIERS, "platform_cut": PLATFORM_CUT})
