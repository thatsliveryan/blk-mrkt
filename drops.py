"""
BLK MRKT — Drops module (stdlib version)
"""

import os
import json
from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role, get_auth_user, decode_token
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list
from engine import (
    transition_drop_states, get_drop_status_info,
    calc_velocity, calc_velocity_bulk, get_engagement_stats,
)
from boosts import boost_multiplier_for_drop
from config import AUDIO_DIR, COVERS_DIR, ALLOWED_AUDIO_EXTENSIONS, ALLOWED_IMAGE_EXTENSIONS

drops_bp = Blueprint("drops", prefix="/api/drops")


def _allowed_file(filename, allowed):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed


@drops_bp.route("", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def create_drop(req):
    if req.files:
        data = req.form
        audio_file = req.files.get("audio")
        cover_file = req.files.get("cover")
    else:
        data = req.get_json(silent=True) or {}
        audio_file = None
        cover_file = None

    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "Title is required"}, 400), 400

    drop_id = new_id()
    drop_type = data.get("drop_type", "open")
    if drop_type not in ("open", "timed", "limited", "tiered", "rare"):
        return jsonify({"error": "Invalid drop_type"}, 400), 400

    total_supply = data.get("total_supply")
    if total_supply is not None and total_supply != '':
        try:
            total_supply = int(total_supply)
        except (ValueError, TypeError):
            return jsonify({"error": "total_supply must be an integer"}, 400), 400
    else:
        total_supply = None

    access_price = float(data.get("access_price", 0))
    own_price = data.get("own_price")
    if own_price is not None and own_price != '':
        try:
            own_price = float(own_price)
        except (ValueError, TypeError):
            own_price = None
    else:
        own_price = None

    # $2 minimum for any paid drop
    MIN_PAID_PRICE = 2.00
    LOW_PRICE_WARNING = 4.99
    price_warning = None
    if access_price > 0 and access_price < MIN_PAID_PRICE:
        return jsonify({
            "error": f"Minimum drop price is ${MIN_PAID_PRICE:.2f}. Set to 0 for a free drop.",
            "min_price": MIN_PAID_PRICE,
        }, 400), 400
    if own_price is not None and own_price > 0 and own_price < MIN_PAID_PRICE:
        return jsonify({
            "error": f"Minimum ownership price is ${MIN_PAID_PRICE:.2f}.",
            "min_price": MIN_PAID_PRICE,
        }, 400), 400
    if 0 < access_price <= LOW_PRICE_WARNING:
        price_warning = f"Low price alert: at ${access_price:.2f} per claim, you keep ${access_price * 0.85:.2f} on the Free plan. Consider upgrading to keep more."

    starts_at = data.get("starts_at") or utcnow()
    expires_at = data.get("expires_at") or None

    audio_path = None
    r2_audio_key = None
    if audio_file and audio_file.filename:
        if not _allowed_file(audio_file.filename, ALLOWED_AUDIO_EXTENSIONS):
            return jsonify({"error": "Audio must be MP3 or WAV"}, 400), 400
        ext = audio_file.filename.rsplit(".", 1)[1].lower()
        audio_path = f"/data/audio/{drop_id}.{ext}"
        local_audio_path = os.path.join(AUDIO_DIR, f"{drop_id}.{ext}")
        os.makedirs(AUDIO_DIR, exist_ok=True)
        audio_file.save(local_audio_path)
        # Dual-write to R2
        from storage import save_audio
        r2_audio_key = f"audio/{drop_id}.{ext}"
        ok, err = save_audio(local_audio_path, r2_audio_key)
        if not ok:
            print(f"[R2] Audio upload warning: {err}")
            r2_audio_key = None

    cover_path = None
    r2_cover_key = None
    if cover_file and cover_file.filename:
        if not _allowed_file(cover_file.filename, ALLOWED_IMAGE_EXTENSIONS):
            return jsonify({"error": "Image must be PNG, JPG, or WebP"}, 400), 400
        ext = cover_file.filename.rsplit(".", 1)[1].lower()
        cover_path = f"/data/covers/{drop_id}.{ext}"
        local_cover_path = os.path.join(COVERS_DIR, f"{drop_id}.{ext}")
        os.makedirs(COVERS_DIR, exist_ok=True)
        cover_file.save(local_cover_path)
        # Dual-write to R2
        from storage import save_cover
        r2_cover_key = f"covers/{drop_id}.{ext}"
        ok, err = save_cover(local_cover_path, r2_cover_key)
        if not ok:
            print(f"[R2] Cover upload warning: {err}")
            r2_cover_key = None

    city = (data.get("city") or "").strip() or None

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO drops
            (id, artist_id, title, description, audio_path, cover_image_path,
             drop_type, total_supply, remaining_supply, access_price, own_price,
             starts_at, expires_at, status, city, r2_audio_key, r2_cover_key)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (drop_id, srv.g.user_id, title, data.get("description", ""),
             audio_path, cover_path, drop_type, total_supply, total_supply,
             access_price, own_price, starts_at, expires_at, "scheduled", city,
             r2_audio_key, r2_cover_key),
        )
        scene_ids = data.get("scene_ids")
        if scene_ids:
            if isinstance(scene_ids, str):
                scene_ids = json.loads(scene_ids)
            for sid in scene_ids:
                conn.execute("INSERT OR IGNORE INTO drop_scenes (drop_id, scene_id) VALUES (?, ?)", (drop_id, sid))
        conn.commit()
    finally:
        conn.close()

    transition_drop_states()
    resp = {"drop": {"id": drop_id, "title": title, "status": "scheduled"}}
    if price_warning:
        resp["warning"] = price_warning
    return jsonify(resp, 201), 201


