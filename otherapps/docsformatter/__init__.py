"""
Auto-Formatter for Docs  –  /otherapps/docsformatter
Blueprint: docsformatter
"""
import os, sqlite3, uuid, io, traceback, html, re
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, send_file, abort, current_app)
from werkzeug.security import generate_password_hash, check_password_hash
from otherapps.reporting import (
    REPORT_REASONS, ensure_reports_table, list_reports, pending_report_count,
    register_reporting_routes,
)
from werkzeug.utils import secure_filename

# ─── paths ────────────────────────────────────────────────────────────────────
BASE_DIR    = os.path.dirname(__file__)
DB_PATH     = os.path.join(BASE_DIR, 'docsformatter.db')
UPLOAD_DIR  = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR  = os.path.join(BASE_DIR, 'outputs')
for d in (UPLOAD_DIR, OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

ALLOWED_EXTS = {'pdf', 'docx', 'doc', 'txt'}

# ─── blueprint ────────────────────────────────────────────────────────────────
docsformatter_bp = Blueprint(
    'docsformatter', __name__,
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
        CREATE TABLE IF NOT EXISTS uploads (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            upload_path TEXT,
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
        # bootstrap admin
        row = db.execute("SELECT id FROM admin LIMIT 1").fetchone()
        if not row:
            db.execute(
                "INSERT INTO admin (username, password_hash) VALUES (?,?)",
                ('admin', generate_password_hash('admin123'))
            )
        # default settings
        defaults = {
            'groq_api_key': '',
            'groq_model': 'llama-3.1-8b-instant',
            'format_style': 'professional',
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
        if not session.get('df_admin'):
            return redirect(url_for('docsformatter.admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── helpers ──────────────────────────────────────────────────────────────────

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTS

def _inline_markdown(text):
    """Convert a small safe subset of Markdown into styled inline HTML."""
    escaped = html.escape(text, quote=False)
    code_spans = []

    def save_code(match):
        code_spans.append(f'<code>{match.group(1)}</code>')
        return f'@@CODE_{len(code_spans) - 1}@@'

    escaped = re.sub(r'`([^`\n]+)`', save_code, escaped)
    escaped = re.sub(r'\*\*(.+?)\*\*|__(.+?)__',
                     lambda m: f'<strong>{m.group(1) or m.group(2)}</strong>',
                     escaped)
    escaped = re.sub(r'(?<!\*)\*([^*\n]+)\*(?!\*)',
                     r'<em>\1</em>', escaped)
    escaped = re.sub(r'(?<!\w)_([^_\n]+)_(?!\w)',
                     r'<em>\1</em>', escaped)

    for index, code in enumerate(code_spans):
        escaped = escaped.replace(f'@@CODE_{index}@@', code)
    return escaped

def markdown_to_html(text):
    """Render formatter Markdown without allowing raw HTML through."""
    text = (text or '').replace('\r\n', '\n').strip()
    fenced = re.fullmatch(r'\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*',
                          text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    lines = text.split('\n')
    blocks = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue

        if line.startswith('```'):
            code_lines = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith('```'):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(f'<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>')
            continue

        heading = re.match(r'^(#{1,6})\s+(.+?)\s*#*$', line)
        if heading:
            level = min(len(heading.group(1)), 4)
            blocks.append(
                f'<h{level}>{_inline_markdown(heading.group(2))}</h{level}>'
            )
            index += 1
            continue

        if re.match(r'^([-*_])(?:\s*\1){2,}\s*$', line):
            blocks.append('<hr>')
            index += 1
            continue

        if re.match(r'^[-*+]\s+', line):
            items = []
            while index < len(lines):
                item = re.match(r'^\s*[-*+]\s+(.+)$', lines[index])
                if not item:
                    break
                items.append(f'<li>{_inline_markdown(item.group(1))}</li>')
                index += 1
            blocks.append('<ul>' + ''.join(items) + '</ul>')
            continue

        if re.match(r'^\d+[.)]\s+', line):
            items = []
            while index < len(lines):
                item = re.match(r'^\s*\d+[.)]\s+(.+)$', lines[index])
                if not item:
                    break
                items.append(f'<li>{_inline_markdown(item.group(1))}</li>')
                index += 1
            blocks.append('<ol>' + ''.join(items) + '</ol>')
            continue

        if line.startswith('>'):
            quote_lines = []
            while index < len(lines) and lines[index].strip().startswith('>'):
                quote_lines.append(re.sub(r'^\s*>\s?', '', lines[index].strip()))
                index += 1
            blocks.append(
                '<blockquote>' + _inline_markdown(' '.join(quote_lines)) + '</blockquote>'
            )
            continue

        paragraph_lines = [line]
        index += 1
        while index < len(lines) and lines[index].strip():
            next_line = lines[index].strip()
            if (
                re.match(r'^(#{1,6})\s+', next_line)
                or re.match(r'^[-*+]\s+', next_line)
                or re.match(r'^\d+[.)]\s+', next_line)
                or next_line.startswith('>')
                or next_line.startswith('```')
            ):
                break
            paragraph_lines.append(next_line)
            index += 1
        blocks.append(f'<p>{_inline_markdown(" ".join(paragraph_lines))}</p>')

    return '\n'.join(blocks)

def render_document_html(text, title='Formatted Document'):
    """Create a styled standalone HTML document from formatter Markdown."""
    safe_title = html.escape(title, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ --accent: #2563eb; --ink: #1e293b; --muted: #64748b; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; padding: 2rem; background: #f8faff; color: var(--ink);
           font: 16px/1.75 "Segoe UI", system-ui, sans-serif; }}
    main {{ max-width: 850px; margin: 0 auto; padding: 2.5rem 3rem;
            background: #fff; border-radius: 14px;
            box-shadow: 0 4px 24px rgba(15,23,42,.08); }}
    h1, h2, h3, h4 {{ color: var(--accent); line-height: 1.25;
                       margin: 1.6em 0 .55em; }}
    h1 {{ font-size: 2rem; border-bottom: 2px solid #dbeafe; padding-bottom: .45rem; }}
    h2 {{ font-size: 1.55rem; }}
    h3 {{ font-size: 1.25rem; }}
    h4 {{ font-size: 1.08rem; }}
    h1:first-child, h2:first-child, h3:first-child {{ margin-top: 0; }}
    p {{ margin: 0 0 1.1rem; }}
    strong {{ color: #0f172a; font-weight: 700; }}
    em {{ color: #475569; }}
    ul, ol {{ margin: .35rem 0 1.2rem; padding-left: 1.65rem; }}
    li {{ margin: .3rem 0; }}
    blockquote {{ margin: 1.25rem 0; padding: .75rem 1rem;
                  border-left: 4px solid var(--accent); background: #eff6ff;
                  color: #334155; }}
    code, pre {{ background: #f1f5f9; border-radius: 6px; }}
    code {{ padding: .12rem .35rem; font-size: .92em; }}
    pre {{ padding: 1rem; overflow-x: auto; line-height: 1.5; }}
    hr {{ border: 0; border-top: 1px solid #cbd5e1; margin: 1.7rem 0; }}
    @media (max-width: 640px) {{ body {{ padding: .75rem; }}
      main {{ padding: 1.35rem; }} }}
  </style>
</head>
<body><main>{markdown_to_html(text)}</main></body>
</html>"""

def extract_text(path, ext):
    if ext == 'txt':
        with open(path, 'r', errors='replace') as f:
            return f.read()
    if ext == 'pdf':
        try:
            from pypdf import PdfReader
            reader = PdfReader(path)
            return '\n\n'.join(p.extract_text() or '' for p in reader.pages)
        except Exception as e:
            raise RuntimeError(f"PDF read error: {e}")
    if ext in ('docx', 'doc'):
        try:
            from docx import Document
            doc = Document(path)
            return '\n\n'.join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise RuntimeError(f"DOCX read error: {e}")
    raise RuntimeError(f"Unsupported file type: {ext}")

def format_with_groq(text, style, api_key, model):
    try:
        from groq import Groq
        client = Groq(api_key=api_key)
        system = (
            "You are a professional document formatter. "
            "Reformat the provided text into a clean, well-structured "
            f"{style} style. Fix grammar, improve readability, add proper "
            "paragraph breaks and headings where appropriate. Return ONLY "
            "the formatted text, no commentary."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": text[:12000]},
            ],
            temperature=0.3,
            max_tokens=4096,
        )
        return resp.choices[0].message.content
    except Exception as e:
        raise RuntimeError(f"Groq API error: {e}")

def basic_format(text):
    import re
    text = re.sub(r'\r\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    lines = [l.strip() for l in text.split('\n')]
    return '\n'.join(lines)

# ─── routes ───────────────────────────────────────────────────────────────────

@docsformatter_bp.route('/')
def index():
    init_db()
    return render_template('docsformatter/index.html')

@docsformatter_bp.route('/settings', methods=['GET', 'POST'])
def groq_settings():
    init_db()
    if request.method == 'POST':
        set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
        flash('Groq API key saved.', 'success')
        return redirect(url_for('docsformatter.groq_settings'))
    return render_template(
        'docsformatter/settings.html',
        groq_api_key=get_setting('groq_api_key'),
    )

@docsformatter_bp.route('/format', methods=['POST'])
def format_doc():
    init_db()
    file = request.files.get('document')
    if not file or file.filename == '':
        flash('Please select a file to upload.', 'error')
        return redirect(url_for('docsformatter.index'))
    if not allowed_file(file.filename):
        flash('Unsupported file type. Please upload PDF, DOCX, or TXT.', 'error')
        return redirect(url_for('docsformatter.index'))

    uid      = str(uuid.uuid4())
    ext      = file.filename.rsplit('.', 1)[1].lower()
    safe_fn  = secure_filename(file.filename)
    up_path  = os.path.join(UPLOAD_DIR, f"{uid}_{safe_fn}")
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_formatted.html")
    file.save(up_path)

    with get_db() as db:
        db.execute(
            "INSERT INTO uploads (id,original_name,upload_path,output_path,status,created_at) VALUES (?,?,?,?,?,?)",
            (uid, safe_fn, up_path, out_path, 'processing', datetime.utcnow().isoformat())
        )
        db.commit()

    try:
        raw_text = extract_text(up_path, ext)
        api_key  = get_setting('groq_api_key')
        model    = get_setting('groq_model', 'llama-3.1-8b-instant')
        style    = get_setting('format_style', 'professional')

        if api_key:
            formatted = format_with_groq(raw_text, style, api_key, model)
        else:
            formatted = basic_format(raw_text)
            flash('No Groq API key configured – basic formatting applied. Set an API key in Admin → Settings for AI formatting.', 'warning')

        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(render_document_html(formatted, safe_fn))

        with get_db() as db:
            db.execute("UPDATE uploads SET status='done' WHERE id=?", (uid,))
            db.commit()

        flash('Document formatted successfully!', 'success')
        return redirect(url_for('docsformatter.result', uid=uid))

    except Exception as e:
        err = str(e)
        with get_db() as db:
            db.execute("UPDATE uploads SET status='error',error_msg=? WHERE id=?", (err, uid))
            db.commit()
        flash(f'Error processing document: {err}', 'error')
        return redirect(url_for('docsformatter.index'))

@docsformatter_bp.route('/result/<uid>')
def result(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    preview_html = ''
    if row['status'] == 'done' and row['output_path'] and os.path.exists(row['output_path']):
        with open(row['output_path'], 'r', encoding='utf-8', errors='replace') as f:
            saved_output = f.read()
        if row['output_path'].lower().endswith('.html'):
            preview_html = saved_output
        else:
            # Render older TXT results instead of showing literal ** markers.
            preview_html = render_document_html(saved_output, row['original_name'])
    return render_template('docsformatter/result.html', row=row, preview_html=preview_html)

@docsformatter_bp.route('/download/<uid>')
def download(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
    if not row or row['status'] != 'done':
        abort(404)
    is_html = row['output_path'].lower().endswith('.html')
    return send_file(
        row['output_path'],
        as_attachment=True,
        download_name=f"formatted_{row['original_name'].rsplit('.', 1)[0]}.{'html' if is_html else 'txt'}",
        mimetype='text/html' if is_html else 'text/plain'
    )

@docsformatter_bp.route('/history')
def history():
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM uploads ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('docsformatter/history.html', rows=rows)

# ── admin ──

@docsformatter_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    init_db()
    if session.get('df_admin'):
        return redirect(url_for('docsformatter.admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as db:
            row = db.execute("SELECT * FROM admin WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['df_admin'] = True
            flash('Welcome back!', 'success')
            return redirect(url_for('docsformatter.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('docsformatter/admin/login.html')

@docsformatter_bp.route('/admin/logout')
def admin_logout():
    session.pop('df_admin', None)
    flash('Logged out.', 'success')
    return redirect(url_for('docsformatter.index'))

@docsformatter_bp.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    init_db()
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) FROM uploads").fetchone()[0]
        done   = db.execute("SELECT COUNT(*) FROM uploads WHERE status='done'").fetchone()[0]
        errors = db.execute("SELECT COUNT(*) FROM uploads WHERE status='error'").fetchone()[0]
        recent = db.execute("SELECT * FROM uploads ORDER BY created_at DESC LIMIT 10").fetchall()
        reports = list_reports(db)
        pending_reports = pending_report_count(db)
    return render_template('docsformatter/admin/dashboard.html',
                           total=total, done=done, errors=errors, recent=recent,
                           reports=reports, pending_reports=pending_reports,
                           report_reasons=dict(REPORT_REASONS))

@docsformatter_bp.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'api':
            set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
            set_setting('groq_model',   request.form.get('groq_model', 'llama-3.1-8b-instant').strip())
            set_setting('format_style', request.form.get('format_style', 'professional').strip())
            flash('API settings saved.', 'success')
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
        return redirect(url_for('docsformatter.admin_settings'))

    settings = {
        'groq_api_key': get_setting('groq_api_key'),
        'groq_model':   get_setting('groq_model', 'llama-3.1-8b-instant'),
        'format_style': get_setting('format_style', 'professional'),
    }
    return render_template('docsformatter/admin/settings.html', settings=settings)

@docsformatter_bp.route('/admin/delete/<uid>', methods=['POST'])
@admin_required
def admin_delete(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM uploads WHERE id=?", (uid,)).fetchone()
        if row:
            for p in (row['upload_path'], row['output_path']):
                if p and os.path.exists(p):
                    os.remove(p)
            db.execute("DELETE FROM uploads WHERE id=?", (uid,))
            db.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('docsformatter.admin_dashboard'))


register_reporting_routes(docsformatter_bp, DB_PATH, 'uploads', 'df_admin')
