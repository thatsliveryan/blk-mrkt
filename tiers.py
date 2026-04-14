"""
BLK MRKT — Artist Tier / Subscription System (Revenue Stream 2)

Four tiers that reduce the platform fee on drop sales:
  Free     — 15% fee, $0/mo,          $50/mo boost cap
  Hustler  —  8% fee, $9/mo  |  $90/yr,  $100/mo boost cap
  Pro      —  3% fee, $29/mo | $290/yr,  $250/mo boost cap
  Label    —  1% fee, $99/mo | $990/yr,  unlimited boosts

Annual billing = 10 months for the price of 12 (2 months free).

Endpoints:
  GET  /api/tiers               — public: all tier definitions
  GET  /api/tiers/me            — artist: current tier + renewal info
  POST /api/tiers/checkout      — artist: create Stripe subscription checkout
  POST /api/tiers/cancel        — artist: cancel subscription at period end
  POST /api/tiers/reactivate    — artist: reactivate cancelled subscription
  GET  /api/tiers/savings       — artist: "math sells itself" savings dashboard
  POST /api/tiers/webhook-sync  — internal: resync tier from Stripe (called by webhook)
"""

import json
from datetime import datetime, timezone, timedelta

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, new_id, utcnow, row_to_dict, rows_to_list
from config import STRIPE_SECRET_KEY, STRIPE_PUBLISHABLE_KEY, PUBLIC_URL

tiers_bp = Blueprint("tiers", prefix="/api/tiers")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _stripe_request(method, path, data=None):
    """Make a Stripe API call using urllib (stdlib)."""
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


def get_tier_limits(conn=None):
    """Return all tier definitions keyed by tier name."""
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        rows = rows_to_list(conn.execute("SELECT * FROM tier_limits ORDER BY monthly_price_cents").fetchall())
        return {r["tier"]: r for r in rows}
    finally:
        if close:
            conn.close()


