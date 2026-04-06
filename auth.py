"""
BLK MRKT — Auth module (stdlib version)
"""

from functools import wraps
import bcrypt
import jwt
import time
from config import JWT_SECRET, JWT_ACCESS_EXPIRY, JWT_REFRESH_EXPIRY
from models import get_db, new_id, utcnow, row_to_dict
from server import Blueprint, jsonify, g
import server as srv

auth_bp = Blueprint("auth", prefix="/api/auth")


def create_token(user_id, role, token_type="access"):
    expiry = JWT_ACCESS_EXPIRY if token_type == "access" else JWT_REFRESH_EXPIRY
    payload = {
        "sub": user_id, "role": role, "type": token_type,
        "iat": int(time.time()), "exp": int(time.time()) + expiry,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def decode_token(token):
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


def get_auth_user(req):
    """Extract user from Bearer token. Returns (user_id, role) or (None, None)."""
    header = req.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None, None
    payload = decode_token(header[7:])
    if not payload or payload.get("type") != "access":
        return None, None
    return payload["sub"], payload["role"]


def require_auth(f):
    @wraps(f)
    def wrapper(req, **kwargs):
        uid, role = get_auth_user(req)
        if not uid:
            return jsonify({"error": "Missing or invalid token"}, 401), 401
        srv.g.user_id = uid
        srv.g.user_role = role
        return f(req, **kwargs)
    return wrapper


def require_role(*roles):
    def decorator(f):
        @wraps(f)
        def wrapper(req, **kwargs):
            if srv.g.user_role not in roles:
                return jsonify({"error": "Insufficient permissions"}, 403), 403
            return f(req, **kwargs)
        return wrapper
    return decorator


@auth_bp.route("/register", methods=["POST"])
def register(req):
    data = req.get_json(silent=True) or {}
    username = (data.get("username") or "").strip().lower()
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""
    role = data.get("role", "fan")
    city = (data.get("city") or "").strip()

    if not username or not email or len(password) < 6:
        return jsonify({"error": "Username, email, and password (6+ chars) required"}, 400), 400
    if role not in ("artist", "fan"):
        return jsonify({"error": "Role must be 'artist' or 'fan'"}, 400), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = new_id()

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO users (id, username, email, password_hash, role, city) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, username, email, pw_hash, role, city),
        )
        conn.commit()
    except Exception as e:
        conn.close()
        if "UNIQUE" in str(e):
            return jsonify({"error": "Username or email already taken"}, 409), 409
        return jsonify({"error": "Registration failed"}, 500), 500
    conn.close()

    access = create_token(user_id, role, "access")
    refresh = create_token(user_id, role, "refresh")

    return jsonify({
        "user": {"id": user_id, "username": username, "email": email, "role": role, "city": city},
        "access_token": access, "refresh_token": refresh,
    }, 201), 201


@auth_bp.route("/login", methods=["POST"])
def login(req):
    data = req.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password required"}, 400), 400

    conn = get_db()
    user = row_to_dict(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())
    conn.close()

    if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Invalid credentials"}, 401), 401

    access = create_token(user["id"], user["role"], "access")
    refresh = create_token(user["id"], user["role"], "refresh")

    return jsonify({
        "user": {"id": user["id"], "username": user["username"], "email": user["email"], "role": user["role"], "city": user["city"]},
        "access_token": access, "refresh_token": refresh,
    })


@auth_bp.route("/refresh", methods=["POST"])
def refresh(req):
    data = req.get_json(silent=True) or {}
    token = data.get("refresh_token", "")
    payload = decode_token(token)

    if not payload or payload.get("type") != "refresh":
        return jsonify({"error": "Invalid or expired refresh token"}, 401), 401

    conn = get_db()
    user = row_to_dict(conn.execute("SELECT id, role FROM users WHERE id = ?", (payload["sub"],)).fetchone())
    conn.close()

    if not user:
        return jsonify({"error": "User not found"}, 404), 404

    access = create_token(user["id"], user["role"], "access")
    return jsonify({"access_token": access})


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me(req):
    conn = get_db()
    user = row_to_dict(conn.execute(
        "SELECT id, username, email, role, city, bio, avatar_url, created_at FROM users WHERE id = ?",
        (srv.g.user_id,),
    ).fetchone())
    conn.close()

    if not user:
        return jsonify({"error": "User not found"}, 404), 404
    return jsonify({"user": user})
