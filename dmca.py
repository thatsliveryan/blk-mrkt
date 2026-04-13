"""
BLK MRKT — DMCA Takedown Pipeline

Safe harbor compliance (17 U.S.C. § 512). Any claimant can file a takedown
report. The drop is immediately locked for audio playback (dmca_review = 1).
Admin reviews and either upholds (drop stays locked / removed) or rejects
(drop restored). Artists can file a counter-notice within 10 business days.

Routes:
  POST /api/dmca/report           — file a DMCA takedown (no auth required)
  POST /api/dmca/counter          — artist counter-notice (auth required)
  GET  /api/admin/dmca            — list all reports (admin)
  GET  /api/admin/dmca/:id        — single report detail (admin)
  PATCH /api/admin/dmca/:id       — uphold / reject / reinstate (admin)

Drop audio is blocked at serve time whenever drops.dmca_review = 1.
"""

import re

from server import Blueprint, jsonify
import server as srv
from auth import require_auth, require_role
from models import get_db, row_to_dict, rows_to_list, new_id, utcnow
from config import EMAIL_ENABLED
from email_utils import send_email

dmca_bp = Blueprint("dmca", prefix="/api/dmca")
admin_dmca_bp = Blueprint("admin_dmca", prefix="/api/admin/dmca")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _lock_drop(conn, drop_id):
    """Set dmca_review=1 on a drop so audio cannot be served."""
    conn.execute(
        "UPDATE drops SET dmca_review = 1 WHERE id = ?", (drop_id,)
    )


def _unlock_drop(conn, drop_id):
    """Clear dmca_review flag to restore normal playback."""
    conn.execute(
        "UPDATE drops SET dmca_review = 0 WHERE id = ?", (drop_id,)
    )


# ---------------------------------------------------------------------------
# Public — file a takedown
# ---------------------------------------------------------------------------

