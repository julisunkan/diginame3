"""
Online ID Validator  –  /otherapps/onlineidval
Blueprint: onlineidval
"""
import os, sqlite3, uuid, base64, json, time
from io import BytesIO
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
DB_PATH    = os.path.join(BASE_DIR, 'onlineidval.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
for d in (UPLOAD_DIR, OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_IMG = {'jpg', 'jpeg', 'png', 'webp', 'bmp', 'gif'}
DEFAULT_VISION_MODEL = 'qwen/qwen3.6-27b'
LEGACY_VISION_MODELS = {'meta-llama/llama-4-scout-17b-16e-instruct'}
VISION_RETRY_ATTEMPTS = 2
VISION_RETRY_BASE_DELAY = 1
ANALYSIS_IMAGE_MAX_DIMENSION = 2000
ANALYSIS_IMAGE_JPEG_QUALITY = 82

# ─── blueprint ────────────────────────────────────────────────────────────────
onlineidval_bp = Blueprint(
    'onlineidval', __name__,
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
        CREATE TABLE IF NOT EXISTS validations (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            image_path TEXT,
            verdict TEXT,
            confidence TEXT,
            details TEXT,
            report_path TEXT,
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
            'vision_model': DEFAULT_VISION_MODEL,
            'strictness': 'standard',
        }
        for k, v in defaults.items():
            db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        current_model = db.execute(
            "SELECT value FROM settings WHERE key='vision_model'"
        ).fetchone()
        if current_model and current_model['value'] in LEGACY_VISION_MODELS:
            db.execute(
                "UPDATE settings SET value=? WHERE key='vision_model'",
                (DEFAULT_VISION_MODEL,),
            )
        db.commit()

def get_setting(key, default=''):
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row['value'] if row else default

def set_setting(key, value):
    with get_db() as db:
        db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        db.commit()

def normalize_vision_model(model):
    """Return the supported default for blank or retired model settings."""
    model = (model or '').strip()
    return DEFAULT_VISION_MODEL if not model or model in LEGACY_VISION_MODELS else model

# ─── auth ─────────────────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('idval_admin'):
            return redirect(url_for('onlineidval.admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_IMG

def _is_transient_model_error(exc):
    """Whether Groq may succeed if the request is retried shortly."""
    status_code = getattr(exc, 'status_code', None)
    message = str(exc).lower()
    return status_code in (429, 500, 502, 503, 504) or any(
        phrase in message
        for phrase in ('over capacity', 'rate limit', 'temporarily unavailable')
    )

def _is_model_not_found_error(exc):
    status_code = getattr(exc, 'status_code', None)
    message = str(exc).lower()
    return status_code == 404 or 'model_not_found' in message or 'does not exist' in message

def _image_payload(image_path):
    """Resize uploads before analysis to reduce upload and vision time."""
    from PIL import Image
    from PIL.ImageOps import exif_transpose

    with Image.open(image_path) as source:
        image = exif_transpose(source).convert('RGB')
        image.thumbnail(
            (ANALYSIS_IMAGE_MAX_DIMENSION, ANALYSIS_IMAGE_MAX_DIMENSION),
            Image.Resampling.LANCZOS,
        )
        output = BytesIO()
        image.save(
            output,
            format='JPEG',
            quality=ANALYSIS_IMAGE_JPEG_QUALITY,
            optimize=True,
        )
        return output.getvalue(), 'image/jpeg'

def _delete_uploaded_image(image_path):
    """Remove an uploaded ID image and forget its filesystem path."""
    if image_path:
        try:
            upload_root = os.path.realpath(UPLOAD_DIR)
            image_real_path = os.path.realpath(image_path)
            if (
                image_real_path.startswith(upload_root + os.sep)
                and os.path.isfile(image_real_path)
            ):
                os.remove(image_real_path)
        except OSError:
            pass

def analyze_id(image_path, api_key, model, strictness):
    from groq import Groq
    client = Groq(api_key=api_key)

    img_bytes, mime = _image_payload(image_path)
    b64  = base64.b64encode(img_bytes).decode('utf-8')

    strictness_note = {
        'lenient': 'Only flag obvious problems.',
        'standard': 'Apply standard checks.',
        'strict': 'Apply strict checks.',
    }.get(strictness, 'Apply standard checks.')

    system_prompt = (
        "You check identity-document images for obvious fraud. "
        f"{strictness_note} "
        "Return ONLY JSON with these keys: "
        "verdict (VALID, SUSPICIOUS, or INVALID), "
        "confidence (HIGH, MEDIUM, or LOW), "
        "id_type, "
        "checks (photo_present, text_readable, security_features_visible, "
        "no_obvious_editing, consistent_fonts, proper_layout as booleans), "
        "flags (short array), and recommendation (short string). "
        "Do not explain your reasoning."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "text", "text": "Analyse this ID document:"},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ]},
    ]
    last_error = None
    for attempt in range(VISION_RETRY_ATTEMPTS):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_completion_tokens=512,
                response_format={"type": "json_object"},
                reasoning_effort="none",
            )
            break
        except Exception as exc:
            last_error = exc
            if not _is_transient_model_error(exc) or attempt == VISION_RETRY_ATTEMPTS - 1:
                raise
            time.sleep(VISION_RETRY_BASE_DELAY ** attempt)
    else:
        raise last_error

    raw = resp.choices[0].message.content.strip()
    # strip markdown code block if present
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return json.loads(raw)

def save_report(uid, original_name, result):
    path = os.path.join(OUTPUT_DIR, f"{uid}_report.txt")
    lines = [
        f"ID VALIDATION REPORT",
        f"{'='*50}",
        f"File      : {original_name}",
        f"Generated : {datetime.utcnow().isoformat()} UTC",
        f"",
        f"VERDICT      : {result.get('verdict', 'N/A')}",
        f"CONFIDENCE   : {result.get('confidence', 'N/A')}",
        f"ID TYPE      : {result.get('id_type', 'N/A')}",
        f"",
        f"CHECKS",
        f"{'─'*30}",
    ]
    for k, v in result.get('checks', {}).items():
        status = '✓' if v else '✗'
        lines.append(f"  {status} {k.replace('_', ' ').title()}")
    flags = result.get('flags', [])
    if flags:
        lines += ['', 'FLAGS', '─'*30]
        for fl in flags:
            lines.append(f"  • {fl}")
    lines += ['', 'RECOMMENDATION', '─'*30, f"  {result.get('recommendation', '')}"]
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path

# ─── routes ───────────────────────────────────────────────────────────────────

@onlineidval_bp.route('/')
def index():
    init_db()
    return render_template('onlineidval/index.html')

@onlineidval_bp.route('/settings', methods=['GET', 'POST'])
def groq_settings():
    init_db()
    if request.method == 'POST':
        set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
        flash('Groq API key saved.', 'success')
        return redirect(url_for('onlineidval.groq_settings'))
    return render_template(
        'onlineidval/settings.html',
        groq_api_key=get_setting('groq_api_key'),
    )

@onlineidval_bp.route('/validate', methods=['POST'])
def validate():
    init_db()
    api_key = get_setting('groq_api_key')
    if not api_key:
        flash('No Groq API key configured. Please set it in Admin → Settings.', 'error')
        return redirect(url_for('onlineidval.index'))

    file = request.files.get('id_image')
    if not file or file.filename == '':
        flash('Please select an image file.', 'error')
        return redirect(url_for('onlineidval.index'))
    if not allowed_file(file.filename):
        flash(f'Unsupported file type. Allowed: {", ".join(ALLOWED_IMG)}', 'error')
        return redirect(url_for('onlineidval.index'))

    uid     = str(uuid.uuid4())
    safe_fn = secure_filename(file.filename)
    up_path = os.path.join(UPLOAD_DIR, f"{uid}_{safe_fn}")
    file.save(up_path)

    with get_db() as db:
        db.execute(
            "INSERT INTO validations (id,original_name,image_path,status,created_at) VALUES (?,?,?,?,?)",
            (uid, safe_fn, up_path, 'processing', datetime.utcnow().isoformat())
        )
        db.commit()

    try:
        model      = normalize_vision_model(get_setting('vision_model', DEFAULT_VISION_MODEL))
        strictness = get_setting('strictness', 'standard')
        result     = analyze_id(up_path, api_key, model, strictness)
        rep_path   = save_report(uid, safe_fn, result)

        with get_db() as db:
            db.execute(
                "UPDATE validations SET status='done',verdict=?,confidence=?,details=?,report_path=? WHERE id=?",
                (result.get('verdict'), result.get('confidence'), json.dumps(result), rep_path, uid)
            )
            db.commit()

        flash('ID document analysed successfully.', 'success')
        return redirect(url_for('onlineidval.result', uid=uid))

    except Exception as e:
        err = str(e)
        with get_db() as db:
            db.execute("UPDATE validations SET status='error',error_msg=? WHERE id=?", (err, uid))
            db.commit()
        if _is_transient_model_error(e):
            flash(
                'Groq vision service is temporarily busy. '
                'The request was retried automatically; please try again in a few minutes.',
                'error',
            )
        elif _is_model_not_found_error(e):
            flash(
                'The configured vision model is unavailable. '
                'Open Admin → Settings and choose a current Groq vision model.',
                'error',
            )
        else:
            flash(f'Error analysing image: {err}', 'error')
        return redirect(url_for('onlineidval.index'))
    finally:
        _delete_uploaded_image(up_path)
        with get_db() as db:
            db.execute("UPDATE validations SET image_path=NULL WHERE id=?", (uid,))
            db.commit()

@onlineidval_bp.route('/result/<uid>')
def result(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM validations WHERE id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    details = {}
    if row['details']:
        try:
            details = json.loads(row['details'])
        except Exception:
            pass
    return render_template('onlineidval/result.html', row=row, details=details)

@onlineidval_bp.route('/download/<uid>')
def download(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM validations WHERE id=?", (uid,)).fetchone()
    if not row or not row['report_path'] or not os.path.exists(row['report_path']):
        abort(404)
    return send_file(
        row['report_path'],
        as_attachment=True,
        download_name=f"id_report_{row['original_name'].rsplit('.', 1)[0]}.txt",
        mimetype='text/plain'
    )

@onlineidval_bp.route('/history')
def history():
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM validations ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('onlineidval/history.html', rows=rows)

# ── admin ──

@onlineidval_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    init_db()
    if session.get('idval_admin'):
        return redirect(url_for('onlineidval.admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as db:
            row = db.execute("SELECT * FROM admin WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['idval_admin'] = True
            flash('Welcome back!', 'success')
            return redirect(url_for('onlineidval.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('onlineidval/admin/login.html')

@onlineidval_bp.route('/admin/logout')
def admin_logout():
    session.pop('idval_admin', None)
    flash('Logged out.', 'success')
    return redirect(url_for('onlineidval.index'))

@onlineidval_bp.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    init_db()
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) FROM validations").fetchone()[0]
        done   = db.execute("SELECT COUNT(*) FROM validations WHERE status='done'").fetchone()[0]
        valid  = db.execute("SELECT COUNT(*) FROM validations WHERE verdict='VALID'").fetchone()[0]
        susp   = db.execute("SELECT COUNT(*) FROM validations WHERE verdict='SUSPICIOUS'").fetchone()[0]
        inv    = db.execute("SELECT COUNT(*) FROM validations WHERE verdict='INVALID'").fetchone()[0]
        recent = db.execute("SELECT * FROM validations ORDER BY created_at DESC LIMIT 10").fetchall()
        reports = list_reports(db)
        pending_reports = pending_report_count(db)
    return render_template('onlineidval/admin/dashboard.html',
                           total=total, done=done, valid=valid,
                           suspicious=susp, invalid=inv, recent=recent,
                           reports=reports, pending_reports=pending_reports,
                           report_reasons=dict(REPORT_REASONS))

@onlineidval_bp.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'api':
            set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
            set_setting(
                'vision_model',
                normalize_vision_model(request.form.get('vision_model', '')),
            )
            set_setting('strictness',   request.form.get('strictness', 'standard').strip())
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
        return redirect(url_for('onlineidval.admin_settings'))

    settings = {
        'groq_api_key': get_setting('groq_api_key'),
        'vision_model': normalize_vision_model(
            get_setting('vision_model', DEFAULT_VISION_MODEL)
        ),
        'strictness':   get_setting('strictness', 'standard'),
    }
    return render_template('onlineidval/admin/settings.html', settings=settings)

@onlineidval_bp.route('/admin/delete/<uid>', methods=['POST'])
@admin_required
def admin_delete(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM validations WHERE id=?", (uid,)).fetchone()
        if row:
            for p in (row['image_path'], row['report_path']):
                if p and os.path.exists(p):
                    os.remove(p)
            db.execute("DELETE FROM validations WHERE id=?", (uid,))
            db.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('onlineidval.admin_dashboard'))


register_reporting_routes(onlineidval_bp, DB_PATH, 'validations', 'idval_admin')
