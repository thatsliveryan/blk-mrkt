"""
BLK MRKT — Stripe Connect (Artist Payouts)

Artists onboard to Stripe Express once. After that, every fan purchase
automatically splits: 85% to artist, 15% platform fee, all via Stripe Connect.

Routes:
  POST /api/connect/onboard    — start Stripe Connect Express onboarding
  GET  /api/connect/status     — check Connect account status
  GET  /api/connect/dashboard  — generate Stripe dashboard login link
"""

import json
import urllib.request
import urllib.parse

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, row_to_dict
from config import STRIPE_SECRET_KEY, PUBLIC_URL, STRIPE_CONNECT_ENABLED

connect_bp = Blueprint("connect", prefix="/api/connect")


def _stripe(method, path, data=None):
    """Raw Stripe API call using stdlib urllib."""
    url = f"https://api.stripe.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = urllib.parse.urlencode(data or {}).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()), None
    except urllib.error.HTTPError as e:
        err = json.loads(e.read())
        return None, err.get("error", {}).get("message", "Stripe error")
    except Exception as e:
        return None, str(e)


def get_artist_connect_id(user_id):
    """Return the Stripe Connect account ID for an artist, or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT stripe_connect_id, stripe_onboarded FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if row:
            return row["stripe_connect_id"], bool(row["stripe_onboarded"])
        return None, False
    finally:
        conn.close()


@connect_bp.route("/onboard", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def onboard(req):
    """
    Create or re-use a Stripe Connect Express account for the artist,
    then return an Account Link URL for onboarding.
    """
    if not STRIPE_SECRET_KEY:
        return jsonify({
            "error": "Stripe not configured",
            "message": "Set STRIPE_SECRET_KEY in environment variables.",
        }, 503), 503

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT id, email, username, stripe_connect_id FROM users WHERE id = ?",
            (srv.g.user_id,)
        ).fetchone())
    finally:
        conn.close()

    connect_id = user.get("stripe_connect_id")

    # Create Express account if not exists
    if not connect_id:
        result, err = _stripe("POST", "/accounts", {
            "type": "express",
            "country": "US",
            "email": user["email"],
            "capabilities[transfers][requested]": "true",
            "capabilities[card_payments][requested]": "true",
            "business_profile[mcc]": "5735",       # Music stores
            "business_profile[product_description]": "Independent music artist on BLK MRKT",
            "metadata[user_id]": srv.g.user_id,
            "metadata[username]": user["username"],
        })
        if err:
            return jsonify({"error": f"Stripe error: {err}"}, 502), 502

        connect_id = result["id"]
        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET stripe_connect_id = ? WHERE id = ?",
                (connect_id, srv.g.user_id)
            )
            conn.commit()
        finally:
            conn.close()

    # Create Account Link for onboarding
    result, err = _stripe("POST", "/account_links", {
        "account": connect_id,
        "refresh_url": f"{PUBLIC_URL}/?connect=refresh",
        "return_url": f"{PUBLIC_URL}/?connect=success",
        "type": "account_onboarding",
    })
    if err:
        return jsonify({"error": f"Stripe error: {err}"}, 502), 502

    return jsonify({
        "onboard_url": result["url"],
        "connect_id": connect_id,
    })


@connect_bp.route("/status", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def connect_status(req):
    """Check the artist's Stripe Connect account status."""
    connect_id, onboarded = get_artist_connect_id(srv.g.user_id)

    if not connect_id:
        return jsonify({
            "connected": False,
            "payouts_enabled": False,
            "charges_enabled": False,
            "onboarded": False,
        })

    if not STRIPE_SECRET_KEY:
        return jsonify({
            "connected": True,
            "payouts_enabled": False,
            "charges_enabled": False,
            "onboarded": onboarded,
            "connect_id": connect_id,
            "note": "Stripe not configured",
        })

    # Fetch live status from Stripe
    result, err = _stripe("GET", f"/accounts/{connect_id}")
    if err:
        return jsonify({"error": err}, 502), 502

    payouts_enabled = result.get("payouts_enabled", False)
    charges_enabled = result.get("charges_enabled", False)

    # Update onboarded status in DB if Stripe says it's ready
    if payouts_enabled and charges_enabled and not onboarded:
        conn = get_db()
        try:
            conn.execute(
                "UPDATE users SET stripe_onboarded = 1 WHERE id = ?", (srv.g.user_id,)
            )
            conn.commit()
        finally:
            conn.close()

    return jsonify({
        "connected": True,
        "payouts_enabled": payouts_enabled,
        "charges_enabled": charges_enabled,
        "onboarded": payouts_enabled and charges_enabled,
        "connect_id": connect_id,
        "details_submitted": result.get("details_submitted", False),
        "requirements": result.get("requirements", {}).get("currently_due", []),
    })


@connect_bp.route("/dashboard", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def connect_dashboard(req):
    """Generate a Stripe Connect login link for the artist's dashboard."""
    connect_id, onboarded = get_artist_connect_id(srv.g.user_id)

    if not connect_id:
        return jsonify({"error": "No Connect account — complete onboarding first"}, 404), 404

    if not STRIPE_SECRET_KEY:
        return jsonify({"error": "Stripe not configured"}, 503), 503

    result, err = _stripe("POST", f"/accounts/{connect_id}/login_links")
    if err:
        return jsonify({"error": f"Stripe error: {err}"}, 502), 502

    return jsonify({"dashboard_url": result["url"]})
