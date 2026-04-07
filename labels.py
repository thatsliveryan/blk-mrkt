"""
BLK MRKT — Label business layer (stdlib version)

A Label is an account that signs artists, oversees their drops,
and gets a consolidated revenue + analytics view.

Routes:
  POST   /api/labels                          - Create a label (label role required)
  GET    /api/labels/:id                      - Label public profile + roster + stats
  PATCH  /api/labels/:id                      - Update label info (owner only)
  GET    /api/labels/:id/roster               - Full roster with per-artist stats
  POST   /api/labels/:id/roster               - Add artist to roster
  DELETE /api/labels/:id/roster/:artist_id    - Remove artist from roster
  GET    /api/labels/:id/drops                - All drops from label's artists
  GET    /api/labels/:id/revenue              - Revenue breakdown by artist
  GET    /api/labels/:id/analytics            - Velocity + engagement for label
  GET    /api/me/label                        - Current user's label (if label role)
"""

import re
from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, row_to_dict, rows_to_list

labels_bp = Blueprint("labels", prefix="/api/labels")


def _slugify(name):
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_-]+", "-", slug)
    return slug[:40]


def _get_label_for_owner(conn, owner_id):
    return row_to_dict(conn.execute(
        "SELECT * FROM labels WHERE owner_id = ?", (owner_id,)
    ).fetchone())


# ---------------------------------------------------------------------------
# Create label
# ---------------------------------------------------------------------------

@labels_bp.route("", methods=["POST"])
@require_auth
@require_role("label", "admin")
def create_label(req):
    conn = get_db()
    try:
        # One label per owner
        existing = conn.execute(
            "SELECT id FROM labels WHERE owner_id = ?", (srv.g.user_id,)
        ).fetchone()
        if existing:
            return jsonify({"error": "You already have a label. Update it instead."}, 409), 409

        data = req.get_json(silent=True) or {}
        name = (data.get("name") or "").strip()
        bio = (data.get("bio") or "").strip()
        city = (data.get("city") or "").strip()

        if not name:
            return jsonify({"error": "Label name is required"}, 400), 400

        slug = _slugify(name)
        label_id = new_id()

        # Ensure slug uniqueness
        base_slug = slug
        suffix = 1
        while conn.execute("SELECT id FROM labels WHERE slug = ?", (slug,)).fetchone():
            slug = f"{base_slug}-{suffix}"
            suffix += 1

        conn.execute(
            "INSERT INTO labels (id, name, slug, owner_id, bio, city) VALUES (?, ?, ?, ?, ?, ?)",
            (label_id, name, slug, srv.g.user_id, bio, city),
        )
        conn.commit()
        label = row_to_dict(conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone())
    except Exception:
        conn.close()
        return jsonify({"error": "Failed to create label"}, 500), 500
    conn.close()
    return jsonify({"label": label}, 201), 201


# ---------------------------------------------------------------------------
# My label shortcut
# ---------------------------------------------------------------------------

@labels_bp.route("/me", methods=["GET"])
@require_auth
@require_role("label", "admin")
def my_label(req):
    conn = get_db()
    try:
        label = _get_label_for_owner(conn, srv.g.user_id)
    finally:
        conn.close()

    if not label:
        return jsonify({"label": None, "setup_required": True})
    return jsonify({"label": label})