def get_artist_fee_rate(artist_id, conn=None):
    """
    Return the platform fee rate (0.0–1.0) for an artist based on their current tier.
    Called by payments.py at checkout time — this is the core revenue lever.
    """
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        row = conn.execute(
            "SELECT tier, tier_expires_at FROM users WHERE id = ?", (artist_id,)
        ).fetchone()
        if not row:
            return 0.15  # Default: full platform fee

        tier = row["tier"] or "free"
        tier_expires_at = row["tier_expires_at"]

        # Check if paid tier has expired
        if tier != "free" and tier_expires_at:
            try:
                exp = datetime.fromisoformat(tier_expires_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    tier = "free"  # Expired — fall back to free
            except Exception:
                tier = "free"

        # Look up fee from tier_limits
        limits = conn.execute(
            "SELECT platform_fee_pct FROM tier_limits WHERE tier = ?", (tier,)
        ).fetchone()
        if limits:
            return float(limits["platform_fee_pct"])
        return 0.15
    finally:
        if close:
            conn.close()


def get_boost_monthly_spent(artist_id, conn=None):
    """Return total boost spending this calendar month in cents."""
    close = False
    if conn is None:
        conn = get_db()
        close = True
    try:
        now = datetime.now(timezone.utc)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        result = conn.execute(
            """SELECT COALESCE(SUM(budget_cents), 0) as total
               FROM boosts
               WHERE artist_id = ? AND status IN ('active', 'completed')
               AND started_at >= ?""",
            (artist_id, month_start.strftime("%Y-%m-%dT%H:%M:%SZ"))
        ).fetchone()
        return result["total"] if result else 0
    finally:
        if close:
            conn.close()


def _ensure_stripe_customer(user, conn):
    """Get or create a Stripe customer for this user. Returns customer_id or None."""
    if not STRIPE_SECRET_KEY:
        return None

    if user.get("stripe_customer_id"):
        return user["stripe_customer_id"]

    result = _stripe_request("POST", "/customers", {
        "email": user["email"],
        "name": user["username"],
        "metadata[user_id]": user["id"],
    })
    if "id" in result:
        cid = result["id"]
        conn.execute(
            "UPDATE users SET stripe_customer_id = ? WHERE id = ?",
            (cid, user["id"])
        )
        conn.commit()
        return cid
    return None


def _get_or_create_stripe_price(tier_row, billing_period="monthly"):
    """
    Get (or create) Stripe price ID for a tier and billing period.
    billing_period: "monthly" | "annual"
    """
    if not STRIPE_SECRET_KEY:
        return None

    id_field = "stripe_annual_price_id" if billing_period == "annual" else "stripe_price_id"
    if tier_row.get(id_field):
        return tier_row[id_field]

    amount_cents = (
        tier_row["annual_price_cents"] if billing_period == "annual"
        else tier_row["monthly_price_cents"]
    )
    if not amount_cents:
        return None

    # Create product (or reuse existing one by looking up by metadata)
    product = _stripe_request("POST", "/products", {
        "name": f"BLK MRKT {tier_row['label']} Plan",
        "description": tier_row.get("description", ""),
        "metadata[tier]": tier_row["tier"],
    })
    if "error" in product:
        return None

    # Create recurring price for the requested interval
    interval = "year" if billing_period == "annual" else "month"
    price = _stripe_request("POST", "/prices", {
        "product": product["id"],
        "unit_amount": str(amount_cents),
        "currency": "usd",
        "recurring[interval]": interval,
        "metadata[tier]": tier_row["tier"],
        "metadata[billing_period]": billing_period,
    })
    if "error" in price:
        return None

    price_id = price["id"]

    # Persist so we don't recreate next time
    conn = get_db()
    try:
        conn.execute(
            f"UPDATE tier_limits SET {id_field} = ? WHERE tier = ?",
            (price_id, tier_row["tier"])
        )
        conn.commit()
    finally:
        conn.close()

    return price_id


def apply_tier_from_stripe(user_id, stripe_sub):
    """
    Called by webhook to sync a user's tier from a Stripe subscription object.
    stripe_sub is the subscription object from the Stripe event.
    """
    status = stripe_sub.get("status", "")
    tier = stripe_sub.get("metadata", {}).get("tier", "free")
    sub_id = stripe_sub.get("id", "")
    current_period_end = stripe_sub.get("current_period_end")

    tier_expires_at = None
    if current_period_end:
        try:
            exp = datetime.fromtimestamp(int(current_period_end), tz=timezone.utc)
            tier_expires_at = exp.strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            pass

    conn = get_db()
    try:
        user = row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())
        if not user:
            return

        old_tier = user.get("tier", "free")

        billing_period = stripe_sub.get("metadata", {}).get("billing_period", "monthly")

        if status in ("active", "trialing"):
            conn.execute(
                """UPDATE users SET tier = ?, tier_expires_at = ?,
                   stripe_subscription_id = ?, billing_cycle = ?
                   WHERE id = ?""",
                (tier, tier_expires_at, sub_id, billing_period, user_id)
            )
            action = "upgrade" if _tier_rank(tier) > _tier_rank(old_tier) else (
                "downgrade" if _tier_rank(tier) < _tier_rank(old_tier) else "subscribe"
            )
        elif status in ("canceled", "unpaid", "past_due"):
            conn.execute(
                "UPDATE users SET tier = 'free', tier_expires_at = NULL WHERE id = ?",
                (user_id,)
            )
            action = "expired" if status != "canceled" else "cancel"
            tier = "free"
        else:
            return

        # Log subscription history
        conn.execute(
            """INSERT INTO subscription_history
               (id, user_id, tier, action, stripe_subscription_id, amount_cents)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (new_id(), user_id, tier, action, sub_id, 0)
        )
        conn.commit()
    finally:
        conn.close()


def _tier_rank(tier):
    """Numeric rank for tier comparison."""
    return {"free": 0, "hustler": 1, "pro": 2, "label": 3}.get(tier, 0)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@tiers_bp.route("", methods=["GET"])
def list_tiers(req):
    """Public — return all tier definitions."""
    tiers = get_tier_limits()
    ordered = [tiers[t] for t in ("free", "hustler", "pro", "label") if t in tiers]
    return jsonify({"tiers": ordered})


@tiers_bp.route("/me", methods=["GET"])
@require_auth
def my_tier(req):
    """Return current user's tier info, renewal date, and upgrade options."""
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            """SELECT id, username, email, role, tier, tier_expires_at,
                      stripe_subscription_id, stripe_customer_id, billing_cycle
               FROM users WHERE id = ?""",
            (srv.g.user_id,)
        ).fetchone())
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        tiers = get_tier_limits(conn)
        current_tier = user.get("tier") or "free"
        tier_info = tiers.get(current_tier, tiers.get("free", {}))

        # Check expiry
        is_expired = False
        if current_tier != "free" and user.get("tier_expires_at"):
            try:
                exp = datetime.fromisoformat(user["tier_expires_at"].replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > exp:
                    is_expired = True
            except Exception:
                pass

        # Monthly boost spend
        boost_spent = get_boost_monthly_spent(srv.g.user_id, conn)
        boost_cap = tier_info.get("monthly_boost_cap_cents")

        # Recent sub history
        history = rows_to_list(conn.execute(
            """SELECT tier, action, amount_cents, created_at
               FROM subscription_history WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 10""",
            (srv.g.user_id,)
        ).fetchall())

    finally:
        conn.close()

    return jsonify({
        "tier": current_tier,
        "tier_info": tier_info,
        "tier_expires_at": user.get("tier_expires_at"),
        "is_expired": is_expired,
        "stripe_subscription_id": user.get("stripe_subscription_id"),
        "billing_cycle": user.get("billing_cycle", "monthly"),
        "boost_spent_this_month_cents": boost_spent,
        "boost_cap_cents": boost_cap,
        "boost_remaining_cents": max(0, boost_cap - boost_spent) if boost_cap is not None else None,
        "history": history,
    })


@tiers_bp.route("/checkout", methods=["POST"])
@require_auth
@require_role("artist", "label", "admin")
def tier_checkout(req):
    """
    Create a Stripe Checkout Session for a subscription.
    Body: { "tier": "hustler" | "pro" | "label", "billing_period": "monthly" | "annual" }
    Annual billing = 10 months for price of 12 (2 months free).
    """
    data = req.get_json(silent=True) or {}
    new_tier = (data.get("tier") or "").strip().lower()
    billing_period = (data.get("billing_period") or "monthly").strip().lower()

    if new_tier not in ("hustler", "pro", "label"):
        return jsonify({"error": "tier must be hustler, pro, or label"}, 400), 400
    if billing_period not in ("monthly", "annual"):
        return jsonify({"error": "billing_period must be monthly or annual"}, 400), 400

    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        current_tier = user.get("tier") or "free"
        current_billing = user.get("billing_cycle") or "monthly"
        if current_tier == new_tier and current_billing == billing_period:
            return jsonify({"error": f"You're already on the {new_tier} {billing_period} plan"}), 400

        if not STRIPE_SECRET_KEY:
            # Dev mode — apply tier immediately without Stripe
            days = 365 if billing_period == "annual" else 30
            exp = (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conn.execute(
                "UPDATE users SET tier = ?, tier_expires_at = ?, billing_cycle = ? WHERE id = ?",
                (new_tier, exp, billing_period, srv.g.user_id)
            )
            conn.execute(
                """INSERT INTO subscription_history (id, user_id, tier, action, amount_cents)
                   VALUES (?, ?, ?, 'subscribe', 0)""",
                (new_id(), srv.g.user_id, new_tier)
            )
            conn.commit()

            tiers = get_tier_limits(conn)
            tier_info = tiers.get(new_tier, {})
            price_cents = (
                tier_info.get("annual_price_cents", 0) if billing_period == "annual"
                else tier_info.get("monthly_price_cents", 0)
            )
            return jsonify({
                "dev_mode": True,
                "tier": new_tier,
                "billing_period": billing_period,
                "message": f"Tier set to {new_tier} ({billing_period}, dev mode — no Stripe configured)",
                "tier_expires_at": exp,
                "price_cents": price_cents,
            })

        tiers = get_tier_limits(conn)
        tier_row = tiers.get(new_tier)
        if not tier_row:
            return jsonify({"error": "Tier not found"}, 404), 404

        # Validate annual price exists
        if billing_period == "annual" and not tier_row.get("annual_price_cents"):
            return jsonify({"error": "Annual pricing not available for this tier"}), 400

        # Get/create Stripe customer
        customer_id = _ensure_stripe_customer(user, conn)
        if not customer_id:
            return jsonify({"error": "Could not create Stripe customer"}, 502), 502

        # Get/create Stripe price for the requested billing period
        price_id = _get_or_create_stripe_price(tier_row, billing_period)
        if not price_id:
            return jsonify({"error": "Could not create Stripe price for tier"}, 502), 502

        # Handle upgrade: cancel existing subscription first
        existing_sub = user.get("stripe_subscription_id")
        if existing_sub and current_tier != "free":
            _stripe_request("DELETE", f"/subscriptions/{existing_sub}")

        price_cents = (
            tier_row["annual_price_cents"] if billing_period == "annual"
            else tier_row["monthly_price_cents"]
        )

    finally:
        conn.close()

    # Create Stripe Checkout Session (subscription mode)
    params = {
        "mode": "subscription",
        "customer": customer_id,
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": f"{PUBLIC_URL}/?tier_success=1&tier={new_tier}&billing={billing_period}",
        "cancel_url": f"{PUBLIC_URL}/?tier_cancelled=1",
        "metadata[user_id]": srv.g.user_id,
        "metadata[tier]": new_tier,
        "metadata[billing_period]": billing_period,
        "subscription_data[metadata][user_id]": srv.g.user_id,
        "subscription_data[metadata][tier]": new_tier,
        "subscription_data[metadata][billing_period]": billing_period,
    }

    result = _stripe_request("POST", "/checkout/sessions", params)

    if "url" in result:
        return jsonify({
            "checkout_url": result["url"],
            "tier": new_tier,
            "billing_period": billing_period,
            "price_cents": price_cents,
        })
    return jsonify({"error": result.get("error", {}).get("message", "Stripe error")}, 502), 502


@tiers_bp.route("/cancel", methods=["POST"])
@require_auth
@require_role("artist", "label", "admin")
def cancel_tier(req):
    """Cancel subscription at period end. Artist keeps tier until expiry date."""
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        current_tier = user.get("tier") or "free"
        if current_tier == "free":
            return jsonify({"error": "No active subscription to cancel"}), 400

        sub_id = user.get("stripe_subscription_id")

        if sub_id and STRIPE_SECRET_KEY:
            # Cancel at period end (not immediately)
            result = _stripe_request("POST", f"/subscriptions/{sub_id}", {
                "cancel_at_period_end": "true",
            })
            if "error" in result:
                return jsonify({"error": result["error"].get("message", "Stripe error")}, 502), 502

        # Log cancellation intent
        conn.execute(
            """INSERT INTO subscription_history (id, user_id, tier, action, stripe_subscription_id, amount_cents)
               VALUES (?, ?, ?, 'cancel', ?, 0)""",
            (new_id(), srv.g.user_id, current_tier, sub_id or "")
        )
        conn.commit()

    finally:
        conn.close()

    return jsonify({
        "message": f"Your {current_tier} plan will remain active until the end of the billing period.",
        "tier_expires_at": user.get("tier_expires_at"),
        "tier": current_tier,
    })


@tiers_bp.route("/reactivate", methods=["POST"])
@require_auth
@require_role("artist", "label", "admin")
def reactivate_tier(req):
    """Reactivate a subscription that was cancelled but hasn't expired yet."""
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
        if not user:
            return jsonify({"error": "User not found"}, 404), 404

        sub_id = user.get("stripe_subscription_id")
        if not sub_id:
            return jsonify({"error": "No subscription found to reactivate"}), 400

        if STRIPE_SECRET_KEY:
            result = _stripe_request("POST", f"/subscriptions/{sub_id}", {
                "cancel_at_period_end": "false",
            })
            if "error" in result:
                return jsonify({"error": result["error"].get("message", "Stripe error")}, 502), 502

        conn.execute(
            """INSERT INTO subscription_history (id, user_id, tier, action, stripe_subscription_id, amount_cents)
               VALUES (?, ?, ?, 'reactivate', ?, 0)""",
            (new_id(), srv.g.user_id, user.get("tier", "free"), sub_id)
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"message": "Subscription reactivated.", "tier": user.get("tier")})


@tiers_bp.route("/savings", methods=["GET"])
@require_auth
@require_role("artist", "label", "admin")
def tier_savings(req):
    """
    "Math sells itself" savings dashboard.
    Shows what an artist has paid in fees over the last 30 days
    and how much they'd save at each tier.
    """
    conn = get_db()
    try:
        user = row_to_dict(conn.execute(
            "SELECT tier FROM users WHERE id = ?", (srv.g.user_id,)
        ).fetchone())
        current_tier = (user.get("tier") or "free") if user else "free"

        # Gross revenue last 30 days
        since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        result = conn.execute(
            """SELECT COALESCE(SUM(t.amount_cents), 0) as gross
               FROM transactions t
               JOIN drops d ON t.drop_id = d.id
               WHERE d.artist_id = ? AND t.status = 'completed'
               AND t.type = 'drop_purchase' AND t.created_at >= ?""",
            (srv.g.user_id, since)
        ).fetchone()
        gross_cents = result["gross"] if result else 0

        # Annual projection (30d * 12)
        annual_gross_cents = gross_cents * 12

        tiers = get_tier_limits(conn)
        breakdown = []
        for tier_key in ("free", "hustler", "pro", "label"):
            t = tiers.get(tier_key)
            if not t:
                continue
            fee_pct = t["platform_fee_pct"]
            monthly_cost = t["monthly_price_cents"]
            fees_on_30d = int(gross_cents * fee_pct)
            fees_annual = int(annual_gross_cents * fee_pct)
            sub_cost_annual = monthly_cost * 12
            total_cost_annual = fees_annual + sub_cost_annual

            # Savings vs Free tier
            free_fees_annual = int(annual_gross_cents * 0.15)
            annual_savings = (free_fees_annual) - total_cost_annual
            net_after_fees_30d = gross_cents - fees_on_30d - monthly_cost

            breakdown.append({
                "tier": tier_key,
                "label": t["label"],
                "monthly_price_cents": monthly_cost,
                "fee_pct": fee_pct,
                "fee_pct_display": f"{int(fee_pct * 100)}%",
                "fees_this_month_cents": fees_on_30d,
                "fees_annual_cents": fees_annual,
                "sub_cost_annual_cents": sub_cost_annual,
                "total_cost_annual_cents": total_cost_annual,
                "net_after_fees_30d_cents": net_after_fees_30d,
                "annual_savings_vs_free_cents": annual_savings,
                "annual_savings_vs_free": round(annual_savings / 100, 2),
                "is_current": tier_key == current_tier,
                "recommended": False,  # Set below
            })

        # Mark best value tier
        if gross_cents > 0:
            best = max(breakdown[1:], key=lambda x: x["annual_savings_vs_free_cents"], default=None)
            if best and best["annual_savings_vs_free_cents"] > 0:
                best["recommended"] = True

        # Breakeven thresholds
        breakeven = {}
        free_fee_rate = 0.15
        for tier_key in ("hustler", "pro", "label"):
            t = tiers.get(tier_key, {})
            sub = t.get("monthly_price_cents", 0)
            their_rate = t.get("platform_fee_pct", 0.15)
            rate_diff = free_fee_rate - their_rate
            if rate_diff > 0:
                breakeven[tier_key] = {
                    "monthly_revenue_cents": int(sub / rate_diff),
                    "monthly_revenue": round(sub / rate_diff / 100, 2),
                }

    finally:
        conn.close()

    return jsonify({
        "gross_30d_cents": gross_cents,
        "gross_30d": round(gross_cents / 100, 2),
        "annual_projection_cents": annual_gross_cents,
        "annual_projection": round(annual_gross_cents / 100, 2),
        "current_tier": current_tier,
        "breakdown": breakdown,
        "breakeven": breakeven,
        "message": (
            f"At your current sales rate, upgrading to Pro saves you "
            f"${round((int(annual_gross_cents * 0.15) - int(annual_gross_cents * 0.03) - 2900 * 12) / 100, 2):.2f}/yr."
            if gross_cents > 0 else
            "Start dropping to see your savings potential."
        ),
    })
