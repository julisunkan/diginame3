"""Shared reporting and moderation support for the independent otherapps."""
import sqlite3
import uuid
from datetime import datetime


REPORT_REASONS = (
    ('inaccurate', 'Inaccurate or misleading'),
    ('harmful', 'Harmful or unsafe'),
    ('copyright', 'Copyright or privacy concern'),
    ('other', 'Other'),
)
VALID_REASONS = {value for value, _label in REPORT_REASONS}


def ensure_reports_table(db):
    """Add the report queue to an app's existing SQLite database."""
    db.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT NOT NULL,
            reviewed_at TEXT
        )
    """)


def list_reports(db, limit=100):
    return db.execute(
        "SELECT * FROM reports ORDER BY "
        "CASE status WHEN 'pending' THEN 0 ELSE 1 END, created_at DESC LIMIT ?",
        (limit,),
    ).fetchall()


def pending_report_count(db):
    return db.execute(
        "SELECT COUNT(*) FROM reports WHERE status='pending'"
    ).fetchone()[0]


def register_reporting_routes(bp, db_path, source_table, session_key):
    """Attach the same report and moderation routes to an app blueprint."""
    def report_content(uid):
        from flask import flash, redirect, request, url_for
        ok, message = create_report_for_table(
            db_path, source_table, uid,
            request.form.get('reason'), request.form.get('details'),
        )
        flash(message, 'success' if ok else 'error')
        return redirect(url_for(f'{bp.name}.result', uid=uid))

    def review_report(report_id):
        from flask import flash, redirect, request, url_for
        decision = request.form.get('decision', '').strip().lower()
        if decision not in ('approved', 'denied'):
            flash('Invalid moderation decision.', 'error')
            return redirect(url_for(f'{bp.name}.admin_dashboard'))
        with sqlite3.connect(db_path) as db:
            ensure_reports_table(db)
            report = db.execute(
                "SELECT content_id, status FROM reports WHERE id=?", (report_id,)
            ).fetchone()
            if not report:
                flash('Report not found.', 'error')
            elif report[1] != 'pending':
                flash('That report has already been reviewed.', 'warning')
            else:
                db.execute(
                    "UPDATE reports SET status=?, reviewed_at=? WHERE id=?",
                    (decision, datetime.utcnow().isoformat(), report_id),
                )
                if decision == 'approved':
                    db.execute(
                        f"UPDATE {source_table} SET status='removed' WHERE id=?",
                        (report[0],),
                    )
                db.commit()
                flash(
                    'Report approved; the generated item was removed.'
                    if decision == 'approved'
                    else 'Report denied; the generated item remains available.',
                    'success' if decision == 'approved' else 'info',
                )
        return redirect(url_for(f'{bp.name}.admin_dashboard'))

    def admin_guard(view):
        from functools import wraps
        from flask import redirect, url_for, session

        @wraps(view)
        def guarded(*args, **kwargs):
            if not session.get(session_key):
                return redirect(url_for(f'{bp.name}.admin_login'))
            return view(*args, **kwargs)
        return guarded

    admin_report_view = admin_guard(review_report)
    bp.add_url_rule(
        '/report/<uid>', endpoint='report_content',
        view_func=report_content, methods=['POST'],
    )
    bp.add_url_rule(
        '/admin/reports/<report_id>', endpoint='review_report',
        view_func=admin_report_view, methods=['POST'],
    )


def create_report_for_table(db_path, source_table, content_id, reason, details):
    reason = (reason or '').strip().lower()
    details = (details or '').strip()[:2000]
    if reason not in VALID_REASONS:
        return False, 'Please choose a valid reason.'
    if not details and reason == 'other':
        return False, 'Please tell us a little more about this report.'

    with sqlite3.connect(db_path) as db:
        ensure_reports_table(db)
        source = db.execute(
            f"SELECT status FROM {source_table} WHERE id=?", (content_id,)
        ).fetchone()
        if not source or source[0] != 'done':
            return False, 'That generated item is no longer available to report.'
        db.execute(
            "INSERT INTO reports "
            "(id, content_id, reason, details, status, created_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?)",
            (str(uuid.uuid4()), content_id, reason, details,
             datetime.utcnow().isoformat()),
        )
        db.commit()
    return True, 'Thanks. Your report was sent to the app administrator.'
