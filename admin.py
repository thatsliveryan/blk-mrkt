"""
BLK MRKT — Admin business layer (stdlib version)

Routes:
  POST   /api/admin/seed                  - First-run admin creation (locked once admin exists)
  GET    /api/admin/stats                 - Platform-wide stats
  GET    /api/admin/users                 - List all users (paginated, filterable)
  GET    /api/admin/users/:id             - User detail + their drops & engagement
  PATCH  /api/admin/users/:id/role        - Promote / demote role
  PATCH  /api/admin/users/:id/suspend     - Suspend / unsuspend account
  DELETE /api/admin/users/:id             - Hard delete user + their content
  GET    /api/admin/drops                 - All drops with stats (paginated)
  PATCH  /api/admin/drops/:id/status      - Force drop status
  DELETE /api/admin/drops/:id             - Remove drop + all access records
  GET    /api/admin/revenue               - Revenue breakdown by artist
  GET    /api/admin/velocity              - Top drops by engagement velocity score
  GET    /api/admin/scenes                - All scenes
  DELETE /api/admin/scenes/:id            - Remove scene
"""

import bcrypt
from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role, create_token
from models import get_db, new_id, row_to_dict, rows_to_list

admin_bp = Blueprint("admin", prefix="/api/admin")

VALID_ROLES = ("artist", "fan", "curator", "admin")


# ---------------------------------------------------------------------------
# Seed — first-run admin creation (no auth required, self-disables)
# ---------------------------------------------------------------------------

@admin_bp.route("/seed", methods=["POST"])
def seed_admin(req):
    """Create root admin account. Only works if zero admin users exist."""
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT COUNT(*) as n FROM users WHERE role = 'admin'"
        ).fetchone()["n"]

        if existing > 0:
            return jsonify({"error": "Admin account already exists. Endpoint locked."}, 403), 403

        data = req.get_json(silent=True) or {}
        username = (data.get("username") or "").strip().lower()
        email = (data.get("email") or "").strip().lower()
        password = data.get("password") or ""

        if not username or not email or len(password) < 8:
            return jsonify({"error": "username, email, and password (8+ chars) required"}, 400), 400

        user_id = new_id()
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role, city) VALUES (?, ?, ?, ?, 'admin', ?)",
            (user_id, username, email, pw_hash, ""),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e):
            return jsonify({"error": "Username or email already taken"}, 409), 409
        return jsonify({"error": "Seed failed"}, 500), 500
    conn.close()

    access = create_token(user_id, "admin", "access")
    refresh = create_token(user_id, "admin", "refresh")

    return jsonify({
        "message": "Admin account created. This endpoint is now locked.",
        "user": {"id": user_id, "username": username, "email": email, "role": "admin"},
        "access_token": access,
        "refresh_token": refresh,
    }, 201), 201


# ---------------------------------------------------------------------------
# Platform stats
# ---------------------------------------------------------------------------

@admin_bp.route("/stats", methods=["GET"])
@require_auth
@require_role("admin")
def stats(req):
    conn = get_db()
    try:
        s = row_to_dict(conn.execute("""
            SELECT
                (SELECT COUNT(*) FROM users)                             AS total_users,
                (SELECT COUNT(*) FROM users WHERE role = 'artist')       AS total_artists,
                (SELECT COUNT(*) FROM users WHERE role = 'fan')          AS total_fans,
                (SELECT COUNT(*) FROM users WHERE role = 'curator')      AS total_curators,
                (SELECT COUNT(*) FROM drops)                             AS total_drops,
                (SELECT COUNT(*) FROM drops WHERE status = 'scheduled')  AS scheduled_drops,
                (SELECT COUNT(*) FROM drops WHERE status = 'live')       AS live_drops,
                (SELECT COUNT(*) FROM drops WHERE status = 'expired')    AS expired_drops,
                (SELECT COUNT(*) FROM drop_access)                       AS total_claims,
                (SELECT COUNT(*) FROM drop_access WHERE access_type = 'own') AS owned_drops,
                (SELECT COUNT(*) FROM drop_engagement)                   AS total_engagements,
                (SELECT COALESCE(SUM(price_paid), 0) FROM drop_access)  AS total_revenue,
                (SELECT COUNT(*) FROM scenes)                            AS total_scenes,
                (SELECT COUNT(*) FROM users WHERE created_at >= date('now', '-7 days')) AS new_users_7d,
                (SELECT COUNT(*) FROM drops WHERE created_at >= date('now', '-7 days'))  AS new_drops_7d
        """).fetchone())
    finally:
        conn.close()
    return jsonify({"stats": s})


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

