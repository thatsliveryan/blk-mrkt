"""
BLK MRKT — Follow System (System 4)

Fans follow artists. Following feed shows only drops from followed artists.
Toggle semantics: POST follow twice → unfollow.

Routes:
  POST /api/follow/:user_id          — toggle follow/unfollow
  GET  /api/follow/:user_id/status   — is the current user following them?
  GET  /api/following                — list of artists this user follows
  GET  /api/followers                — list of fans following this user
  GET  /api/feed/following           — drop feed from followed artists only
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth
from models import get_db, utcnow, row_to_dict, rows_to_list
from engine import transition_drop_states, get_drop_status_info, calc_velocity_bulk

follows_bp = Blueprint("follows", prefix="/api")


@follows_bp.route("/follow/:user_id", methods=["POST"])
@require_auth
def toggle_follow(req, user_id):
    """Toggle follow/unfollow for a user. Returns new follow state."""
    if user_id == srv.g.user_id:
        return jsonify({"error": "Cannot follow yourself"}, 400), 400

    conn = get_db()
    try:
        # Check the target user exists
        target = row_to_dict(conn.execute(
            "SELECT id, username, role FROM users WHERE id = ?", (user_id,)
        ).fetchone())
        if not target:
            return jsonify({"error": "User not found"}, 404), 404

        # Check existing follow
        existing = row_to_dict(conn.execute(
            "SELECT * FROM follows WHERE follower_id = ? AND following_id = ?",
            (srv.g.user_id, user_id)
        ).fetchone())

        if existing:
            # Unfollow
            conn.execute(
                "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
                (srv.g.user_id, user_id)
            )
            # Decrement follower_count (min 0)
            conn.execute(
                "UPDATE users SET follower_count = MAX(0, follower_count - 1) WHERE id = ?",
                (user_id,)
            )
            conn.commit()
            # Get updated count
            follower_count = conn.execute(
                "SELECT follower_count FROM users WHERE id = ?", (user_id,)
            ).fetchone()["follower_count"]
            return jsonify({
                "following": False,
                "target_user": target["username"],
                "follower_count": follower_count,
            })
        else:
            # Follow
            conn.execute(
                "INSERT OR IGNORE INTO follows (follower_id, following_id, created_at) VALUES (?, ?, ?)",
                (srv.g.user_id, user_id, utcnow())
            )
            conn.execute(
                "UPDATE users SET follower_count = follower_count + 1 WHERE id = ?",
                (user_id,)
            )
            conn.commit()
            follower_count = conn.execute(
                "SELECT follower_count FROM users WHERE id = ?", (user_id,)
            ).fetchone()["follower_count"]
            return jsonify({
                "following": True,
                "target_user": target["username"],
                "follower_count": follower_count,
            }, 201), 201
    finally:
        conn.close()


@follows_bp.route("/follow/:user_id/status", methods=["GET"])
@require_auth
def follow_status(req, user_id):
    """Check if the current user is following a specific user."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT created_at FROM follows WHERE follower_id = ? AND following_id = ?",
            (srv.g.user_id, user_id)
        ).fetchone()
        target = row_to_dict(conn.execute(
            "SELECT id, username, follower_count FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        conn.close()

    if not target:
        return jsonify({"error": "User not found"}, 404), 404

    return jsonify({
        "following": existing is not None,
        "since": existing["created_at"] if existing else None,
        "follower_count": target["follower_count"],
    })


@follows_bp.route("/following", methods=["GET"])
@require_auth
def my_following(req):
    """List of artists/users this user follows."""
    limit = min(int(req.args.get("limit", 50)), 200)
    offset = int(req.args.get("offset", 0))

    conn = get_db()
    try:
        users = rows_to_list(conn.execute(
            """SELECT u.id, u.username, u.role, u.city, u.bio, u.avatar_url,
                      u.follower_count, f.created_at as followed_at
               FROM follows f JOIN users u ON f.following_id = u.id
               WHERE f.follower_id = ?
               ORDER BY f.created_at DESC LIMIT ? OFFSET ?""",
            (srv.g.user_id, limit, offset)
        ).fetchall())
    finally:
        conn.close()

    return jsonify({"following": users, "count": len(users)})


@follows_bp.route("/followers", methods=["GET"])
@require_auth
def my_followers(req):
    """List of users following the current user."""
    limit = min(int(req.args.get("limit", 50)), 200)
    offset = int(req.args.get("offset", 0))

    conn = get_db()
    try:
        users = rows_to_list(conn.execute(
            """SELECT u.id, u.username, u.role, u.city, u.bio, u.avatar_url,
                      f.created_at as followed_at
               FROM follows f JOIN users u ON f.follower_id = u.id
               WHERE f.following_id = ?
               ORDER BY f.created_at DESC LIMIT ? OFFSET ?""",
            (srv.g.user_id, limit, offset)
        ).fetchall())
    finally:
        conn.close()

    return jsonify({"followers": users, "count": len(users)})


@follows_bp.route("/feed/following", methods=["GET"])
@require_auth
def following_feed(req):
    """Drop feed showing only drops from artists the user follows."""
    transition_drop_states()

    limit = min(int(req.args.get("limit", 50)), 100)
    offset = int(req.args.get("offset", 0))
    status_filter = req.args.get("status", "live")

    conn = get_db()
    try:
        # Get IDs of followed artists
        followed_ids = [r["following_id"] for r in conn.execute(
            "SELECT following_id FROM follows WHERE follower_id = ?",
            (srv.g.user_id,)
        ).fetchall()]

        if not followed_ids:
            return jsonify({
                "drops": [],
                "count": 0,
                "message": "Follow some artists to see their drops here.",
            })

        placeholders = ",".join("?" for _ in followed_ids)
        drops = rows_to_list(conn.execute(
            f"""SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar,
                       u.city as artist_city
                FROM drops d JOIN users u ON d.artist_id = u.id
                WHERE d.artist_id IN ({placeholders}) AND d.status = ?
                ORDER BY d.starts_at DESC LIMIT ? OFFSET ?""",
            (*followed_ids, status_filter, limit, offset)
        ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        d["velocity"] = velocities.get(d["id"], 0)
        get_drop_status_info(d)

    drops.sort(key=lambda d: d["velocity"], reverse=True)
    return jsonify({"drops": drops, "count": len(drops)})
