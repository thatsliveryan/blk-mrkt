"""
BLK MRKT — Users module (stdlib version)
"""

from server import Blueprint, jsonify
from models import get_db, row_to_dict, rows_to_list
from engine import calc_velocity_bulk, get_drop_status_info

users_bp = Blueprint("users", prefix="/api/users")


@users_bp.route("/:user_id/profile", methods=["GET"])
def profile(req, user_id):
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, username, role, city, bio, avatar_url, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone())
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        drops = []
        if user["role"] == "artist":
            drops = rows_to_list(conn.execute(
                """SELECT id, title, cover_image_path, drop_type, status,
                       total_supply, remaining_supply, starts_at, expires_at
                FROM drops WHERE artist_id = ? ORDER BY created_at DESC LIMIT 50""",
                (user_id,),
            ).fetchall())

        stats = row_to_dict(conn.execute(
            """SELECT
                (SELECT COUNT(*) FROM drops WHERE artist_id = ?) as total_drops,
                (SELECT COUNT(*) FROM drop_engagement de JOIN drops d ON de.drop_id = d.id WHERE d.artist_id = ? AND de.action = 'play') as total_plays,
                (SELECT COUNT(DISTINCT user_id) FROM drop_access da JOIN drops d ON da.drop_id = d.id WHERE d.artist_id = ?) as total_fans""",
            (user_id, user_id, user_id),
        ).fetchone())
    finally:
        conn.close()

    for d in drops:
        get_drop_status_info(d)

    user["drops"] = drops
    user["stats"] = stats
    return jsonify({"profile": user})


@users_bp.route("/:user_id/collection", methods=["GET"])
def collection(req, user_id):
    conn = get_db()
    try:
        user = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        items = rows_to_list(conn.execute(
            """SELECT da.access_type, da.acquired_at, da.price_paid,
                   d.id as drop_id, d.title, d.cover_image_path, d.artist_id,
                   d.drop_type, d.status, d.total_supply, d.remaining_supply,
                   u.username as artist_name
            FROM drop_access da JOIN drops d ON da.drop_id = d.id
            JOIN users u ON d.artist_id = u.id
            WHERE da.user_id = ? ORDER BY da.acquired_at DESC""",
            (user_id,),
        ).fetchall())
    finally:
        conn.close()
    return jsonify({"collection": items, "count": len(items)})
