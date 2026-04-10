"""
BLK MRKT — Artist Analytics (System 6)

Provides artists with deep insight into their drops' performance.

Routes:
  GET /api/analytics/drops/:drop_id   — hourly breakdown, velocity curve, conversion funnel
  GET /api/analytics/overview         — artist-level aggregate stats across all drops
"""

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, row_to_dict, rows_to_list
from engine import calc_velocity, calc_velocity_bulk

analytics_bp = Blueprint("analytics", prefix="/api/analytics")


@analytics_bp.route("/drops/:drop_id", methods=["GET"])
@require_auth
def drop_analytics(req, drop_id):
    """
    Detailed analytics for a single drop:
    - Hourly engagement breakdown (last 72 hours)
    - Velocity curve (velocity at each hour bucket)
    - Conversion funnel: plays → saves → shares → claims
    - Top engagement windows
    """
    conn = get_db()
    try:
        drop = row_to_dict(conn.execute(
            "SELECT * FROM drops WHERE id = ?", (drop_id,)
        ).fetchone())
        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404

        # Only artist or admin can see analytics
        if drop["artist_id"] != srv.g.user_id and getattr(srv.g, "user_role", "") != "admin":
            return jsonify({"error": "Forbidden"}, 403), 403

        # Total engagement per action
        totals = rows_to_list(conn.execute(
            """SELECT action, COUNT(*) as count
               FROM drop_engagement WHERE drop_id = ?
               GROUP BY action""",
            (drop_id,)
        ).fetchall())
        total_map = {r["action"]: r["count"] for r in totals}

        # Total claims (from drop_access)
        total_claims = conn.execute(
            "SELECT COUNT(*) as c FROM drop_access WHERE drop_id = ?", (drop_id,)
        ).fetchone()["c"]

        # Hourly breakdown — engagement grouped by hour bucket
        hourly_raw = rows_to_list(conn.execute(
            """SELECT
                   strftime('%Y-%m-%dT%H:00:00Z', timestamp) as hour,
                   action,
                   COUNT(*) as count
               FROM drop_engagement
               WHERE drop_id = ?
               AND timestamp >= datetime('now', '-72 hours')
               GROUP BY hour, action
               ORDER BY hour ASC""",
            (drop_id,)
        ).fetchall())

        # Restructure hourly into {hour: {action: count}}
        hourly = {}
        for row in hourly_raw:
            h = row["hour"]
            if h not in hourly:
                hourly[h] = {"hour": h, "play": 0, "replay": 0, "save": 0, "share": 0, "profile_click": 0, "total": 0}
            hourly[h][row["action"]] = row["count"]
            hourly[h]["total"] += row["count"]

        hourly_list = sorted(hourly.values(), key=lambda x: x["hour"])

        # Velocity curve — cumulative weighted score at each hour
        weights = {"play": 1, "replay": 2, "save": 3, "share": 5, "profile_click": 2}
        velocity_curve = []
        cumulative = 0
        for i, bucket in enumerate(hourly_list):
            bucket_score = sum(bucket.get(a, 0) * w for a, w in weights.items())
            cumulative += bucket_score
            hour_index = i + 1
            velocity_curve.append({
                "hour": bucket["hour"],
                "hour_index": hour_index,
                "score": bucket_score,
                "cumulative": cumulative,
                "velocity": round(cumulative / hour_index, 2) if hour_index > 0 else 0,
            })

        # Conversion funnel
        plays = total_map.get("play", 0) + total_map.get("replay", 0)
        saves = total_map.get("save", 0)
        shares = total_map.get("share", 0)
        claims = total_claims

        funnel = [
            {"stage": "Plays", "count": plays, "pct": 100},
            {"stage": "Saves", "count": saves, "pct": round((saves / plays * 100) if plays else 0, 1)},
            {"stage": "Shares", "count": shares, "pct": round((shares / plays * 100) if plays else 0, 1)},
            {"stage": "Claims", "count": claims, "pct": round((claims / plays * 100) if plays else 0, 1)},
        ]

        # Boost history for this drop
        boosts = rows_to_list(conn.execute(
            """SELECT id, budget_cents, status, started_at, expires_at,
                      impressions, clicks, created_at
               FROM boosts WHERE drop_id = ? ORDER BY created_at DESC""",
            (drop_id,)
        ).fetchall())

        # Recent claimers (last 10, usernames only for social proof)
        claimers = rows_to_list(conn.execute(
            """SELECT u.username, da.acquired_at, da.fan_number, da.access_type
               FROM drop_access da JOIN users u ON da.user_id = u.id
               WHERE da.drop_id = ? ORDER BY da.acquired_at DESC LIMIT 10""",
            (drop_id,)
        ).fetchall())

        # Revenue from this drop
        revenue = conn.execute(
            """SELECT COALESCE(SUM(amount_cents), 0) as total,
                      COUNT(*) as transactions
               FROM transactions
               WHERE drop_id = ? AND status = 'completed' AND type = 'drop_purchase'""",
            (drop_id,)
        ).fetchone()

        current_velocity = calc_velocity(drop_id, drop["starts_at"])

    finally:
        conn.close()

    return jsonify({
        "drop_id": drop_id,
        "drop_title": drop["title"],
        "drop_status": drop["status"],
        "starts_at": drop["starts_at"],
        "total_supply": drop["total_supply"],
        "remaining_supply": drop["remaining_supply"],
        "current_velocity": current_velocity,

        # Engagement summary
        "engagement": {
            "plays": total_map.get("play", 0),
            "replays": total_map.get("replay", 0),
            "saves": total_map.get("save", 0),
            "shares": total_map.get("share", 0),
            "profile_clicks": total_map.get("profile_click", 0),
            "total_engagements": sum(total_map.values()),
            "claims": total_claims,
        },

        # Revenue
        "revenue": {
            "total_cents": revenue["total"],
            "total": round(revenue["total"] / 100, 2),
            "transactions": revenue["transactions"],
        },

        # Time series
        "hourly": hourly_list,
        "velocity_curve": velocity_curve,

        # Funnel
        "funnel": funnel,

        # Context
        "boosts": boosts,
        "recent_claimers": claimers,
    })