@dmca_bp.route("/report", methods=["POST"])
def file_report(req):
    """
    File a DMCA takedown notice. No authentication required — any third party
    can file. The drop is immediately marked dmca_review=1 on receipt.

    Required body fields:
      drop_id, claimant_name, claimant_email, original_work,
      statement_confirmed (bool), perjury_confirmed (bool)
    """
    body = req.json or {}

    drop_id           = (body.get("drop_id") or "").strip()
    claimant_name     = (body.get("claimant_name") or "").strip()
    claimant_email    = (body.get("claimant_email") or "").strip().lower()
    original_work     = (body.get("original_work") or "").strip()
    stmt_confirmed    = bool(body.get("statement_confirmed"))
    perjury_confirmed = bool(body.get("perjury_confirmed"))

    # --- Validation ---
    errors = []
    if not drop_id:
        errors.append("drop_id is required")
    if not claimant_name:
        errors.append("claimant_name is required")
    if not claimant_email or not _EMAIL_RE.match(claimant_email):
        errors.append("valid claimant_email is required")
    if not original_work or len(original_work) < 20:
        errors.append("original_work description must be at least 20 characters")
    if not stmt_confirmed:
        errors.append("statement_confirmed must be true — you must certify the information is accurate")
    if not perjury_confirmed:
        errors.append("perjury_confirmed must be true — you must acknowledge the penalty of perjury")
    if errors:
        return jsonify({"error": "; ".join(errors)}, 400), 400

    conn = get_db()
    try:
        # Verify drop exists
        drop = row_to_dict(conn.execute(
            "SELECT id, title, artist_id, status FROM drops WHERE id = ?",
            (drop_id,)
        ).fetchone())

        if not drop:
            return jsonify({"error": "Drop not found"}, 404), 404

        if drop["status"] == "expired":
            return jsonify({"error": "Cannot file a report against an expired drop"}, 400), 400

        # Create the report
        report_id = new_id()
        now = utcnow()
        conn.execute(
            """INSERT INTO dmca_reports
               (id, drop_id, claimant_name, claimant_email, original_work,
                statement_confirmed, perjury_confirmed, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (report_id, drop_id, claimant_name, claimant_email,
             original_work, 1 if stmt_confirmed else 0,
             1 if perjury_confirmed else 0, now)
        )

        # Immediately lock the drop
        _lock_drop(conn, drop_id)
        conn.commit()

        # Notify artist (if email enabled)
        if EMAIL_ENABLED:
            artist = row_to_dict(conn.execute(
                "SELECT email, username FROM users WHERE id = ?",
                (drop["artist_id"],)
            ).fetchone())
            if artist:
                send_email(
                    to=artist["email"],
                    subject="DMCA Takedown Notice Filed — BLK MRKT",
                    text=(
                        f"Hi {artist['username']},\n\n"
                        f"A DMCA takedown notice has been filed against your drop "
                        f"\"{drop['title']}\" (ID: {drop_id}).\n\n"
                        f"Your drop has been temporarily locked pending admin review. "
                        f"You may file a counter-notice via the BLK MRKT platform if you "
                        f"believe the claim is incorrect.\n\n"
                        f"Report ID: {report_id}\n\n"
                        f"BLK MRKT Team"
                    )
                )

    finally:
        conn.close()

    return jsonify({
        "report_id": report_id,
        "status": "pending",
        "message": (
            "Your DMCA takedown notice has been received and the drop has been "
            "temporarily locked pending admin review. You will be contacted if "
            "a counter-notice is filed."
        ),
    }, 201), 201


# ---------------------------------------------------------------------------
# Artist — counter-notice
# ---------------------------------------------------------------------------

@dmca_bp.route("/counter", methods=["POST"])
@require_auth
@require_role("artist", "admin")
def file_counter(req):
    """
    File a counter-notice against a DMCA report. Artist auth required.
    The artist must own the drop referenced by the report.

    Required body: report_id, counter_statement
    """
    body = req.json or {}

    report_id         = (body.get("report_id") or "").strip()
    counter_statement = (body.get("counter_statement") or "").strip()

    if not report_id:
        return jsonify({"error": "report_id is required"}, 400), 400
    if not counter_statement or len(counter_statement) < 50:
        return jsonify({"error": "counter_statement must be at least 50 characters"}, 400), 400

    conn = get_db()
    try:
        report = row_to_dict(conn.execute(
            """SELECT r.*, d.artist_id, d.title
               FROM dmca_reports r
               JOIN drops d ON d.id = r.drop_id
               WHERE r.id = ?""",
            (report_id,)
        ).fetchone())

        if not report:
            return jsonify({"error": "Report not found"}, 404), 404

        # Only the drop's artist can counter
        if report["artist_id"] != srv.g.user_id and srv.g.user_role != "admin":
            return jsonify({"error": "You do not own this drop"}, 403), 403

        if report["status"] not in ("pending", "upheld"):
            return jsonify({"error": f"Cannot counter a report with status '{report['status']}'"}, 400), 400

        if report["counter_statement"]:
            return jsonify({"error": "A counter-notice has already been filed for this report"}, 409), 409

        now = utcnow()
        conn.execute(
            """UPDATE dmca_reports
               SET counter_statement = ?, counter_filed_at = ?, status = 'counter_filed'
               WHERE id = ?""",
            (counter_statement, now, report_id)
        )
        conn.commit()

        # Notify admin (log only if no email)
        if EMAIL_ENABLED:
            send_email(
                to="dmca@blkmrkt.com",
                subject=f"DMCA Counter-Notice Filed — {report['title']}",
                text=(
                    f"A counter-notice has been filed for DMCA report {report_id}.\n\n"
                    f"Drop: {report['title']} ({report['drop_id']})\n\n"
                    f"Counter Statement:\n{counter_statement}\n\n"
                    f"Review at /admin/dmca/{report_id}"
                )
            )

    finally:
        conn.close()

    return jsonify({
        "report_id": report_id,
        "status": "counter_filed",
        "message": (
            "Your counter-notice has been received. The admin team will review "
            "within 10 business days. The drop will remain locked during review."
        ),
    })


# ---------------------------------------------------------------------------
# Admin — list & manage reports
# ---------------------------------------------------------------------------

@admin_dmca_bp.route("", methods=["GET"])
@require_auth
@require_role("admin")
def list_reports(req):
    """List all DMCA reports, newest first. Optional ?status= filter."""
    status_filter = (req.query.get("status") or "").strip()

    conn = get_db()
    try:
        if status_filter:
            rows = conn.execute(
                """SELECT r.*, d.title AS drop_title, d.artist_id,
                          u.username AS artist_username
                   FROM dmca_reports r
                   JOIN drops d ON d.id = r.drop_id
                   JOIN users u ON u.id = d.artist_id
                   WHERE r.status = ?
                   ORDER BY r.created_at DESC""",
                (status_filter,)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT r.*, d.title AS drop_title, d.artist_id,
                          u.username AS artist_username
                   FROM dmca_reports r
                   JOIN drops d ON d.id = r.drop_id
                   JOIN users u ON u.id = d.artist_id
                   ORDER BY r.created_at DESC"""
            ).fetchall()
        reports = rows_to_list(rows)
    finally:
        conn.close()

    return jsonify({"reports": reports, "count": len(reports)})


