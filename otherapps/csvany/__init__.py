"""
CSV-to-Anything Converter  –  /otherapps/csvany
Blueprint: csvany
"""
import os, sqlite3, uuid, csv, json, io
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
DB_PATH    = os.path.join(BASE_DIR, 'csvany.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
for d in (UPLOAD_DIR, OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

OUTPUT_FORMATS = ['json', 'sql', 'excel', 'xml', 'yaml', 'markdown', 'tsv']

# ─── blueprint ────────────────────────────────────────────────────────────────
csvany_bp = Blueprint(
    'csvany', __name__,
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
        CREATE TABLE IF NOT EXISTS conversions (
            id TEXT PRIMARY KEY,
            original_name TEXT,
            input_path TEXT,
            output_path TEXT,
            output_format TEXT,
            row_count INTEGER DEFAULT 0,
            col_count INTEGER DEFAULT 0,
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
            'default_format': 'json',
            'sql_table_name': 'data',
            'max_rows': '10000',
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
        if not session.get('csvany_admin'):
            return redirect(url_for('csvany.admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── conversion helpers ────────────────────────────────────────────────────────

def read_csv(path):
    rows = []
    with open(path, 'r', encoding='utf-8-sig', errors='replace') as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for row in reader:
            rows.append(dict(row))
    return headers, rows

def to_json(headers, rows, path):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rows, f, indent=2, default=str)
    return path

def to_sql(headers, rows, path, table_name='data'):
    lines = []
    cols  = ', '.join(f'"{h}"' for h in headers)
    lines.append(f'CREATE TABLE IF NOT EXISTS "{table_name}" ({cols});')
    lines.append('')
    for row in rows:
        vals = ', '.join(
            "NULL" if v is None else f"'{str(v).replace(chr(39), chr(39)*2)}'"
            for v in row.values()
        )
        lines.append(f'INSERT INTO "{table_name}" ({cols}) VALUES ({vals});')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path

def to_excel(headers, rows, path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Data'
    ws.append(headers)
    for row in rows:
        ws.append([row.get(h, '') for h in headers])
    wb.save(path)
    return path

def to_xml(headers, rows, path):
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<data>']
    for row in rows:
        lines.append('  <row>')
        for h in headers:
            val = str(row.get(h, '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'    <{h}>{val}</{h}>')
        lines.append('  </row>')
    lines.append('</data>')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path

def to_yaml(headers, rows, path):
    import yaml
    with open(path, 'w', encoding='utf-8') as f:
        yaml.dump(rows, f, default_flow_style=False, allow_unicode=True)
    return path

def to_markdown(headers, rows, path):
    lines = []
    lines.append('| ' + ' | '.join(headers) + ' |')
    lines.append('| ' + ' | '.join(['---'] * len(headers)) + ' |')
    for row in rows:
        lines.append('| ' + ' | '.join(str(row.get(h, '')) for h in headers) + ' |')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))
    return path

def to_tsv(headers, rows, path):
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)
    return path

EXT_MAP  = {'json': '.json', 'sql': '.sql', 'excel': '.xlsx',
             'xml': '.xml', 'yaml': '.yaml', 'markdown': '.md', 'tsv': '.tsv'}
MIME_MAP = {'json': 'application/json', 'sql': 'text/plain',
             'excel': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
             'xml': 'application/xml', 'yaml': 'text/plain',
             'markdown': 'text/markdown', 'tsv': 'text/tab-separated-values'}

# ─── routes ───────────────────────────────────────────────────────────────────

@csvany_bp.route('/')
def index():
    init_db()
    default_fmt = get_setting('default_format', 'json')
    return render_template('csvany/index.html', formats=OUTPUT_FORMATS, default_fmt=default_fmt)

@csvany_bp.route('/settings', methods=['GET', 'POST'])
def groq_settings():
    init_db()
    if request.method == 'POST':
        set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
        flash('Groq API key saved.', 'success')
        return redirect(url_for('csvany.groq_settings'))
    return render_template(
        'csvany/settings.html',
        groq_api_key=get_setting('groq_api_key'),
    )

@csvany_bp.route('/convert', methods=['POST'])
def convert():
    init_db()
    file       = request.files.get('csvfile')
    out_format = request.form.get('format', 'json').lower()

    if not file or file.filename == '':
        flash('Please select a CSV file.', 'error')
        return redirect(url_for('csvany.index'))
    if not file.filename.lower().endswith('.csv'):
        flash('Only CSV files are accepted.', 'error')
        return redirect(url_for('csvany.index'))
    if out_format not in OUTPUT_FORMATS:
        flash('Invalid output format.', 'error')
        return redirect(url_for('csvany.index'))

    uid      = str(uuid.uuid4())
    safe_fn  = secure_filename(file.filename)
    up_path  = os.path.join(UPLOAD_DIR, f"{uid}_{safe_fn}")
    ext      = EXT_MAP[out_format]
    out_path = os.path.join(OUTPUT_DIR, f"{uid}_converted{ext}")
    file.save(up_path)

    with get_db() as db:
        db.execute(
            "INSERT INTO conversions (id,original_name,input_path,output_path,output_format,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, safe_fn, up_path, out_path, out_format, 'processing', datetime.utcnow().isoformat())
        )
        db.commit()

    try:
        max_rows = int(get_setting('max_rows', '10000'))
        table_nm = get_setting('sql_table_name', 'data')
        headers, rows = read_csv(up_path)
        rows = rows[:max_rows]

        if out_format == 'json':
            to_json(headers, rows, out_path)
        elif out_format == 'sql':
            to_sql(headers, rows, out_path, table_nm)
        elif out_format == 'excel':
            to_excel(headers, rows, out_path)
        elif out_format == 'xml':
            to_xml(headers, rows, out_path)
        elif out_format == 'yaml':
            to_yaml(headers, rows, out_path)
        elif out_format == 'markdown':
            to_markdown(headers, rows, out_path)
        elif out_format == 'tsv':
            to_tsv(headers, rows, out_path)

        with get_db() as db:
            db.execute(
                "UPDATE conversions SET status='done',row_count=?,col_count=? WHERE id=?",
                (len(rows), len(headers), uid)
            )
            db.commit()

        flash('CSV converted successfully!', 'success')
        return redirect(url_for('csvany.result', uid=uid))

    except Exception as e:
        err = str(e)
        with get_db() as db:
            db.execute("UPDATE conversions SET status='error',error_msg=? WHERE id=?", (err, uid))
            db.commit()
        flash(f'Conversion error: {err}', 'error')
        return redirect(url_for('csvany.index'))

@csvany_bp.route('/result/<uid>')
def result(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM conversions WHERE id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    preview = ''
    if row['status'] == 'done' and row['output_path'] and os.path.exists(row['output_path']):
        if row['output_format'] != 'excel':
            with open(row['output_path'], 'r', errors='replace') as f:
                preview = f.read(3000)
    return render_template('csvany/result.html', row=row, preview=preview)

@csvany_bp.route('/download/<uid>')
def download(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM conversions WHERE id=?", (uid,)).fetchone()
    if not row or row['status'] != 'done':
        abort(404)
    fmt  = row['output_format']
    ext  = EXT_MAP.get(fmt, '.txt')
    mime = MIME_MAP.get(fmt, 'application/octet-stream')
    base = row['original_name'].rsplit('.', 1)[0]
    return send_file(
        row['output_path'],
        as_attachment=True,
        download_name=f"{base}_converted{ext}",
        mimetype=mime
    )

@csvany_bp.route('/history')
def history():
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM conversions ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('csvany/history.html', rows=rows)

# ── admin ──

@csvany_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    init_db()
    if session.get('csvany_admin'):
        return redirect(url_for('csvany.admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as db:
            row = db.execute("SELECT * FROM admin WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['csvany_admin'] = True
            flash('Welcome back!', 'success')
            return redirect(url_for('csvany.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('csvany/admin/login.html')

@csvany_bp.route('/admin/logout')
def admin_logout():
    session.pop('csvany_admin', None)
    flash('Logged out.', 'success')
    return redirect(url_for('csvany.index'))

@csvany_bp.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    init_db()
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) FROM conversions").fetchone()[0]
        done   = db.execute("SELECT COUNT(*) FROM conversions WHERE status='done'").fetchone()[0]
        errors = db.execute("SELECT COUNT(*) FROM conversions WHERE status='error'").fetchone()[0]
        recent = db.execute("SELECT * FROM conversions ORDER BY created_at DESC LIMIT 10").fetchall()
        reports = list_reports(db)
        pending_reports = pending_report_count(db)
    return render_template('csvany/admin/dashboard.html',
                           total=total, done=done, errors=errors, recent=recent,
                           reports=reports, pending_reports=pending_reports,
                           report_reasons=dict(REPORT_REASONS))

@csvany_bp.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'app':
            set_setting('default_format',  request.form.get('default_format', 'json'))
            set_setting('sql_table_name',  request.form.get('sql_table_name', 'data').strip())
            set_setting('max_rows',        request.form.get('max_rows', '10000').strip())
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
        return redirect(url_for('csvany.admin_settings'))

    settings = {
        'default_format': get_setting('default_format', 'json'),
        'sql_table_name': get_setting('sql_table_name', 'data'),
        'max_rows':       get_setting('max_rows', '10000'),
    }
    return render_template('csvany/admin/settings.html', settings=settings, formats=OUTPUT_FORMATS)

@csvany_bp.route('/admin/delete/<uid>', methods=['POST'])
@admin_required
def admin_delete(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM conversions WHERE id=?", (uid,)).fetchone()
        if row:
            for p in (row['input_path'], row['output_path']):
                if p and os.path.exists(p):
                    os.remove(p)
            db.execute("DELETE FROM conversions WHERE id=?", (uid,))
            db.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('csvany.admin_dashboard'))


register_reporting_routes(csvany_bp, DB_PATH, 'conversions', 'csvany_admin')
