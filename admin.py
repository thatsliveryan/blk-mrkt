"""
BLK MRKT — Admin module (stdlib version)
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, row_to_dict

admin_bp = Blueprint("admin", prefix="/api/admin")


@admin_bp.route("/stats", methods=["GET"])
@require_auth
@require_role("admin")
def stats(req):
    conn = get_db()
    try:
        s = row_to_dict(conn.execute(
            """SELECT
                (SELECT COUNT(*) FROM users) as total_users,
                (SELECT COUNT(*) FROM users WHERE role = 'artist') as total_artists,
                (SELECT COUNT(*) FROM users WHERE role = 'fan') as total_fans,
                (SELECT COUNT(*) FROM drops) as total_drops,
                (SELECT COUNT(*) FROM drops WHERE status = 'live') as live_drops,
                (SELECT COUNT(*) FROM drop_access) as total_claims,
                (SELECT COUNT(*) FROM drop_engagement) as total_engagements,
                (SELECT COALESCE(SUM(price_paid), 0) FROM drop_access) as total_revenue"""
        ).fetchone())
    finally:
        conn.close()
    return jsonify({"stats": s})


@admin_bp.route("/drops/:drop_id/status", methods=["PATCH"])
@require_auth
@require_role("admin")
def force_status(req, drop_id):
    data = req.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("live", "locked", "expired"):
        return jsonify({"error": "Status must be 'live', 'locked', or 'expired'"}, 400), 400

    conn = get_db()
    try:
        drop = conn.execute("SELECT id FROM drops WHERE id = ?", (drop_id,)).fetchone()
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        conn.execute("UPDATE drops SET status = ? WHERE id = ?", (new_status, drop_id))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Drop status set to {new_status}", "drop_id": drop_id})
