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

    starts_at = data.get("starts_at") or utcnow()
    expires_at = data.get("expires_at") or None

    audio_path = None
    if audio_file and audio_file.filename:
        if not _allowed_file(audio_file.filename, ALLOWED_AUDIO_EXTENSIONS):
            return jsonify({"error": "Audio must be MP3 or WAV"}, 400), 400
        ext = audio_file.filename.rsplit(".", 1)[1].lower()
        audio_path = f"/data/audio/{drop_id}.{ext}"
        os.makedirs(AUDIO_DIR, exist_ok=True)
        audio_file.save(os.path.join(AUDIO_DIR, f"{drop_id}.{ext}"))

    cover_path = None
    if cover_file and cover_file.filename:
        if not _allowed_file(cover_file.filename, ALLOWED_IMAGE_EXTENSIONS):
            return jsonify({"error": "Image must be PNG, JPG, or WebP"}, 400), 400
        ext = cover_file.filename.rsplit(".", 1)[1].lower()
        cover_path = f"/data/covers/{drop_id}.{ext}"
        os.makedirs(COVERS_DIR, exist_ok=True)
        cover_file.save(os.path.join(COVERS_DIR, f"{drop_id}.{ext}"))

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO drops
            (id, artist_id, title, description, audio_path, cover_image_path,
             drop_type, total_supply, remaining_supply, access_price, own_price,
             starts_at, expires_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (drop_id, srv.g.user_id, title, data.get("description", ""),
             audio_path, cover_path, drop_type, total_supply, total_supply,
             access_price, own_price, starts_at, expires_at, "scheduled"),
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
    return jsonify({"drop": {"id": drop_id, "title": title, "status": "scheduled"}}, 201), 201


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
        d["velocity"] = velocities.get(d["id"], 0)
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
        d["velocity"] = velocities.get(d["id"], 0)
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

    # Check access
    auth_header = req.headers.get("authorization", "")
    drop["user_has_access"] = False
    drop["user_access_type"] = None
    if auth_header.startswith("Bearer "):
        payload = decode_token(auth_header[7:])
        if payload:
            conn = get_db()
            try:
                access = row_to_dict(conn.execute(
                    "SELECT * FROM drop_access WHERE user_id = ? AND drop_id = ?",
                    (payload["sub"], drop_id),
                ).fetchone())
                drop["user_has_access"] = access is not None
                drop["user_access_type"] = access["access_type"] if access else None
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

        conn.execute(
            "INSERT INTO drop_access (user_id, drop_id, access_type, price_paid) VALUES (?, ?, ?, ?)",
            (srv.g.user_id, drop_id, access_type, price),
        )
        conn.commit()

        updated = row_to_dict(conn.execute("SELECT remaining_supply, total_supply FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if updated["total_supply"] is not None and updated["remaining_supply"] <= 0:
            conn.execute("UPDATE drops SET status = 'expired' WHERE id = ?", (drop_id,))
            conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Access granted", "access_type": access_type, "price_paid": price, "drop_id": drop_id}, 201), 201


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
