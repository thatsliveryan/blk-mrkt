"""
BLK MRKT — Stripe Payment Integration (Revenue Stream: Fan Transactions)

Free drops (price = 0) → direct claim, no Stripe.
Paid drops (price > 0) → Stripe Checkout Session → webhook → grant access.

Endpoints:
  POST /api/payments/checkout      — create Stripe Checkout Session
  POST /api/payments/webhook       — Stripe event handler (no auth)
  GET  /api/payments/history       — transaction history (fan or artist view)
  GET  /api/payments/config        — return publishable key for frontend
"""

import json
import hmac
import hashlib
import time

from server import Blueprint, jsonify, Response
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list
from config import (
    STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET, STRIPE_PUBLISHABLE_KEY, PUBLIC_URL
)

payments_bp = Blueprint("payments", prefix="/api/payments")

# ---------------------------------------------------------------------------
# Stripe helper — minimal REST calls without the SDK to avoid import issues
# at module load time if stripe isn't installed yet.
# Falls back gracefully if STRIPE_SECRET_KEY is not set.
# ---------------------------------------------------------------------------

def _stripe_request(method, path, data=None):
    """Make a Stripe API call using urllib (stdlib). Returns parsed JSON."""
    import urllib.request
    import urllib.parse

    url = f"https://api.stripe.com/v1{path}"
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    body = urllib.parse.urlencode(data or {}).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read())


def _flatten_params(d, prefix=""):
    """Flatten nested dict for Stripe's form encoding (e.g. metadata[key]=val)."""
    result = {}
    for k, v in d.items():
        full_key = f"{prefix}[{k}]" if prefix else k
        if isinstance(v, dict):
            result.update(_flatten_params(v, full_key))
        else:
            result[full_key] = str(v) if v is not None else ""
    return result


def create_checkout_session(drop, user, access_type="stream"):
    """Create a Stripe Checkout Session for a drop purchase."""
    if not STRIPE_SECRET_KEY:
        return None, "Stripe not configured"

    price_cents = int((drop["access_price"] if access_type == "stream" else (drop.get("own_price") or drop["access_price"])) * 100)
    drop_title = drop["title"][:80]  # Stripe has a name length limit

    params = _flatten_params({
        "mode": "payment",
        "success_url": f"{PUBLIC_URL}/?drop={drop['id']}&payment=success",
        "cancel_url":  f"{PUBLIC_URL}/?drop={drop['id']}&payment=cancelled",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(price_cents),
        "line_items[0][price_data][product_data][name]": drop_title,
        "line_items[0][price_data][product_data][description]": f"{'Stream access' if access_type == 'stream' else 'Ownership'} — BLK MRKT Drop",
        "line_items[0][quantity]": "1",
        "metadata[drop_id]": drop["id"],
        "metadata[user_id]": user["id"],
        "metadata[access_type]": access_type,
        "metadata[price_cents]": str(price_cents),
        "client_reference_id": user["id"],
    })

    result = _stripe_request("POST", "/checkout/sessions", params)
    if "url" in result:
        return result["url"], None
    return None, result.get("error", {}).get("message", "Stripe error")


def create_boost_checkout_session(boost_id, drop_title, budget_cents, artist):
    """Create a Stripe Checkout Session for a boost payment."""
    if not STRIPE_SECRET_KEY:
        return None, "Stripe not configured"

    params = _flatten_params({
        "mode": "payment",
        "success_url": f"{PUBLIC_URL}/?boost={boost_id}&payment=success",
        "cancel_url":  f"{PUBLIC_URL}/?boost={boost_id}&payment=cancelled",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(budget_cents),
        "line_items[0][price_data][product_data][name]": f"BLK MRKT Boost — {drop_title[:60]}",
        "line_items[0][price_data][product_data][description]": "Drop amplification boost",
        "line_items[0][quantity]": "1",
        "metadata[boost_id]": boost_id,
        "metadata[artist_id]": artist["id"],
        "metadata[budget_cents]": str(budget_cents),
        "metadata[type]": "boost",
        "client_reference_id": artist["id"],
    })

    result = _stripe_request("POST", "/checkout/sessions", params)
    if "url" in result:
        return result["url"], None
    return None, result.get("error", {}).get("message", "Stripe error")


