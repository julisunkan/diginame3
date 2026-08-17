"""
AI Meeting Summarizer  –  /otherapps/meetingsummarizer
Blueprint: meetingsummarizer
"""
import os, sqlite3, uuid
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from otherapps.reporting import (
    REPORT_REASONS, ensure_reports_table, list_reports, pending_report_count,
    register_reporting_routes,
)
from werkzeug.utils import secure_filename

# ─── paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
DB_PATH    = os.path.join(BASE_DIR, 'meetingsummarizer.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
for d in (UPLOAD_DIR, OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_AUDIO = {'mp3', 'wav', 'm4a', 'mp4', 'webm', 'ogg', 'flac'}

# ─── blueprint ────────────────────────────────────────────────────────────────
meetingsummarizer_bp = Blueprint(
    'meetingsummarizer', __name__,
    template_folder='templates',
    static_folder='static',
)

# ─── database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS meetings (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            audio_path TEXT,
            transcript TEXT,
            summary TEXT,
            output_path TEXT,
            status TEXT DEFAULT 'pending',
            error_msg TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS admin (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT
        );
        """)
        ensure_reports_table(db)
        row = db.execute("SELECT id FROM admin LIMIT 1").fetchone()
        if not row:
            db.execute(
                "INSERT INTO admin (username,password_hash) VALUES (?,?)",
                ('admin', generate_password_hash('admin123'))
            )
        defaults = {
            'groq_api_key': '',
            'whisper_model': 'whisper-large-v3',
            'summary_model': 'openai/gpt-oss-20b',
            'summary_style': 'bullet-points',
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        db.commit()

def get_setting(key, default=''):
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        db.commit()

# ─── auth ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('ms_admin'):
            return redirect(url_for('meetingsummarizer.admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_AUDIO

def transcribe_audio(audio_path, api_key, model='whisper-large-v3'):
    from groq import Groq
    client = Groq(api_key=api_key)
    with open(audio_path, 'rb') as f:
        resp = client.audio.transcriptions.create(
            file=(os.path.basename(audio_path), f),
            model=model,
            response_format='text',
        )
    return resp if isinstance(resp, str) else resp.text

def summarize_text(transcript, style, api_key, model):
    from groq import Groq
    client = Groq(api_key=api_key)
    style_prompt = {
        'bullet-points': 'Create a concise bullet-point summary with key decisions and action items.',
        'paragraph': 'Write a clear, professional paragraph summary.',
        'detailed': 'Write a detailed summary including all major points, decisions, and follow-ups.',
    }.get(style, 'Create a concise bullet-point summary.')
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": f"You are an expert meeting note-taker. {style_prompt}"},
            {"role": "user", "content": f"Meeting transcript:\n\n{transcript[:10000]}"},
        ],
        temperature=0.4,
        max_tokens=2048,
    )
    return resp.choices[0].message.content

# ─── routes ───────────────────────────────────────────────────────────────────

@meetingsummarizer_bp.route('/')
def index():
    init_db()
    return render_template('meetingsummarizer/index.html')

@meetingsummarizer_bp.route('/settings', methods=['GET', 'POST'])
def groq_settings():
    init_db()
    if request.method == 'POST':
        set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
        flash('Groq API key saved.', 'success')
        return redirect(url_for('meetingsummarizer.groq_settings'))
    return render_template(
        'meetingsummarizer/settings.html',
        groq_api_key=get_setting('groq_api_key'),
    )

@meetingsummarizer_bp.route('/transcribe', methods=['POST'])
def transcribe():
    init_db()
    api_key = get_setting('groq_api_key')
    if not api_key:
        flash('No Groq API key configured. Please set it in Admin → Settings.', 'error')
        return redirect(url_for('meetingsummarizer.index'))

    file = request.files.get('audio')
    if not file or file.filename == '':
        flash('Please select an audio file.', 'error')
        return redirect(url_for('meetingsummarizer.index'))
    if not allowed_file(file.filename):
        flash(f'Unsupported file type. Allowed: {", ".join(ALLOWED_AUDIO)}', 'error')
        return redirect(url_for('meetingsummarizer.index'))

    uid      = str(uuid.uuid4())
    ext      = file.filename.rsplit('.', 1)[1].lower()
    safe_fn  = secure_filename(file.filename)
    up_path  = os.path.join(UPLOAD_DIR, f"{uid}_{safe_fn}")
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_summary.txt")
    file.save(up_path)

    with get_db() as db:
        db.execute(
            "INSERT INTO meetings (id,original_name,audio_path,output_path,status,created_at) VALUES (?,?,?,?,?,?)",
            (uid, safe_fn, up_path, out_path, 'processing', datetime.utcnow().isoformat())
        )
        db.commit()

    try:
        whisper_model  = get_setting('whisper_model', 'whisper-large-v3')
        summary_model  = get_setting('summary_model', 'openai/gpt-oss-20b')
        summary_style  = get_setting('summary_style', 'bullet-points')

        transcript = transcribe_audio(up_path, api_key, whisper_model)
        summary    = summarize_text(transcript, summary_style, api_key, summary_model)

        output = f"MEETING TRANSCRIPT\n{'='*60}\n\n{transcript}\n\n\nMEETING SUMMARY\n{'='*60}\n\n{summary}"
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(output)

        with get_db() as db:
            db.execute(
                "UPDATE meetings SET status='done',transcript=?,summary=? WHERE id=?",
                (transcript[:5000], summary, uid)
            )
            db.commit()

        flash('Meeting transcribed and summarized!', 'success')
        return redirect(url_for('meetingsummarizer.result', uid=uid))

    except Exception as e:
        err = str(e)
        with get_db() as db:
            db.execute("UPDATE meetings SET status='error',error_msg=? WHERE id=?", (err, uid))
            db.commit()
        flash(f'Error processing audio: {err}', 'error')
        return redirect(url_for('meetingsummarizer.index'))

@meetingsummarizer_bp.route('/result/<uid>')
def result(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM meetings WHERE id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    return render_template('meetingsummarizer/result.html', row=row)

@meetingsummarizer_bp.route('/download/<uid>')
def download(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM meetings WHERE id=?", (uid,)).fetchone()
    if not row or row['status'] != 'done':
        abort(404)
    return send_file(
        row['output_path'],
        as_attachment=True,
        download_name=f"summary_{row['original_name'].rsplit('.', 1)[0]}.txt",
        mimetype='text/plain'
    )

@meetingsummarizer_bp.route('/history')
def history():
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM meetings ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('meetingsummarizer/history.html', rows=rows)

# ── admin ──

@meetingsummarizer_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    init_db()
    if session.get('ms_admin'):
        return redirect(url_for('meetingsummarizer.admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as db:
            row = db.execute("SELECT * FROM admin WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['ms_admin'] = True
            flash('Welcome back!', 'success')
            return redirect(url_for('meetingsummarizer.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('meetingsummarizer/admin/login.html')

@meetingsummarizer_bp.route('/admin/logout')
def admin_logout():
    session.pop('ms_admin', None)
    flash('Logged out.', 'success')
    return redirect(url_for('meetingsummarizer.index'))

@meetingsummarizer_bp.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    init_db()
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) FROM meetings").fetchone()[0]
        done   = db.execute("SELECT COUNT(*) FROM meetings WHERE status='done'").fetchone()[0]
        errors = db.execute("SELECT COUNT(*) FROM meetings WHERE status='error'").fetchone()[0]
        recent = db.execute("SELECT * FROM meetings ORDER BY created_at DESC LIMIT 10").fetchall()
        reports = list_reports(db)
        pending_reports = pending_report_count(db)
    return render_template('meetingsummarizer/admin/dashboard.html',
                           total=total, done=done, errors=errors, recent=recent,
                           reports=reports, pending_reports=pending_reports,
                           report_reasons=dict(REPORT_REASONS))

@meetingsummarizer_bp.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'api':
            set_setting('groq_api_key',   request.form.get('groq_api_key', '').strip())
            set_setting('whisper_model',  request.form.get('whisper_model', 'whisper-large-v3').strip())
            set_setting('summary_model',  request.form.get('summary_model', 'openai/gpt-oss-20b').strip())
            set_setting('summary_style',  request.form.get('summary_style', 'bullet-points').strip())
            flash('Settings saved.', 'success')
        elif action == 'password':
            cur_pw  = request.form.get('current_password', '')
            new_pw  = request.form.get('new_password', '')
            conf_pw = request.form.get('confirm_password', '')
            with get_db() as db:
                row = db.execute("SELECT * FROM admin LIMIT 1").fetchone()
            if not row or not check_password_hash(row['password_hash'], cur_pw):
                flash('Current password is incorrect.', 'error')
            elif new_pw != conf_pw:
                flash('New passwords do not match.', 'error')
            elif len(new_pw) < 6:
                flash('Password must be at least 6 characters.', 'error')
            else:
                with get_db() as db:
                    db.execute("UPDATE admin SET password_hash=?", (generate_password_hash(new_pw),))
                    db.commit()
                flash('Password updated.', 'success')
        return redirect(url_for('meetingsummarizer.admin_settings'))

    settings = {
        'groq_api_key':  get_setting('groq_api_key'),
        'whisper_model': get_setting('whisper_model', 'whisper-large-v3'),
        'summary_model': get_setting('summary_model', 'openai/gpt-oss-20b'),
        'summary_style': get_setting('summary_style', 'bullet-points'),
    }
    return render_template('meetingsummarizer/admin/settings.html', settings=settings)

@meetingsummarizer_bp.route('/admin/delete/<uid>', methods=['POST'])
@admin_required
def admin_delete(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM meetings WHERE id=?", (uid,)).fetchone()
        if row:
            for p in (row['audio_path'], row['output_path']):
                if p and os.path.exists(p):
                    os.remove(p)
            db.execute("DELETE FROM meetings WHERE id=?", (uid,))
            db.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('meetingsummarizer.admin_dashboard'))


register_reporting_routes(meetingsummarizer_bp, DB_PATH, 'meetings', 'ms_admin')