# ---------------------------------------------------------------------------
# Get label profile
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id", methods=["GET"])
@require_auth
def get_label(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT l.*, u.username AS owner_username FROM labels l JOIN users u ON u.id = l.owner_id WHERE l.id = ?",
            (label_id,),
        ).fetchone())

        if not label:
            return jsonify({"error": "Label not found"}, 404), 404

        # Quick stats
        stats = row_to_dict(conn.execute("""
            SELECT
                COUNT(DISTINCT la.artist_id)  AS roster_size,
                COUNT(DISTINCT d.id)          AS total_drops,
                COUNT(DISTINCT CASE WHEN d.status = 'live' THEN d.id END) AS live_drops,
                COALESCE(SUM(da.price_paid), 0) AS total_revenue,
                COUNT(DISTINCT da.id)         AS total_sales
            FROM label_artists la
            LEFT JOIN drops d   ON d.artist_id = la.artist_id AND la.label_id = ?
            LEFT JOIN drop_access da ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
        """, (label_id, label_id)).fetchone())

        # Roster preview
        roster = rows_to_list(conn.execute("""
            SELECT u.id, u.username, u.city, u.bio, u.avatar_url, la.joined_at,
                   COUNT(DISTINCT d.id) AS drop_count
            FROM label_artists la
            JOIN users u ON u.id = la.artist_id
            LEFT JOIN drops d ON d.artist_id = u.id
            WHERE la.label_id = ? AND la.status = 'active'
            GROUP BY u.id
            ORDER BY la.joined_at ASC
        """, (label_id,)).fetchall())

    finally:
        conn.close()

    return jsonify({"label": label, "stats": stats, "roster": roster})


# ---------------------------------------------------------------------------
# Update label info
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id", methods=["PATCH"])
@require_auth
@require_role("label", "admin")
def update_label(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404
        if label["owner_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Not authorized"}, 403), 403

        data = req.get_json(silent=True) or {}
        updates = {}
        if "name" in data and data["name"].strip():
            updates["name"] = data["name"].strip()
        if "bio" in data:
            updates["bio"] = data["bio"].strip()
        if "city" in data:
            updates["city"] = data["city"].strip()

        if not updates:
            return jsonify({"error": "Nothing to update"}, 400), 400

        set_sql = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(f"UPDATE labels SET {set_sql} WHERE id = ?", list(updates.values()) + [label_id])
        conn.commit()
        label = row_to_dict(conn.execute("SELECT * FROM labels WHERE id = ?", (label_id,)).fetchone())
    finally:
        conn.close()
    return jsonify({"label": label})