def _verify_webhook_signature(payload_bytes, sig_header):
    """Verify Stripe webhook signature (HMAC-SHA256 with timestamp tolerance)."""
    if not STRIPE_WEBHOOK_SECRET:
        return True  # Skip in dev/test mode

    try:
        parts = {p.split("=")[0]: p.split("=")[1] for p in sig_header.split(",")}
        ts = int(parts.get("t", 0))
        sig = parts.get("v1", "")

        if abs(time.time() - ts) > 300:  # 5-minute tolerance
            return False

        signed_payload = f"{ts}.".encode() + payload_bytes
        expected = hmac.new(
            STRIPE_WEBHOOK_SECRET.encode(),
            signed_payload,
            hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@payments_bp.route("/config", methods=["GET"])
def stripe_config(req):
    """Return the Stripe publishable key for the frontend."""
    return jsonify({
        "publishable_key": STRIPE_PUBLISHABLE_KEY,
        "configured": bool(STRIPE_SECRET_KEY),
    })


@payments_bp.route("/checkout", methods=["POST"])
@require_auth
def create_checkout(req):
    """Create a Stripe Checkout Session for a drop purchase."""
    data = req.get_json(silent=True) or {}
    drop_id     = data.get("drop_id", "").strip()
    access_type = data.get("access_type", "stream")

    if not drop_id:
        return jsonify({"error": "drop_id required"}, 400), 400
    if access_type not in ("stream", "own"):
        return jsonify({"error": "access_type must be stream or own"}, 400), 400

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404
        if drop["status"] != "live":
            return jsonify({"error": "Drop is not live"}, 400), 400
        if drop.get("total_supply") is not None and drop.get("remaining_supply", 0) <= 0:
            return jsonify({"error": "SOLD_OUT"}, 410), 410

        # Check if already has access
        existing = row_to_dict(conn.execute(
            "SELECT id FROM drop_access WHERE user_id = ? AND drop_id = ? AND access_type = ?",
            (srv.g.user_id, drop_id, access_type)
        ).fetchone())
        if existing:
            return jsonify({"error": "Already have access"}, 409), 409

        user = row_to_dict(conn.execute(
            "SELECT id, username, email FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
    finally:
        conn.close()

    price = drop["access_price"] if access_type == "stream" else (drop.get("own_price") or drop["access_price"])

    # Free drop — use direct claim instead
    if price <= 0:
        return jsonify({"error": "Free drops use /api/drops/:id/access directly"}, 400), 400

    checkout_url, err = create_checkout_session(drop, user, access_type)
    if err:
        return jsonify({"error": err}, 502), 502

    # Record pending transaction
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO transactions (id, user_id, drop_id, amount_cents, type, status)
               VALUES (?, ?, ?, ?, 'drop_purchase', 'pending')""",
            (new_id(), srv.g.user_id, drop_id, int(price * 100))
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"checkout_url": checkout_url, "drop_id": drop_id, "access_type": access_type})


@payments_bp.route("/webhook", methods=["POST"])
def stripe_webhook(req):
    """Handle Stripe webhook events. No auth — verified by signature."""
    sig = req.headers.get("stripe-signature", "")
    payload = req.body

    if not _verify_webhook_signature(payload, sig):
        return jsonify({"error": "Invalid signature"}, 400), 400

    try:
        event = json.loads(payload)
    except Exception:
        return jsonify({"error": "Invalid JSON"}, 400), 400

    event_type = event.get("type", "")
    obj = event.get("data", {}).get("object", {})

    if event_type == "checkout.session.completed":
        _handle_checkout_completed(obj)
    elif event_type == "payment_intent.payment_failed":
        _handle_payment_failed(obj)

    # Always return 200 immediately to Stripe
    return jsonify({"received": True})


def _handle_checkout_completed(session):
    """Grant access / activate boost when Stripe payment completes."""
    meta = session.get("metadata", {})
    session_id = session.get("id", "")
    payment_intent = session.get("payment_intent", "")
    txn_type = meta.get("type", "drop_purchase")

    if txn_type == "boost":
        _activate_boost(meta, session_id, payment_intent)
        return

    # Drop purchase
    drop_id     = meta.get("drop_id")
    user_id     = meta.get("user_id")
    access_type = meta.get("access_type", "stream")
    price_cents = int(meta.get("price_cents", 0))

    if not drop_id or not user_id:
        return

    conn = get_db()
    try:
        drop = row_to_dict(conn.execute("SELECT * FROM drops WHERE id = ?", (drop_id,)).fetchone())
        if not drop:
            return

        # Idempotency — don't double-grant
        existing = row_to_dict(conn.execute(
            "SELECT id FROM drop_access WHERE user_id = ? AND drop_id = ? AND access_type = ?",
            (user_id, drop_id, access_type)
        ).fetchone())
        if existing:
            return

        # Record fan number
        fan_number = conn.execute(
            "SELECT COUNT(*) as c FROM drop_access WHERE drop_id = ? AND access_type = ?",
            (drop_id, access_type)
        ).fetchone()["c"] + 1

        # Decrement supply atomically
        if drop.get("total_supply") is not None:
            cur = conn.execute(
                "UPDATE drops SET remaining_supply = remaining_supply - 1 WHERE id = ? AND remaining_supply > 0",
                (drop_id,)
            )
            if cur.rowcount == 0:
                return  # Sold out race condition

        # Grant access
        conn.execute(
            "INSERT INTO drop_access (user_id, drop_id, access_type, price_paid, fan_number) VALUES (?, ?, ?, ?, ?)",
            (user_id, drop_id, access_type, price_cents / 100, fan_number)
        )

        # Log a play engagement on claim (claim action is not in engagement schema)
        conn.execute(
            "INSERT INTO drop_engagement (user_id, drop_id, action) VALUES (?, ?, 'play')",
            (user_id, drop_id)
        )

        # Update/insert transaction record
        existing_txn = row_to_dict(conn.execute(
            "SELECT id FROM transactions WHERE user_id = ? AND drop_id = ? AND status = 'pending'",
            (user_id, drop_id)
        ).fetchone())
        if existing_txn:
            conn.execute(
                "UPDATE transactions SET status = 'completed', stripe_session_id = ?, stripe_payment_intent = ? WHERE id = ?",
                (session_id, payment_intent, existing_txn["id"])
            )
        else:
            conn.execute(
                """INSERT INTO transactions (id, user_id, drop_id, amount_cents, stripe_session_id,
                   stripe_payment_intent, type, status) VALUES (?, ?, ?, ?, ?, ?, 'drop_purchase', 'completed')""",
                (new_id(), user_id, drop_id, price_cents, session_id, payment_intent)
            )

        # Expire if sold out
        updated = row_to_dict(conn.execute(
            "SELECT remaining_supply, total_supply FROM drops WHERE id = ?", (drop_id,)
        ).fetchone())
        if updated and updated["total_supply"] is not None and (updated["remaining_supply"] or 0) <= 0:
            conn.execute("UPDATE drops SET status = 'expired' WHERE id = ?", (drop_id,))

        conn.commit()

        # Award badges asynchronously (don't block webhook)
        try:
            from badges import check_and_award_badges
            check_and_award_badges(user_id, drop_id, conn=None)
        except Exception:
            pass

    finally:
        conn.close()


def _activate_boost(meta, session_id, payment_intent):
    """Activate a boost after Stripe payment."""
    from datetime import datetime, timezone, timedelta

    boost_id    = meta.get("boost_id")
    budget_cents = int(meta.get("budget_cents", 0))
    if not boost_id:
        return

    conn = get_db()
    try:
        boost = row_to_dict(conn.execute("SELECT * FROM boosts WHERE id = ?", (boost_id,)).fetchone())
        if not boost or boost["status"] != "pending":
            return

        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=boost.get("duration_hours", 24))
        started_at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        expires_at = expires.strftime("%Y-%m-%dT%H:%M:%SZ")

        conn.execute(
            """UPDATE boosts SET status = 'active', stripe_session_id = ?,
               started_at = ?, expires_at = ? WHERE id = ?""",
            (session_id, started_at, expires_at, boost_id)
        )
        conn.execute("UPDATE drops SET boost_active = 1 WHERE id = ?", (boost["drop_id"],))

        # Record transaction
        conn.execute(
            """INSERT INTO transactions (id, user_id, drop_id, amount_cents, stripe_session_id,
               stripe_payment_intent, type, status, metadata)
               VALUES (?, ?, ?, ?, ?, ?, 'boost', 'completed', ?)""",
            (new_id(), boost["artist_id"], boost["drop_id"], budget_cents,
             session_id, payment_intent, json.dumps({"boost_id": boost_id}))
        )
        conn.commit()
    finally:
        conn.close()


def _handle_payment_failed(payment_intent_obj):
    """Mark pending transactions as failed."""
    pi_id = payment_intent_obj.get("id", "")
    conn = get_db()
    try:
        conn.execute(
            "UPDATE transactions SET status = 'failed' WHERE stripe_payment_intent = ? AND status = 'pending'",
            (pi_id,)
        )
        conn.commit()
    finally:
        conn.close()


@payments_bp.route("/history", methods=["GET"])
@require_auth
def payment_history(req):
    """Transaction history — fan sees purchases, artist sees earnings."""
    view = req.args.get("view", "fan")
    limit = min(int(req.args.get("limit", 50)), 200)

    conn = get_db()
    try:
        if view == "artist":
            # Artist earnings: all completed transactions on their drops
            rows = rows_to_list(conn.execute(
                """SELECT t.*, d.title as drop_title, u.username as buyer_username
                   FROM transactions t
                   JOIN drops d ON t.drop_id = d.id
                   LEFT JOIN users u ON t.user_id = u.id
                   WHERE d.artist_id = ? AND t.status = 'completed'
                   ORDER BY t.created_at DESC LIMIT ?""",
                (srv.g.user_id, limit)
            ).fetchall())

            total_cents = conn.execute(
                """SELECT COALESCE(SUM(t.amount_cents), 0) as total
                   FROM transactions t JOIN drops d ON t.drop_id = d.id
                   WHERE d.artist_id = ? AND t.status = 'completed' AND t.type = 'drop_purchase'""",
                (srv.g.user_id,)
            ).fetchone()["total"]

            # Per-drop breakdown
            by_drop = rows_to_list(conn.execute(
                """SELECT d.id, d.title, COUNT(t.id) as sales,
                          COALESCE(SUM(t.amount_cents), 0) as revenue_cents
                   FROM drops d
                   LEFT JOIN transactions t ON t.drop_id = d.id AND t.status = 'completed' AND t.type = 'drop_purchase'
                   WHERE d.artist_id = ?
                   GROUP BY d.id ORDER BY revenue_cents DESC LIMIT 20""",
                (srv.g.user_id,)
            ).fetchall())

            return jsonify({
                "transactions": rows,
                "total_earned_cents": total_cents,
                "total_earned": round(total_cents / 100, 2),
                "by_drop": by_drop,
            })
        else:
            # Fan purchase history
            rows = rows_to_list(conn.execute(
                """SELECT t.*, d.title as drop_title, u.username as artist_name
                   FROM transactions t
                   JOIN drops d ON t.drop_id = d.id
                   JOIN users u ON d.artist_id = u.id
                   WHERE t.user_id = ? AND t.status = 'completed'
                   ORDER BY t.created_at DESC LIMIT ?""",
                (srv.g.user_id, limit)
            ).fetchall())

            total_cents = conn.execute(
                "SELECT COALESCE(SUM(amount_cents), 0) as total FROM transactions WHERE user_id = ? AND status = 'completed'",
                (srv.g.user_id,)
            ).fetchone()["total"]

            return jsonify({
                "transactions": rows,
                "total_spent_cents": total_cents,
                "total_spent": round(total_cents / 100, 2),
            })
    finally:
        conn.close()