@drops_bp.route("", methods=["GET"])
def list_drops(req):
    transition_drop_states()

    limit = min(int(req.args.get("limit", 50)), 100)
    offset = int(req.args.get("offset", 0))
    status_filter = req.args.get("status", "live")

    conn = get_db()
    try:
        drops = rows_to_list(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar
            FROM drops d JOIN users u ON d.artist_id = u.id
            WHERE d.status = ?
            ORDER BY d.starts_at DESC LIMIT ? OFFSET ?""",
            (status_filter, limit, offset),
        ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        base_velocity = velocities.get(d["id"], 0)
        multiplier = boost_multiplier_for_drop(d["id"]) if d.get("boost_active") else 1.0
        d["velocity"] = round(base_velocity * multiplier, 2)
        d["boost_multiplier"] = multiplier
        get_drop_status_info(d)
    drops.sort(key=lambda d: d["velocity"], reverse=True)

    return jsonify({"drops": drops, "count": len(drops)})


@drops_bp.route("/trending", methods=["GET"])
def trending(req):
    transition_drop_states()

    conn = get_db()
    try:
        drops = rows_to_list(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar
            FROM drops d JOIN users u ON d.artist_id = u.id
            WHERE d.status IN ('live', 'expired')
            AND d.starts_at >= datetime('now', '-24 hours')
            ORDER BY d.starts_at DESC LIMIT 20""",
        ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        base_velocity = velocities.get(d["id"], 0)
        multiplier = boost_multiplier_for_drop(d["id"]) if d.get("boost_active") else 1.0
        d["velocity"] = round(base_velocity * multiplier, 2)
        d["boost_multiplier"] = multiplier
        get_drop_status_info(d)
    drops.sort(key=lambda d: d["velocity"], reverse=True)
    return jsonify({"drops": drops})


@drops_bp.route("/:drop_id", methods=["GET"])
def get_drop(req, drop_id):
    transition_drop_states()

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar,
                   u.bio as artist_bio, u.city as artist_city
            FROM drops d JOIN users u ON d.artist_id = u.id WHERE d.id = ?""",
            (drop_id,),
        ).fetchone())
    finally:
        conn.close()

    if not drop:
        return jsonify({"error": "Drop not found"}, 404), 404

    drop["velocity"] = calc_velocity(drop_id, drop["starts_at"])
    drop["engagement"] = get_engagement_stats(drop_id)
    get_drop_status_info(drop)

    # Ownership ledger — how many people own this drop + first 10 owners for social proof
    conn = get_db()
    try:
        owner_count = conn.execute(
            "SELECT COUNT(*) as c FROM drop_access WHERE drop_id = ? AND access_type = 'own'",
            (drop_id,),
        ).fetchone()["c"]
        first_owners = rows_to_list(conn.execute(
            """SELECT u.username, u.avatar_url, da.acquired_at, da.fan_number
               FROM drop_access da JOIN users u ON da.user_id = u.id
               WHERE da.drop_id = ? AND da.access_type = 'own'
               ORDER BY da.acquired_at ASC LIMIT 10""",
            (drop_id,),
        ).fetchall())
    finally:
        conn.close()
    drop["owner_count"] = owner_count
    drop["first_owners"] = first_owners

    # Check access for authenticated user
    auth_header = req.headers.get("authorization", "")
    drop["user_has_access"] = False
    drop["user_access_type"] = None
    drop["user_fan_number"] = None
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header[7:])
        if payload:
            conn = get_db()
            try:
                access = row_to_dict(conn.execute(
                    "SELECT * FROM drop_access WHERE user_id = ? AND drop_id = ? ORDER BY access_type DESC LIMIT 1",
                    (payload["sub"], drop_id),
                ).fetchone())
                drop["user_has_access"] = access is not None
                drop["user_access_type"] = access["access_type"] if access else None
                drop["user_fan_number"] = access["fan_number"] if access else None
            finally:
                conn.close()

    return jsonify({"drop": drop})


@drops_bp.route("/:drop_id/access", methods=["POST"])
@require_auth
def claim_access(req, drop_id):
    transition_drop_states()

    data = req.get_json(silent=True) or {}
    access_type = data.get("access_type", "stream")
    if access_type not in ("stream", "own"):
        return jsonify({"error": "access_type must be 'stream' or 'own'"}, 400), 400

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404

        if drop["status"] == "expired":
            return jsonify({"error": "EXPIRED", "message": "This drop has expired"}, 410), 410
        if drop["status"] == "locked":
            return jsonify({"error": "LOCKED", "message": "This drop is locked"}, 403), 403
        if drop["status"] != "live":
            return jsonify({"error": "Drop is not live yet"}, 403), 403

        existing = row_to_dict(conn.execute(
            "SELECT * FROM drop_access WHERE user_id = ? AND drop_id = ? AND access_type = ?",
            (srv.g.user_id, drop_id, access_type),
        ).fetchone())
        if existing:
            return jsonify({"error": "Already have access", "access": existing}, 409), 409

        if drop["total_supply"] is not None and drop["remaining_supply"] <= 0:
            return jsonify({"error": "SOLD_OUT", "message": "This drop is sold out"}, 410), 410

        price = drop["access_price"] if access_type == "stream" else (drop["own_price"] or drop["access_price"])

        if drop["total_supply"] is not None:
            cur = conn.execute(
                "UPDATE drops SET remaining_supply = remaining_supply - 1 WHERE id = ? AND remaining_supply > 0",
                (drop_id,),
            )
            if cur.rowcount == 0:
                return jsonify({"error": "SOLD_OUT", "message": "This drop is sold out"}, 410), 410

        # Record which fan number this is (for First-N badge)
        fan_number = conn.execute(
            "SELECT COUNT(*) as c FROM drop_access WHERE drop_id = ? AND access_type = ?",
            (drop_id, access_type),
        ).fetchone()["c"] + 1  # +1 because we haven't inserted yet

        conn.execute(
            "INSERT INTO drop_access (user_id, drop_id, access_type, price_paid, fan_number) VALUES (?, ?, ?, ?, ?)",
            (srv.g.user_id, drop_id, access_type, price, fan_number),
        )
        conn.commit()

        updated = row_to_dict(conn.execute("SELECT remaining_supply, total_supply FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if updated["total_supply"] is not None and updated["remaining_supply"] <= 0:
            conn.execute("UPDATE drops SET status = 'expired' WHERE id = ?", (drop_id,))
            conn.commit()
    finally:
        conn.close()

    # Award badges for free/direct claims
    try:
        from badges import check_and_award_badges
        awarded = check_and_award_badges(srv.g.user_id, drop_id)
    except Exception:
        awarded = []

    return jsonify({
        "message": "Access granted",
        "access_type": access_type,
        "price_paid": price,
        "drop_id": drop_id,
        "fan_number": fan_number,
        "badge": f"First {fan_number}" if fan_number <= 100 else None,
        "badges_awarded": awarded,
    }, 201), 201


@drops_bp.route("/collection", methods=["GET"])
@require_auth
def my_collection(req):
    """Fan's claimed drops collection (stream + own)."""
    conn = get_db()
    try:
        drops = rows_to_list(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar,
                      da.access_type, da.acquired_at, da.price_paid, da.fan_number
               FROM drop_access da
               JOIN drops d ON da.drop_id = d.id
               JOIN users u ON d.artist_id = u.id
               WHERE da.user_id = ?
               ORDER BY da.acquired_at DESC""",
            (srv.g.user_id,),
        ).fetchall())
    finally:
        conn.close()

    for d in drops:
        get_drop_status_info(d)
        if d.get("fan_number") and d["fan_number"] <= 100:
            d["badge"] = f"First {d['fan_number']}"
        else:
            d["badge"] = None

    return jsonify({"drops": drops, "count": len(drops)})


@drops_bp.route("/my", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def my_drops(req):
    """Artist's own drops."""
    status_filter = req.args.get("status")
    conn = get_db()
    try:
        if status_filter:
            drops = rows_to_list(conn.execute(
                "SELECT * FROM drops WHERE artist_id = ? AND status = ? ORDER BY created_at DESC",
                (srv.g.user_id, status_filter),
            ).fetchall())
        else:
            drops = rows_to_list(conn.execute(
                "SELECT * FROM drops WHERE artist_id = ? ORDER BY created_at DESC",
                (srv.g.user_id,),
            ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        d["velocity"] = velocities.get(d["id"], 0)
        get_drop_status_info(d)

    return jsonify({"drops": drops, "count": len(drops)})


@drops_bp.route("/trending/city/:city", methods=["GET"])
def trending_by_city(req, city):
    """City-level trending — the local leaderboard."""
    transition_drop_states()
    conn = get_db()
    try:
        # Match by drop city OR artist city
        drops = rows_to_list(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar,
                      u.city as artist_city
               FROM drops d JOIN users u ON d.artist_id = u.id
               WHERE d.status IN ('live', 'expired')
               AND (LOWER(d.city) = LOWER(?) OR LOWER(u.city) = LOWER(?))
               AND d.starts_at >= datetime('now', '-72 hours')
               ORDER BY d.starts_at DESC LIMIT 20""",
            (city, city),
        ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        d["velocity"] = velocities.get(d["id"], 0)
        get_drop_status_info(d)
    drops.sort(key=lambda d: d["velocity"], reverse=True)

    return jsonify({"city": city, "drops": drops, "count": len(drops)})


@drops_bp.route("/:drop_id", methods=["PATCH"])
@require_auth
@require_role("artist", "admin")
def update_drop(req, drop_id):
    """Update drop metadata or status (owner or admin only)."""
    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        if drop["artist_id"] != srv.g.user_id and getattr(srv.g, "role", "") != "admin":
            return jsonify({"error": "Forbidden"}, 403), 403

        data = req.get_json(silent=True) or {}
        updates = {}
        allowed = ["title", "description", "status", "expires_at", "city"]
        for field in allowed:
            if field in data:
                updates[field] = data[field]

        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            vals = list(updates.values()) + [drop_id]
            conn.execute(f"UPDATE drops SET {set_clause} WHERE id = ?", vals)
            conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Drop updated", "drop_id": drop_id, "updated": list(updates.keys())})


@drops_bp.route("/:drop_id/engage", methods=["POST"])
@require_auth
def log_engagement(req, drop_id):
    data = req.get_json(silent=True) or {}
    action = data.get("action")
    if action not in ("play", "replay", "save", "share", "profile_click"):
        return jsonify({"error": "Invalid action"}, 400), 400

    metadata = json.dumps(data.get("metadata", {}))
    conn = get_db()
    try:
        drop = conn.execute("SELECT id FROM drops WHERE id = ?", (drop_id,)).fetchone()
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        conn.execute(
            "INSERT INTO drop_engagement (user_id, drop_id, action, metadata) VALUES (?, ?, ?, ?)",
            (srv.g.user_id, drop_id, action, metadata),
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Engagement logged", "action": action}, 201), 201