# ---------------------------------------------------------------------------
# Roster management
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id/roster", methods=["GET"])
@require_auth
def get_roster(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404

        # Only label owner or admin can see full roster stats
        is_owner = label["owner_id"] == srv.g.user_id or srv.g.user_role == "admin"

        roster = rows_to_list(conn.execute("""
            SELECT
                u.id, u.username, u.email, u.city, u.bio, u.avatar_url,
                la.status, la.joined_at,
                COUNT(DISTINCT d.id)              AS total_drops,
                COUNT(DISTINCT CASE WHEN d.status='live' THEN d.id END) AS live_drops,
                COUNT(DISTINCT da.id)             AS total_sales,
                COALESCE(SUM(da.price_paid), 0)   AS revenue,
                COALESCE(SUM(CASE WHEN de.action='play'   THEN 1 ELSE 0 END), 0) AS plays,
                COALESCE(SUM(CASE WHEN de.action='save'   THEN 1 ELSE 0 END), 0) AS saves,
                COALESCE(SUM(CASE WHEN de.action='share'  THEN 1 ELSE 0 END), 0) AS shares
            FROM label_artists la
            JOIN users u ON u.id = la.artist_id
            LEFT JOIN drops d  ON d.artist_id = u.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
            GROUP BY u.id
            ORDER BY revenue DESC
        """, (label_id,)).fetchall())

        if not is_owner:
            # Strip sensitive fields for public view
            for a in roster:
                a.pop("email", None)
    finally:
        conn.close()

    return jsonify({"roster": roster, "label_id": label_id})


@labels_bp.route("/:label_id/roster", methods=["POST"])
@require_auth
@require_role("label", "admin")
def add_to_roster(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404
        if label["owner_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Not authorized"}, 403), 403

        data = req.get_json(silent=True) or {}
        # Accept artist_id or username
        artist_id = data.get("artist_id")
        username = (data.get("username") or "").strip().lower()

        if username and not artist_id:
            artist = row_to_dict(conn.execute(
                "SELECT id, role FROM users WHERE username = ?", (username,)
            ).fetchone())
        elif artist_id:
            artist = row_to_dict(conn.execute(
                "SELECT id, role FROM users WHERE id = ?", (artist_id,)
            ).fetchone())
        else:
            return jsonify({"error": "artist_id or username required"}, 400), 400

        if not artist:
            return jsonify({"error": "Artist not found"}, 404), 404
        if artist["role"] not in ("artist", "curator"):
            return jsonify({"error": "User must be an artist or curator"}, 400), 400

        # Check already on roster
        existing = conn.execute(
            "SELECT status FROM label_artists WHERE label_id = ? AND artist_id = ?",
            (label_id, artist["id"]),
        ).fetchone()

        if existing:
            if existing["status"] == "active":
                return jsonify({"error": "Artist already on roster"}, 409), 409
            # Re-activate
            conn.execute(
                "UPDATE label_artists SET status = 'active' WHERE label_id = ? AND artist_id = ?",
                (label_id, artist["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO label_artists (label_id, artist_id, invited_by, status) VALUES (?, ?, ?, 'active')",
                (label_id, artist["id"], srv.g.user_id),
            )
        conn.commit()
    except Exception:
        conn.close()
        return jsonify({"error": "Failed to add artist"}, 500), 500
    conn.close()
    return jsonify({"message": "Artist added to roster", "artist_id": artist["id"]}, 201), 201


@labels_bp.route("/:label_id/roster/:artist_id", methods=["DELETE"])
@require_auth
@require_role("label", "admin")
def remove_from_roster(req, label_id, artist_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404
        if label["owner_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Not authorized"}, 403), 403

        conn.execute(
            "UPDATE label_artists SET status = 'removed' WHERE label_id = ? AND artist_id = ?",
            (label_id, artist_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"message": "Artist removed from roster"})


# ---------------------------------------------------------------------------
# Label drops
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id/drops", methods=["GET"])
@require_auth
def label_drops(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404

        status_filter = req.query.get("status", "")
        page = max(1, int(req.query.get("page", 1)))
        per_page = min(100, max(10, int(req.query.get("per_page", 25))))
        offset = (page - 1) * per_page

        where_extra = ""
        params = [label_id]
        if status_filter in ("scheduled", "live", "locked", "expired"):
            where_extra = "AND d.status = ?"
            params.append(status_filter)

        total = conn.execute(f"""
            SELECT COUNT(*) as n FROM drops d
            JOIN label_artists la ON la.artist_id = d.artist_id
            WHERE la.label_id = ? AND la.status = 'active' {where_extra}
        """, params).fetchone()["n"]

        rows = conn.execute(f"""
            SELECT
                d.id, d.title, d.status, d.drop_type, d.access_price,
                d.total_supply, d.remaining_supply, d.starts_at, d.expires_at, d.created_at,
                u.id AS artist_id, u.username AS artist_username,
                COUNT(DISTINCT da.id)           AS claim_count,
                COALESCE(SUM(da.price_paid), 0) AS revenue,
                COUNT(DISTINCT de.id)           AS engagement_count
            FROM drops d
            JOIN users u ON u.id = d.artist_id
            JOIN label_artists la ON la.artist_id = d.artist_id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active' {where_extra}
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


# ---------------------------------------------------------------------------
# Label revenue
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id/revenue", methods=["GET"])
@require_auth
def label_revenue(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404
        if label["owner_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Not authorized"}, 403), 403

        totals = row_to_dict(conn.execute("""
            SELECT
                COALESCE(SUM(da.price_paid), 0)    AS gross_revenue,
                COUNT(DISTINCT da.id)              AS total_sales,
                COUNT(DISTINCT da.user_id)         AS unique_buyers,
                COUNT(DISTINCT d.id)               AS monetized_drops,
                COALESCE(AVG(NULLIF(da.price_paid, 0)), 0) AS avg_sale
            FROM label_artists la
            JOIN drops d ON d.artist_id = la.artist_id
            JOIN drop_access da ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
        """, (label_id,)).fetchone())

        by_artist = rows_to_list(conn.execute("""
            SELECT
                u.id AS artist_id, u.username,
                COUNT(DISTINCT d.id)               AS drop_count,
                COUNT(DISTINCT da.id)              AS total_sales,
                COALESCE(SUM(da.price_paid), 0)    AS revenue,
                COALESCE(AVG(NULLIF(da.price_paid,0)), 0) AS avg_sale
            FROM label_artists la
            JOIN users u ON u.id = la.artist_id
            LEFT JOIN drops d  ON d.artist_id = u.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
            GROUP BY u.id
            ORDER BY revenue DESC
        """, (label_id,)).fetchall())

        daily_30d = rows_to_list(conn.execute("""
            SELECT date(da.acquired_at) AS day,
                   COUNT(*)            AS sales,
                   SUM(da.price_paid)  AS revenue
            FROM label_artists la
            JOIN drops d  ON d.artist_id = la.artist_id
            JOIN drop_access da ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
              AND da.acquired_at >= date('now', '-30 days')
            GROUP BY day ORDER BY day DESC
        """, (label_id,)).fetchall())
    finally:
        conn.close()

    return jsonify({"totals": totals, "by_artist": by_artist, "daily_30d": daily_30d})


# ---------------------------------------------------------------------------
# Label analytics (velocity)
# ---------------------------------------------------------------------------

@labels_bp.route("/:label_id/analytics", methods=["GET"])
@require_auth
def label_analytics(req, label_id):
    conn = get_db()
    try:
        label = row_to_dict(conn.execute(
            "SELECT id, owner_id FROM labels WHERE id = ?", (label_id,)
        ).fetchone())
        if not label:
            return jsonify({"error": "Label not found"}, 404), 404
        if label["owner_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "Not authorized"}, 403), 403

        top_drops = rows_to_list(conn.execute("""
            SELECT
                d.id, d.title, d.status, d.drop_type,
                u.username AS artist,
                COUNT(DISTINCT CASE WHEN de.action='play'   THEN de.id END) AS plays,
                COUNT(DISTINCT CASE WHEN de.action='save'   THEN de.id END) AS saves,
                COUNT(DISTINCT CASE WHEN de.action='share'  THEN de.id END) AS shares,
                COUNT(DISTINCT da.id)  AS claims,
                COALESCE(SUM(da.price_paid), 0) AS revenue,
                ROUND(
                  (COUNT(DISTINCT CASE WHEN de.action='play'   THEN de.id END) * 1 +
                   COUNT(DISTINCT CASE WHEN de.action='replay' THEN de.id END) * 2 +
                   COUNT(DISTINCT CASE WHEN de.action='save'   THEN de.id END) * 3 +
                   COUNT(DISTINCT CASE WHEN de.action='share'  THEN de.id END) * 5 +
                   COUNT(DISTINCT CASE WHEN de.action='profile_click' THEN de.id END) * 2
                  ) * 1.0 / MAX(1, (julianday('now') - julianday(d.starts_at)) * 24),
                3) AS velocity_score
            FROM label_artists la
            JOIN drops d ON d.artist_id = la.artist_id
            JOIN users u ON u.id = d.artist_id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            LEFT JOIN drop_access da     ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
            GROUP BY d.id
            ORDER BY velocity_score DESC
            LIMIT 20
        """, (label_id,)).fetchall())

        roster_engagement = rows_to_list(conn.execute("""
            SELECT
                u.id AS artist_id, u.username,
                COALESCE(SUM(CASE WHEN de.action='play'   THEN 1 ELSE 0 END), 0) AS plays,
                COALESCE(SUM(CASE WHEN de.action='save'   THEN 1 ELSE 0 END), 0) AS saves,
                COALESCE(SUM(CASE WHEN de.action='share'  THEN 1 ELSE 0 END), 0) AS shares,
                COUNT(DISTINCT da.id) AS claims
            FROM label_artists la
            JOIN users u ON u.id = la.artist_id
            LEFT JOIN drops d ON d.artist_id = u.id
            LEFT JOIN drop_engagement de ON de.drop_id = d.id
            LEFT JOIN drop_access da ON da.drop_id = d.id
            WHERE la.label_id = ? AND la.status = 'active'
            GROUP BY u.id
            ORDER BY plays DESC
        """, (label_id,)).fetchall())
    finally:
        conn.close()

    return jsonify({"top_drops": top_drops, "roster_engagement": roster_engagement})