@analytics_bp.route("/overview", methods=["GET"])
@require_auth
@require_role("artist", "admin")
def artist_overview(req):
    """
    Artist-level overview across all drops:
    - Total plays, saves, shares, claims
    - Total revenue
    - Top performing drops
    - Follower count
    - Drop count by status
    """
    conn = get_db()
    try:
        # Limit to this artist's drops (or all drops for admin)
        if srv.g.user_role == "admin":
            artist_filter = ""
            params = []
        else:
            artist_filter = "WHERE d.artist_id = ?"
            params = [srv.g.user_id]

        # Drop count by status
        status_counts = rows_to_list(conn.execute(
            f"""SELECT status, COUNT(*) as count FROM drops {artist_filter}
                GROUP BY status""",
            params
        ).fetchall())
        status_map = {r["status"]: r["count"] for r in status_counts}

        # Total engagement across all drops
        engagement_totals = rows_to_list(conn.execute(
            f"""SELECT de.action, COUNT(*) as count
                FROM drop_engagement de
                JOIN drops d ON de.drop_id = d.id
                {artist_filter}
                GROUP BY de.action""",
            params
        ).fetchall())
        eng_map = {r["action"]: r["count"] for r in engagement_totals}

        # Total claims
        claim_params = params + []
        total_claims = conn.execute(
            f"""SELECT COUNT(*) as c FROM drop_access da
                JOIN drops d ON da.drop_id = d.id
                {artist_filter}""",
            params
        ).fetchone()["c"]

        # Total revenue
        revenue = conn.execute(
            f"""SELECT COALESCE(SUM(t.amount_cents), 0) as total,
                       COUNT(*) as transactions
                FROM transactions t
                JOIN drops d ON t.drop_id = d.id
                {artist_filter}
                {'AND' if artist_filter else 'WHERE'} t.status = 'completed'
                  AND t.type = 'drop_purchase'""",
            params
        ).fetchone()

        # Top drops by velocity
        all_drops = rows_to_list(conn.execute(
            f"""SELECT d.id, d.title, d.starts_at, d.status,
                       d.total_supply, d.remaining_supply, d.boost_active
                FROM drops d {artist_filter}
                ORDER BY d.created_at DESC LIMIT 50""",
            params
        ).fetchall())

        ids_starts = [(d["id"], d["starts_at"]) for d in all_drops]
        velocities = calc_velocity_bulk(ids_starts)

        top_drops = []
        for d in all_drops:
            vel = velocities.get(d["id"], 0)
            # Per-drop claim count
            claims = conn.execute(
                "SELECT COUNT(*) as c FROM drop_access WHERE drop_id = ?", (d["id"],)
            ).fetchone()["c"]
            # Per-drop revenue
            rev = conn.execute(
                """SELECT COALESCE(SUM(amount_cents), 0) as r
                   FROM transactions WHERE drop_id = ? AND status = 'completed'
                   AND type = 'drop_purchase'""",
                (d["id"],)
            ).fetchone()["r"]
            top_drops.append({
                **d,
                "velocity": vel,
                "claims": claims,
                "revenue_cents": rev,
                "revenue": round(rev / 100, 2),
            })

        top_drops.sort(key=lambda x: x["velocity"], reverse=True)
        top_drops = top_drops[:10]

        # Follower count
        follower_count = 0
        if srv.g.user_role != "admin":
            row = conn.execute(
                "SELECT follower_count FROM users WHERE id = ?", (srv.g.user_id,)
            ).fetchone()
            follower_count = row["follower_count"] if row else 0

        # Recent activity (last 7 days engagement)
        recent_engagement = rows_to_list(conn.execute(
            f"""SELECT strftime('%Y-%m-%d', de.timestamp) as day,
                       de.action, COUNT(*) as count
                FROM drop_engagement de
                JOIN drops d ON de.drop_id = d.id
                {artist_filter}
                {'AND' if artist_filter else 'WHERE'} de.timestamp >= datetime('now', '-7 days')
                GROUP BY day, de.action
                ORDER BY day ASC""",
            params
        ).fetchall())

    finally:
        conn.close()

    return jsonify({
        "summary": {
            "drops": status_map,
            "total_drops": sum(status_map.values()),
            "follower_count": follower_count,
            "total_claims": total_claims,
            "revenue": {
                "total_cents": revenue["total"],
                "total": round(revenue["total"] / 100, 2),
                "transactions": revenue["transactions"],
            },
            "engagement": {
                "plays": eng_map.get("play", 0),
                "replays": eng_map.get("replay", 0),
                "saves": eng_map.get("save", 0),
                "shares": eng_map.get("share", 0),
                "profile_clicks": eng_map.get("profile_click", 0),
                "total": sum(eng_map.values()),
            },
        },
        "top_drops": top_drops,
        "recent_engagement": recent_engagement,
    })