@admin_dmca_bp.route("/:report_id", methods=["GET"])
@require_auth
@require_role("admin")
def get_report(req, report_id):
    """Get a single DMCA report with full detail."""
    conn = get_db()
    try:
        report = row_to_dict(conn.execute(
            """SELECT r.*, d.title AS drop_title, d.artist_id,
                      u.username AS artist_username, u.email AS artist_email
               FROM dmca_reports r
               JOIN drops d ON d.id = r.drop_id
               JOIN users u ON u.id = d.artist_id
               WHERE r.id = ?""",
            (report_id,)
        ).fetchone())
    finally:
        conn.close()

    if not report:
        return jsonify({"error": "Report not found"}, 404), 404

    return jsonify({"report": report})


@admin_dmca_bp.route("/:report_id", methods=["PATCH"])
@require_auth
@require_role("admin")
def resolve_report(req, report_id):
    """
    Admin resolves a DMCA report.

    Body:
      action: "uphold" | "reject" | "reinstate"
      admin_notes: optional string

    - uphold:    drop stays locked (dmca_review=1), status='upheld'
    - reject:    drop unlocked (dmca_review=0), status='rejected'
    - reinstate: after counter-notice, admin restores drop, status='reinstated'
    """
    body = req.json or {}
    action      = (body.get("action") or "").strip().lower()
    admin_notes = (body.get("admin_notes") or "").strip()

    if action not in ("uphold", "reject", "reinstate"):
        return jsonify({"error": "action must be 'uphold', 'reject', or 'reinstate'"}, 400), 400

    conn = get_db()
    try:
        report = row_to_dict(conn.execute(
            """SELECT r.*, d.artist_id, d.title
               FROM dmca_reports r
               JOIN drops d ON d.id = r.drop_id
               WHERE r.id = ?""",
            (report_id,)
        ).fetchone())

        if not report:
            return jsonify({"error": "Report not found"}, 404), 404

        now = utcnow()

        if action == "uphold":
            # Keep drop locked
            _lock_drop(conn, report["drop_id"])
            new_status = "upheld"

        elif action == "reject":
            # Claimant's claim is invalid — restore the drop
            _unlock_drop(conn, report["drop_id"])
            new_status = "rejected"

        elif action == "reinstate":
            # After valid counter-notice — restore the drop
            if report["status"] not in ("counter_filed", "upheld"):
                return jsonify({
                    "error": "Can only reinstate after a counter-notice is filed"
                }, 400), 400
            _unlock_drop(conn, report["drop_id"])
            new_status = "reinstated"

        conn.execute(
            """UPDATE dmca_reports
               SET status = ?, admin_notes = ?, resolved_at = ?
               WHERE id = ?""",
            (new_status, admin_notes or report.get("admin_notes"), now, report_id)
        )
        conn.commit()

        # Notify artist of outcome
        if EMAIL_ENABLED:
            artist = row_to_dict(conn.execute(
                "SELECT email, username FROM users WHERE id = ?",
                (report["artist_id"],)
            ).fetchone())
            if artist:
                if action == "reject":
                    msg = (
                        f"Good news, {artist['username']}! The DMCA claim against "
                        f"\"{report['title']}\" has been reviewed and rejected as invalid. "
                        f"Your drop has been restored."
                    )
                elif action == "uphold":
                    msg = (
                        f"Hi {artist['username']}, the DMCA claim against "
                        f"\"{report['title']}\" has been upheld. Your drop will remain locked. "
                        f"You may file a counter-notice if you believe this is incorrect."
                    )
                else:  # reinstate
                    msg = (
                        f"Hi {artist['username']}, your counter-notice for "
                        f"\"{report['title']}\" has been reviewed and your drop has been reinstated."
                    )
                send_email(
                    to=artist["email"],
                    subject=f"DMCA Report Update — {report['title']}",
                    text=msg + "\n\nBLK MRKT Team"
                )

    finally:
        conn.close()

    return jsonify({
        "report_id": report_id,
        "action": action,
        "new_status": new_status,
        "message": f"Report {action}ed successfully.",
    })
