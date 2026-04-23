"""
BLK MRKT — Auth module (stdlib version)

Endpoints:
  POST /api/auth/register            — create account (sends verification email)
  POST /api/auth/login               — get JWT tokens
  POST /api/auth/refresh             — exchange refresh token for new access token
  GET  /api/auth/me                  — current user profile
  GET  /api/auth/verify              — verify email via ?token=xxx
  POST /api/auth/resend-verification — resend verification email
  POST /api/auth/forgot-password     — send password reset email
  POST /api/auth/reset-password      — reset password via token
"""

import secrets
import re
from functools import wraps
import bcrypt
import jwt
import time

from config import JWT_SECRET, JWT_ACCESS_EXPIRY, JWT_REFRESH_EXPIRY, EMAIL_ENABLED, PUBLIC_URL
from models import get_db, new_id, utcnow, row_to_dict
from server import Blueprint, jsonify, g
import server as srv

auth_bp = Blueprint("auth", prefix="/api/auth")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Token expiry windows
VERIFY_TOKEN_TTL   = 86400      # 24 hours (seconds)
RESET_TOKEN_TTL    = 3600       # 1 hour (seconds)
RESET_RATE_LIMIT_S = 300        # 5 minutes between reset requests

# ---------------------------------------------------------------------------
# Security: constant-time dummy hash for login timing attack prevention.
# Always run bcrypt regardless of whether the email exists, so response
# time doesn't leak whether an account is registered.
# ---------------------------------------------------------------------------
_DUMMY_HASH = bcrypt.hashpw(b"blkmrkt_timing_sentinel_do_not_use", bcrypt.gensalt()).decode()


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def require_auth(f):
    @wraps(f)
    def wrapper(req, **kwargs):
        uid, _ = get_auth_user(req)
        if not uid:
            return jsonify({"error": "Missing or invalid token"}, 401), 401

        # --- JWT role staleness fix ---
        # Always read role + suspended from DB so bans and role changes take
        # effect immediately without waiting for the token to expire.
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT role, suspended FROM users WHERE id = ?", (uid,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return jsonify({"error": "Account not found"}, 401), 401
        if row["suspended"]:
            return jsonify({"error": "Account suspended"}, 403), 403

        srv.g.user_id = uid
        srv.g.user_role = row["role"]   # fresh DB role, not stale JWT claim
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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _send_verification_email(user_id: str, email: str, username: str, conn=None):
    """
    Create a verification token and send the verification email.
    If conn is provided, uses it; otherwise opens its own connection.
    """
    close_conn = conn is None
    if conn is None:
        conn = get_db()

    token = secrets.token_urlsafe(32)
    import datetime
    expires_at = (
        datetime.datetime.utcnow() + datetime.timedelta(seconds=VERIFY_TOKEN_TTL)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Remove any existing tokens for this user
    conn.execute("DELETE FROM email_verifications WHERE user_id = ?", (user_id,))
    conn.execute(
        "INSERT INTO email_verifications (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
    )
    conn.commit()

    verify_url = f"{PUBLIC_URL}/api/auth/verify?token={token}"

    if EMAIL_ENABLED:
        from email_utils import send_email
        send_email(
            to=email,
            subject="Verify your BLK MRKT account",
            text=(
                f"Hi {username},\n\n"
                f"Click the link below to verify your email address:\n\n"
                f"{verify_url}\n\n"
                f"This link expires in 24 hours.\n\n"
                f"If you didn't create a BLK MRKT account, ignore this email.\n\n"
                f"BLK MRKT Team"
            ),
            html=(
                f'<p>Hi <strong>{username}</strong>,</p>'
                f'<p>Click the button below to verify your email address:</p>'
                f'<p><a href="{verify_url}" style="background:#e63946;color:white;padding:12px 24px;'
                f'border-radius:4px;text-decoration:none;font-weight:bold;">Verify Email</a></p>'
                f'<p>Or copy this link: {verify_url}</p>'
                f'<p>This link expires in 24 hours.</p>'
                f'<p><small>BLK MRKT Team</small></p>'
            ),
        )
    else:
        print(f"[DEV] Verify URL for {email}: {verify_url}")

    if close_conn:
        conn.close()

    return token


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@auth_bp.route("/register", methods=["POST"])
def register(req):
    data = req.get_json(silent=True) or {}
    username      = (data.get("username") or "").strip()
    email         = (data.get("email") or "").strip().lower()
    password      = data.get("password") or ""
    role          = data.get("role", "fan")
    city          = (data.get("city") or "").strip()
    tos_agreed    = bool(data.get("tos_agreed") or data.get("tosAgreed"))

    # Validation
    if not username or len(username) < 2:
        return jsonify({"error": "Username must be at least 2 characters"}, 400), 400
    if not email or not _EMAIL_RE.match(email):
        return jsonify({"error": "Valid email address is required"}, 400), 400
    if len(password) < 6:
        return jsonify({"error": "Password must be at least 6 characters"}, 400), 400
    if role not in ("artist", "fan", "label"):
        return jsonify({"error": "Role must be 'artist', 'fan', or 'label'"}, 400), 400
    if not tos_agreed:
        return jsonify({"error": "You must agree to the Terms of Service to create an account"}, 400), 400

    pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    user_id = new_id()

    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO users
               (id, username, email, password_hash, role, city, tos_agreed, email_verified)
               VALUES (?, ?, ?, ?, ?, ?, 1, 0)""",
            (user_id, username, email, pw_hash, role, city),
        )
        conn.commit()

        # Send verification email (non-blocking — failure doesn't break registration)
        try:
            _send_verification_email(user_id, email, username, conn=conn)
        except Exception as e:
            print(f"[auth] Verification email error: {e}")

    except Exception as e:
        if "UNIQUE" in str(e):
            return jsonify({"error": "Username or email already taken"}, 409), 409
        return jsonify({"error": "Registration failed"}, 500), 500
    finally:
        conn.close()

    access  = create_token(user_id, role, "access")
    refresh = create_token(user_id, role, "refresh")

    return jsonify({
        "user": {
            "id": user_id, "username": username, "email": email,
            "role": role, "city": city, "email_verified": False,
        },
        "access_token": access,
        "refresh_token": refresh,
        "email_verification_required": EMAIL_ENABLED,
        "message": (
            "Account created. Please check your email to verify your address."
            if EMAIL_ENABLED else "Account created."
        ),
    }, 201), 201


@auth_bp.route("/login", methods=["POST"])
def login(req):
    data     = req.get_json(silent=True) or {}
    email    = (data.get("email") or "").strip().lower()
    password = data.get("password") or ""

    if not email or not password:
        return jsonify({"error": "Email and password required"}, 400), 400

    conn = get_db()
    try:
        user = row_to_dict(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())
    finally:
        conn.close()

    # Always run bcrypt regardless of whether the user exists — prevents timing-based
    # email enumeration (non-existent email would otherwise return ~0ms, real email ~200ms).
    hash_to_check = user["password_hash"] if user else _DUMMY_HASH
    password_valid = bcrypt.checkpw(password.encode(), hash_to_check.encode())

    if not user or not password_valid:
        return jsonify({"error": "Invalid credentials"}, 401), 401

    if user.get("suspended"):
        return jsonify({"error": "Account suspended"}, 403), 403

    access  = create_token(user["id"], user["role"], "access")
    refresh = create_token(user["id"], user["role"], "refresh")

    return jsonify({
        "user": {
            "id": user["id"], "username": user["username"],
            "email": user["email"], "role": user["role"],
            "city": user.get("city"), "email_verified": bool(user.get("email_verified")),
        },
        "access_token": access,
        "refresh_token": refresh,
    })


@auth_bp.route("/refresh", methods=["POST"])
def refresh_token(req):
    data    = req.get_json(silent=True) or {}
    token   = data.get("refresh_token", "")
    payload = decode_token(token)

    if not payload or payload.get("type") != "refresh":
        return jsonify({"error": "Invalid or expired refresh token"}, 401), 401

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (payload["sub"],)
        ).fetchone())
    finally:
        conn.close()

    if not user:
        return jsonify({"error": "User not found"}, 404), 404

    access = create_token(user["id"], user["role"], "access")
    return jsonify({"access_token": access})


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me(req):
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            """SELECT id, username, email, role, city, bio, avatar_url,
                      email_verified, tos_agreed, stripe_connect_id, stripe_onboarded,
                      tier, tier_expires_at, billing_cycle,
                      created_at
               FROM users WHERE id = ?""",
            (srv.g.user_id,),
        ).fetchone())
    finally:
        conn.close()

    if not user:
        return jsonify({"error": "User not found"}, 404), 404
    return jsonify({"user": user})


# ---------------------------------------------------------------------------
# Email verification
# ---------------------------------------------------------------------------

@auth_bp.route("/verify", methods=["GET"])
def verify_email(req):
    """
    GET /api/auth/verify?token=xxx
    Marks the user's email as verified.
    Redirects to /?verified=1 on success.
    """
    token = (req.query.get("token") or "").strip()
    if not token:
        return jsonify({"error": "Verification token is required"}, 400), 400

    conn = get_db()
    try:
        row = conn.execute(
            """SELECT v.user_id, v.expires_at
               FROM email_verifications v
               WHERE v.token = ?""",
            (token,)
        ).fetchone()

        if not row:
            return jsonify({"error": "Invalid or expired verification token"}, 400), 400

        # Check expiry
        from datetime import datetime
        expires_at = row["expires_at"]
        now_str = utcnow()
        if expires_at < now_str:
            conn.execute("DELETE FROM email_verifications WHERE token = ?", (token,))
            conn.commit()
            return jsonify({"error": "Verification token has expired. Please request a new one."}, 400), 400

        user_id = row["user_id"]
        conn.execute("UPDATE users SET email_verified = 1 WHERE id = ?", (user_id,))
        conn.execute("DELETE FROM email_verifications WHERE user_id = ?", (user_id,))
        conn.commit()

        user = row_to_dict(conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        conn.close()

    # Issue new tokens with verified status baked in
    access  = create_token(user["id"], user["role"], "access")
    refresh = create_token(user["id"], user["role"], "refresh")

    # Return JSON — frontend handles the redirect
    return jsonify({
        "verified": True,
        "access_token": access,
        "refresh_token": refresh,
        "message": "Email verified successfully.",
    })


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification(req):
    """
    POST /api/auth/resend-verification
    Body: { "email": "..." }
    Rate-limited: 1 per 5 minutes per email.
    """
    data  = req.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email:
        return jsonify({"error": "Email is required"}, 400), 400

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, username, email_verified FROM users WHERE email = ?", (email,)
        ).fetchone())

        if not user:
            # Don't reveal whether email exists — return 200
            return jsonify({"message": "If your email is registered, a verification link has been sent."})

        if user["email_verified"]:
            return jsonify({"message": "Your email is already verified."})

        # Rate limit: check last token creation time
        last = conn.execute(
            "SELECT created_at FROM email_verifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user["id"],)
        ).fetchone()

        if last:
            last_ts = last["created_at"]
            now_str = utcnow()
            # Compare strings — both are ISO UTC
            import datetime
            try:
                last_dt = datetime.datetime.strptime(last_ts, "%Y-%m-%dT%H:%M:%SZ")
                now_dt  = datetime.datetime.strptime(now_str, "%Y-%m-%dT%H:%M:%SZ")
                elapsed = (now_dt - last_dt).total_seconds()
                if elapsed < RESET_RATE_LIMIT_S:
                    wait = int(RESET_RATE_LIMIT_S - elapsed)
                    return jsonify({
                        "error": f"Please wait {wait} seconds before requesting another verification email."
                    }, 429), 429
            except Exception:
                pass

        _send_verification_email(user["id"], email, user.get("username", email), conn=conn)
    finally:
        conn.close()

    return jsonify({"message": "If your email is registered, a verification link has been sent."})


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@auth_bp.route("/forgot-password", methods=["POST"])
def forgot_password(req):
    """
    POST /api/auth/forgot-password
    Body: { "email": "..." }
    Rate-limited: 1 request per 5 minutes per email.
    Always returns 200 to avoid email enumeration.
    """
    data  = req.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()

    if not email or not _EMAIL_RE.match(email):
        return jsonify({"message": "If your email is registered, a reset link has been sent."})

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, username FROM users WHERE email = ?", (email,)
        ).fetchone())

        if not user:
            return jsonify({"message": "If your email is registered, a reset link has been sent."})

        # Rate limit
        last = conn.execute(
            "SELECT created_at FROM password_resets WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user["id"],)
        ).fetchone()
        if last:
            import datetime
            try:
                last_dt = datetime.datetime.strptime(last["created_at"], "%Y-%m-%dT%H:%M:%SZ")
                now_dt  = datetime.datetime.strptime(utcnow(), "%Y-%m-%dT%H:%M:%SZ")
                if (now_dt - last_dt).total_seconds() < RESET_RATE_LIMIT_S:
                    # Still return 200 to avoid enumeration, but don't send
                    return jsonify({"message": "If your email is registered, a reset link has been sent."})
            except Exception:
                pass

        # Create reset token
        token = secrets.token_urlsafe(32)
        import datetime
        expires_at = (
            datetime.datetime.utcnow() + datetime.timedelta(seconds=RESET_TOKEN_TTL)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Remove old tokens for this user
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user["id"],))
        conn.execute(
            "INSERT INTO password_resets (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user["id"], expires_at),
        )
        conn.commit()

        reset_url = f"{PUBLIC_URL}/?reset_token={token}"

        if EMAIL_ENABLED:
            from email_utils import send_email
            send_email(
                to=email,
                subject="Reset your BLK MRKT password",
                text=(
                    f"Hi {user['username']},\n\n"
                    f"Click the link below to reset your password:\n\n"
                    f"{reset_url}\n\n"
                    f"This link expires in 1 hour.\n\n"
                    f"If you didn't request a password reset, ignore this email.\n\n"
                    f"BLK MRKT Team"
                ),
                html=(
                    f'<p>Hi <strong>{user["username"]}</strong>,</p>'
                    f'<p>Click below to reset your password:</p>'
                    f'<p><a href="{reset_url}" style="background:#e63946;color:white;padding:12px 24px;'
                    f'border-radius:4px;text-decoration:none;font-weight:bold;">Reset Password</a></p>'
                    f'<p>Or copy this link: {reset_url}</p>'
                    f'<p>This link expires in 1 hour.</p>'
                    f'<p><small>BLK MRKT Team</small></p>'
                ),
            )
        else:
            print(f"[DEV] Password reset URL for {email}: {reset_url}")

    finally:
        conn.close()

    return jsonify({"message": "If your email is registered, a reset link has been sent."})


@auth_bp.route("/reset-password", methods=["POST"])
def reset_password(req):
    """
    POST /api/auth/reset-password
    Body: { "token": "...", "new_password": "..." }
    """
    data         = req.get_json(silent=True) or {}
    token        = (data.get("token") or "").strip()
    new_password = data.get("new_password") or data.get("password") or ""

    if not token:
        return jsonify({"error": "Reset token is required"}, 400), 400
    if len(new_password) < 6:
        return jsonify({"error": "New password must be at least 6 characters"}, 400), 400

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT user_id, expires_at FROM password_resets WHERE token = ?", (token,)
        ).fetchone()

        if not row:
            return jsonify({"error": "Invalid or expired reset token"}, 400), 400

        if row["expires_at"] < utcnow():
            conn.execute("DELETE FROM password_resets WHERE token = ?", (token,))
            conn.commit()
            return jsonify({"error": "Reset token has expired. Please request a new one."}, 400), 400

        user_id = row["user_id"]
        pw_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw_hash, user_id))
        conn.execute("DELETE FROM password_resets WHERE user_id = ?", (user_id,))
        conn.commit()

        user = row_to_dict(conn.execute(
            "SELECT id, role FROM users WHERE id = ?", (user_id,)
        ).fetchone())
    finally:
        conn.close()

    # Issue fresh tokens
    access  = create_token(user["id"], user["role"], "access")
    refresh = create_token(user["id"], user["role"], "refresh")

    return jsonify({
        "message": "Password reset successfully.",
        "access_token": access,
        "refresh_token": refresh,
    })
