"""
BLK MRKT — Scenes module (stdlib version)
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, row_to_dict, rows_to_list
from engine import transition_drop_states, calc_velocity_bulk, get_drop_status_info

scenes_bp = Blueprint("scenes", prefix="/api/scenes")


@scenes_bp.route("", methods=["GET"])
def list_scenes(req):
    conn = get_db()
    try:
        scenes = rows_to_list(conn.execute(
            """SELECT s.*, u.username as creator_name,
                   (SELECT COUNT(*) FROM drop_scenes ds WHERE ds.scene_id = s.id) as drop_count
            FROM scenes s JOIN users u ON s.created_by = u.id ORDER BY drop_count DESC"""
        ).fetchall())
    finally:
        conn.close()
    return jsonify({"scenes": scenes})


@scenes_bp.route("/:scene_id/drops", methods=["GET"])
def scene_drops(req, scene_id):
    transition_drop_states()
    conn = get_db()
    try:
        scene = row_to_dict(conn.execute("SELECT * FROM scenes WHERE id = ?", (scene_id,)).fetchone())
        if not scene:
            return jsonify({"error": "Scene not found"}, 404), 404
        drops = rows_to_list(conn.execute(
            """SELECT d.*, u.username as artist_name, u.avatar_url as artist_avatar
            FROM drops d JOIN drop_scenes ds ON d.id = ds.drop_id
            JOIN users u ON d.artist_id = u.id
            WHERE ds.scene_id = ? AND d.status IN ('live', 'expired')
            ORDER BY d.starts_at DESC LIMIT 50""",
            (scene_id,),
        ).fetchall())
    finally:
        conn.close()

    ids_starts = [(d["id"], d["starts_at"]) for d in drops]
    velocities = calc_velocity_bulk(ids_starts)
    for d in drops:
        d["velocity"] = velocities.get(d["id"], 0)
        get_drop_status_info(d)
    drops.sort(key=lambda d: d["velocity"], reverse=True)
    return jsonify({"scene": scene, "drops": drops})


@scenes_bp.route("", methods=["POST"])
@require_auth
@require_role("curator", "admin")
def create_scene(req):
    data = req.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name is required"}, 400), 400

    scene_id = new_id()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO scenes (id, name, city, description, created_by) VALUES (?, ?, ?, ?, ?)",
            (scene_id, name, data.get("city", ""), data.get("description", ""), srv.g.user_id),
        )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"scene": {"id": scene_id, "name": name}}, 201), 201