@admin_bp.route("/users", methods=["GET"])
@require_auth
@require_role("admin")
def list_users(req):
    role_filter = req.query.get("role", "")
    search = req.query.get("q", "")
    page = max(1, int(req.query.get("page", 1)))
    per_page = min(100, max(10, int(req.query.get("per_page", 25))))
    offset = (page - 1) * per_page

    where_clauses = []
    params = []

    if role_filter and role_filter in VALID_ROLES:
        where_clauses.append("u.role = ?")
        params.append(role_filter)

    if search:
        where_clauses.append("(u.username LIKE ? OR u.email LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) as n FROM users u {where_sql}", params
        ).fetchone()["n"]

        rows = conn.execute(f"""
            SELECT
                u.id, u.username, u.email, u.role, u.city, u.created_at,
                COALESCE(u.bio, '')        AS bio,
                COALESCE(u.avatar_url, '') AS avatar_url,
                COUNT(DISTINCT d.id)       AS drop_count,
                COALESCE(SUM(da.price_paid), 0) AS revenue_generated
            FROM users u
            LEFT JOIN drops d  ON d.artist_id = u.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            {where_sql}
            GROUP BY u.id
            ORDER BY u.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()
    finally:
        conn.close()

    return jsonify({
        "users": rows_to_list(rows),
        "pagination": {
            "page": page, "per_page": per_page,
            "total": total, "pages": (total + per_page - 1) // per_page,
        },
    })


@admin_bp.route("/users/:user_id", methods=["GET"])
@require_auth
@require_role("admin")
def get_user(req, user_id):
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, username, email, role, city, bio, avatar_url, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone())

        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        drops = rows_to_list(conn.execute("""
            SELECT d.id, d.title, d.status, d.drop_type, d.total_supply, d.remaining_supply,
                   d.access_price, d.created_at,
                   COUNT(DISTINCT da.id)  AS claim_count,
                   COALESCE(SUM(da.price_paid), 0) AS revenue
            FROM drops d
            LEFT JOIN drop_access da ON da.drop_id = d.id
            WHERE d.artist_id = ?
            GROUP BY d.id
            ORDER BY d.created_at DESC
        """, (user_id,)).fetchall())

        engagement = row_to_dict(conn.execute("""
            SELECT
                SUM(CASE WHEN action = 'play'          THEN 1 ELSE 0 END) AS plays,
                SUM(CASE WHEN action = 'replay'        THEN 1 ELSE 0 END) AS replays,
                SUM(CASE WHEN action = 'save'          THEN 1 ELSE 0 END) AS saves,
                SUM(CASE WHEN action = 'share'         THEN 1 ELSE 0 END) AS shares,
                SUM(CASE WHEN action = 'profile_click' THEN 1 ELSE 0 END) AS profile_clicks
            FROM drop_engagement
            WHERE user_id = ?
        """, (user_id,)).fetchone())

        collection = rows_to_list(conn.execute("""
            SELECT da.drop_id, da.access_type, da.price_paid, da.acquired_at,
                   d.title, d.status
            FROM drop_access da
            JOIN drops d ON d.id = da.drop_id
            WHERE da.user_id = ?
            ORDER BY da.acquired_at DESC
        """, (user_id,)).fetchall())

    finally:
        conn.close()

    return jsonify({
        "user": user,
        "drops": drops,
        "engagement": engagement,
        "collection": collection,
    })


@admin_bp.route("/users/:user_id/role", methods=["PATCH"])
@require_auth
@require_role("admin")
def change_role(req, user_id):
    # Prevent self-demotion
    if user_id == srv.g.user_id:
        return jsonify({"error": "Cannot change your own role"}, 400), 400

    data = req.get_json(silent=True) or {}
    new_role = (data.get("role") or "").strip()

    if new_role not in VALID_ROLES:
        return jsonify({"error": f"Role must be one of: {', '.join(VALID_ROLES)}"}, 400), 400

    conn = get_db()
    try:
        cur = conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
        conn.commit()
    finally:
        conn.close()

    if cur.rowcount == 0:
        return jsonify({"error": "User not found"}, 404), 404

    return jsonify({"message": f"Role updated to '{new_role}'", "user_id": user_id, "role": new_role})


@admin_bp.route("/users/:user_id/suspend", methods=["PATCH"])
@require_auth
@require_role("admin")
def suspend_user(req, user_id):
    """Suspend sets role to 'suspended'; unsuspend restores to 'fan' unless original saved."""
    if user_id == srv.g.user_id:
        return jsonify({"error": "Cannot suspend yourself"}, 400), 400

    data = req.get_json(silent=True) or {}
    # Accept both "suspend":true and "suspended":true for client convenience
    suspend = bool(data.get("suspend", data.get("suspended", True)))

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone())

        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        if suspend:
            if user["role"] == "admin":
                return jsonify({"error": "Cannot suspend an admin"}, 403), 403
            new_role = user["role"]   # preserve original role
            action = "suspended"
            conn.execute("UPDATE users SET suspended = 1 WHERE id = ?", (user_id,))
        else:
            new_role = user["role"]
            action = "unsuspended"
            conn.execute("UPDATE users SET suspended = 0 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": f"User {action}", "user_id": user_id, "role": new_role, "suspended": suspend})


@admin_bp.route("/users/:user_id", methods=["DELETE"])
@require_auth
@require_role("admin")
def delete_user(req, user_id):
    if user_id == srv.g.user_id:
        return jsonify({"error": "Cannot delete yourself"}, 400), 400

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone())

        if not user:
            return jsonify({"error": "User not found"}, 404), 404
        if user["role"] == "admin":
            return jsonify({"error": "Cannot delete another admin account"}, 403), 403

        # Cascade: engagement → access → drop_scenes → drops → user
        conn.execute(
            "DELETE FROM drop_engagement WHERE user_id = ? OR drop_id IN (SELECT id FROM drops WHERE artist_id = ?)",
            (user_id, user_id),
        )
        conn.execute(
            "DELETE FROM drop_access WHERE user_id = ? OR drop_id IN (SELECT id FROM drops WHERE artist_id = ?)",
            (user_id, user_id),
        )
        conn.execute(
            "DELETE FROM drop_scenes WHERE drop_id IN (SELECT id FROM drops WHERE artist_id = ?)",
            (user_id,),
        )
        conn.execute("DELETE FROM drops WHERE artist_id = ?", (user_id,))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "User and all associated content deleted", "user_id": user_id})


# ---------------------------------------------------------------------------
# Drops
# ---------------------------------------------------------------------------

@admin_bp.route("/drops", methods=["GET"])
@require_auth
@require_role("admin")
def list_drops(req):
    status_filter = req.query.get("status", "")
    search = req.query.get("q", "")
    page = max(1, int(req.query.get("page", 1)))
    per_page = min(100, max(10, int(req.query.get("per_page", 25))))
    offset = (page - 1) * per_page

    where_clauses = []
    params = []

    valid_statuses = ("scheduled", "live", "locked", "expired")
    if status_filter and status_filter in valid_statuses:
        where_clauses.append("d.status = ?")
        params.append(status_filter)

    if search:
        where_clauses.append("(d.title LIKE ? OR u.username LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    conn = get_db()
    try:
        total = conn.execute(
            f"SELECT COUNT(*) as n FROM drops d JOIN users u ON u.id = d.artist_id {where_sql}",
            params,
        ).fetchone()["n"]

        rows = conn.execute(f"""
            SELECT
                d.id, d.title, d.status, d.drop_type,
                d.total_supply, d.remaining_supply,
                d.access_price, d.own_price,
                d.starts_at, d.expires_at, d.created_at,
                u.id AS artist_id, u.username AS artist_username,
                COUNT(DISTINCT da.id)           AS claim_count,
                COALESCE(SUM(da.price_paid), 0) AS revenue,
                COUNT(DISTINCT de.id)           AS engagement_count
            FROM drops d
            JOIN users u ON u.id = d.artist_id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            {where_sql}
            GROUP BY d.id
            ORDER BY d.created_at DESC
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()
    finally:
        conn.close()

    return jsonify({
        "drops": rows_to_list(rows),
        "pagination": {
            "page": page, "per_page": per_page,
            "total": total, "pages": (total + per_page - 1) // per_page,
        },
    })


@admin_bp.route("/drops/:drop_id/status", methods=["PATCH"])
@require_auth
@require_role("admin")
def force_status(req, drop_id):
    data = req.get_json(silent=True) or {}
    new_status = data.get("status")
    if new_status not in ("scheduled", "live", "locked", "expired"):
        return jsonify({"error": "Status must be: scheduled, live, locked, or expired"}, 400), 400

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT id FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        conn.execute("UPDATE drops SET status = ? WHERE id = ?", (new_status, drop_id))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": f"Drop status set to '{new_status}'", "drop_id": drop_id, "status": new_status})


@admin_bp.route("/drops/:drop_id", methods=["DELETE"])
@require_auth
@require_role("admin")
def delete_drop(req, drop_id):
    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT id, title FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404

        conn.execute("DELETE FROM drop_engagement WHERE drop_id = ?", (drop_id,))
        conn.execute("DELETE FROM drop_access WHERE drop_id = ?", (drop_id,))
        conn.execute("DELETE FROM drop_scenes WHERE drop_id = ?", (drop_id,))
        conn.execute("DELETE FROM drops WHERE id = ?", (drop_id,))
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": f"Drop '{drop['title']}' deleted", "drop_id": drop_id})


# ---------------------------------------------------------------------------
# Revenue
# ---------------------------------------------------------------------------

@admin_bp.route("/revenue", methods=["GET"])
@require_auth
@require_role("admin")
def revenue(req):
    conn = get_db()
    try:
        # Platform totals
        totals = row_to_dict(conn.execute("""
            SELECT
                COALESCE(SUM(price_paid), 0)                                    AS gross_revenue,
                COALESCE(SUM(CASE WHEN access_type='own'    THEN price_paid END), 0) AS ownership_revenue,
                COALESCE(SUM(CASE WHEN access_type='stream' THEN price_paid END), 0) AS stream_revenue,
                COUNT(*)                                                        AS total_transactions,
                COUNT(DISTINCT user_id)                                         AS paying_users,
                COUNT(DISTINCT drop_id)                                         AS monetized_drops,
                COALESCE(AVG(NULLIF(price_paid, 0)), 0)                         AS avg_transaction
            FROM drop_access
        """).fetchone())

        # By artist
        by_artist = rows_to_list(conn.execute("""
            SELECT
                u.id AS artist_id, u.username,
                COUNT(DISTINCT d.id)               AS drop_count,
                COUNT(DISTINCT da.id)              AS total_sales,
                COALESCE(SUM(da.price_paid), 0)    AS total_revenue,
                COALESCE(MAX(da.price_paid), 0)    AS highest_sale,
                COALESCE(AVG(NULLIF(da.price_paid, 0)), 0) AS avg_sale
            FROM users u
            JOIN drops d ON d.artist_id = u.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            GROUP BY u.id
            HAVING total_revenue > 0
            ORDER BY total_revenue DESC
            LIMIT 50
        """).fetchall())

        # Last 30 days daily breakdown
        daily = rows_to_list(conn.execute("""
            SELECT
                date(acquired_at) AS day,
                COUNT(*)          AS transactions,
                SUM(price_paid)   AS revenue
            FROM drop_access
            WHERE price_paid > 0
              AND acquired_at >= date('now', '-30 days')
            GROUP BY day
            ORDER BY day DESC
        """).fetchall())

    finally:
        conn.close()

    return jsonify({"totals": totals, "by_artist": by_artist, "daily_30d": daily})


# ---------------------------------------------------------------------------
# Velocity (trending)
# ---------------------------------------------------------------------------

@admin_bp.route("/velocity", methods=["GET"])
@require_auth
@require_role("admin")
def velocity(req):
    limit = min(50, max(5, int(req.query.get("limit", 20))))

    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT
                d.id, d.title, d.status, d.drop_type,
                u.username AS artist,
                COUNT(DISTINCT CASE WHEN de.action = 'play'          THEN de.id END) AS plays,
                COUNT(DISTINCT CASE WHEN de.action = 'replay'        THEN de.id END) AS replays,
                COUNT(DISTINCT CASE WHEN de.action = 'save'          THEN de.id END) AS saves,
                COUNT(DISTINCT CASE WHEN de.action = 'share'         THEN de.id END) AS shares,
                COUNT(DISTINCT CASE WHEN de.action = 'profile_click' THEN de.id END) AS profile_clicks,
                COUNT(DISTINCT da.id)  AS claims,
                COALESCE(SUM(da.price_paid), 0) AS revenue,
                MAX(de.timestamp)  AS last_activity,
                ROUND(
                    (
                        COUNT(DISTINCT CASE WHEN de.action = 'play'          THEN de.id END) * 1 +
                        COUNT(DISTINCT CASE WHEN de.action = 'replay'        THEN de.id END) * 2 +
                        COUNT(DISTINCT CASE WHEN de.action = 'save'          THEN de.id END) * 3 +
                        COUNT(DISTINCT CASE WHEN de.action = 'share'         THEN de.id END) * 5 +
                        COUNT(DISTINCT CASE WHEN de.action = 'profile_click' THEN de.id END) * 2
                    ) * 1.0 / MAX(1, (
                        (julianday('now') - julianday(d.starts_at)) * 24
                    )),
                3) AS velocity_score
            FROM drops d
            JOIN users u ON u.id = d.artist_id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            WHERE d.status IN ('live', 'expired')
            GROUP BY d.id
            ORDER BY velocity_score DESC
            LIMIT ?
        """, (limit,)).fetchall()
    finally:
        conn.close()

    return jsonify({"drops": rows_to_list(rows), "limit": limit})


# ---------------------------------------------------------------------------
# Scenes
# ---------------------------------------------------------------------------

@admin_bp.route("/scenes", methods=["GET"])
@require_auth
@require_role("admin")
def list_scenes(req):
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT s.id, s.name, s.city, s.description, s.created_at,
                   u.username AS created_by_username,
                   COUNT(DISTINCT ds.drop_id) AS drop_count
            FROM scenes s
            JOIN users u ON u.id = s.created_by
            LEFT JOIN drop_scenes ds ON ds.scene_id = s.id
            GROUP BY s.id
            ORDER BY drop_count DESC, s.created_at DESC
        """).fetchall()
    finally:
        conn.close()
    return jsonify({"scenes": rows_to_list(rows)})


@admin_bp.route("/scenes/:scene_id", methods=["DELETE"])
@require_auth
@require_role("admin")
def delete_scene(req, scene_id):
    conn = get_db()
    try:
        scene = row_to_dict(conn.execute(
            "SELECT id, name FROM scenes WHERE id = ?", (scene_id,)
        ).fetchone())
        if not scene:
            return jsonify({"error": "Scene not found"}, 404), 404

        conn.execute("DELETE FROM drop_scenes WHERE scene_id = ?", (scene_id,))
        conn.execute("DELETE FROM scenes WHERE id = ?", (scene_id,))
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": f"Scene '{scene['name']}' deleted", "scene_id": scene_id})
