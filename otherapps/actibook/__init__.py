"""
Activity Book Generator  –  /otherapps/actibook
Blueprint: actibook
Generates word searches, Sudoku, crosswords & trivia in a PDF activity book.
"""
import os, sqlite3, uuid, json, random, string
from datetime import datetime
from functools import wraps
from flask import (Blueprint, render_template, request, redirect, url_for,
                   session, flash, send_file, abort)
from werkzeug.security import generate_password_hash, check_password_hash
from otherapps.reporting import (
    REPORT_REASONS, ensure_reports_table, list_reports, pending_report_count,
    register_reporting_routes,
)

# ─── paths ────────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(__file__)
DB_PATH    = os.path.join(BASE_DIR, 'actibook.db')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs')
os.makedirs(OUTPUT_DIR, exist_ok=True)

DIFFICULTIES = ['easy', 'medium', 'hard']
ACTIVITY_TYPES = ['word_search', 'sudoku', 'trivia', 'crossword']

# ─── blueprint ────────────────────────────────────────────────────────────────
actibook_bp = Blueprint(
    'actibook', __name__,
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
        CREATE TABLE IF NOT EXISTS books (
            id TEXT PRIMARY KEY,
            theme TEXT,
            difficulty TEXT,
            pages INTEGER,
            activities TEXT,
            pdf_path TEXT,
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
            'groq_model': 'llama-3.1-8b-instant',
            'max_pages': '20',
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
        if not session.get('actibook_admin'):
            return redirect(url_for('actibook.admin_login'))
        return f(*args, **kwargs)
    return decorated

# ─── activity generators ──────────────────────────────────────────────────────

def gen_sudoku(difficulty='medium'):
    """Generate a valid Sudoku puzzle using backtracking."""
    def is_valid(board, row, col, num):
        if num in board[row]:
            return False
        if num in [board[r][col] for r in range(9)]:
            return False
        br, bc = 3 * (row // 3), 3 * (col // 3)
        for r in range(br, br + 3):
            for c in range(bc, bc + 3):
                if board[r][c] == num:
                    return False
        return True

    def solve(board):
        for r in range(9):
            for c in range(9):
                if board[r][c] == 0:
                    nums = list(range(1, 10))
                    random.shuffle(nums)
                    for n in nums:
                        if is_valid(board, r, c, n):
                            board[r][c] = n
                            if solve(board):
                                return True
                            board[r][c] = 0
                    return False
        return True

    solution = [[0] * 9 for _ in range(9)]
    solve(solution)

    remove_count = {'easy': 30, 'medium': 45, 'hard': 55}.get(difficulty, 45)
    puzzle = [row[:] for row in solution]
    cells = [(r, c) for r in range(9) for c in range(9)]
    random.shuffle(cells)
    for r, c in cells[:remove_count]:
        puzzle[r][c] = 0

    return puzzle, solution

def gen_word_search(words, grid_size=15):
    """Place words in a grid and fill remaining cells with random letters."""
    grid = [['.' for _ in range(grid_size)] for _ in range(grid_size)]
    placed = []
    directions = [(0,1),(1,0),(1,1),(1,-1),(0,-1),(-1,0),(-1,-1),(-1,1)]

    for word in words:
        word = word.upper()
        placed_ok = False
        for _ in range(200):
            dr, dc = random.choice(directions)
            r = random.randint(0, grid_size - 1)
            c = random.randint(0, grid_size - 1)
            er = r + dr * (len(word) - 1)
            ec = c + dc * (len(word) - 1)
            if not (0 <= er < grid_size and 0 <= ec < grid_size):
                continue
            can_place = True
            for i, ch in enumerate(word):
                cell = grid[r + dr * i][c + dc * i]
                if cell not in ('.', ch):
                    can_place = False
                    break
            if can_place:
                for i, ch in enumerate(word):
                    grid[r + dr * i][c + dc * i] = ch
                placed.append(word)
                placed_ok = True
                break

    # fill blanks
    for r in range(grid_size):
        for c in range(grid_size):
            if grid[r][c] == '.':
                grid[r][c] = random.choice(string.ascii_uppercase)
    return grid, placed

def groq_word_list(theme, difficulty, api_key, model, count=15):
    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return only a JSON array of strings. No extra text."},
            {"role": "user", "content":
             f"Give me {count} words related to the theme '{theme}' suitable for a {difficulty} word search puzzle. "
             f"Words should be 4-12 letters, no spaces or hyphens. Return as JSON array only."},
        ],
        temperature=0.7, max_tokens=300,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return json.loads(raw)

def groq_trivia(theme, difficulty, api_key, model, count=10):
    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return only a valid JSON array. No extra text."},
            {"role": "user", "content":
             f"Generate {count} trivia questions about '{theme}' at {difficulty} level. "
             f"Return as JSON array of objects with keys: 'question', 'options' (array of 4 strings), 'answer' (index 0-3). "
             f"Only return the JSON array."},
        ],
        temperature=0.7, max_tokens=1500,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return json.loads(raw)

def groq_crossword_clues(theme, difficulty, api_key, model, count=8):
    from groq import Groq
    client = Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return only a valid JSON array. No extra text."},
            {"role": "user", "content":
             f"Generate {count} crossword clues about '{theme}' at {difficulty} level. "
             f"Return as JSON array of objects with keys: 'word' (5-10 uppercase letters, no spaces), 'clue' (string). "
             f"Only return the JSON array."},
        ],
        temperature=0.7, max_tokens=800,
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
    return json.loads(raw)

def build_pdf(uid, theme, difficulty, pages, activities_data, api_key, model):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import cm
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                    Table, TableStyle, PageBreak, HRFlowable)
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    pdf_path = os.path.join(OUTPUT_DIR, f"{uid}_activity_book.pdf")
    doc = SimpleDocTemplate(pdf_path, pagesize=A4,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            leftMargin=2*cm, rightMargin=2*cm)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle('Title', parent=styles['Title'],
                                 fontSize=24, spaceAfter=6, alignment=TA_CENTER)
    h1_style    = ParagraphStyle('H1', parent=styles['Heading1'],
                                 fontSize=16, spaceAfter=8, spaceBefore=14)
    h2_style    = ParagraphStyle('H2', parent=styles['Heading2'],
                                 fontSize=13, spaceAfter=6, spaceBefore=10)
    body_style  = ParagraphStyle('Body', parent=styles['Normal'],
                                 fontSize=10, spaceAfter=4)
    cell_style  = ParagraphStyle('Cell', parent=styles['Normal'],
                                 fontSize=9, alignment=TA_CENTER)
    q_style     = ParagraphStyle('Q', parent=styles['Normal'],
                                 fontSize=10, spaceAfter=3, spaceBefore=3)

    story = []

    # Cover page
    story.append(Spacer(1, 3*cm))
    story.append(Paragraph(f"Activity Book", title_style))
    story.append(Paragraph(f"Theme: {theme.title()}", styles['Heading2']))
    story.append(Paragraph(f"Difficulty: {difficulty.title()}", styles['Heading3']))
    story.append(Spacer(1, 1*cm))
    story.append(Paragraph(f"Name: {'_'*30}", body_style))
    story.append(Paragraph(f"Date: {'_'*20}", body_style))
    story.append(PageBreak())

    page_count = 0

    for act in activities_data:
        if page_count >= pages:
            break
        atype = act['type']

        if atype == 'word_search':
            story.append(Paragraph(f"Word Search – {theme.title()}", h1_style))
            story.append(Paragraph(
                "Find all the hidden words. Words may be horizontal, vertical, or diagonal.",
                body_style))
            story.append(Spacer(1, 0.3*cm))
            grid  = act['grid']
            gsize = len(grid)
            tdata = [[Paragraph(grid[r][c], cell_style) for c in range(gsize)] for r in range(gsize)]
            cell_w = 14 * cm / gsize
            cell_h = cell_w
            t = Table(tdata, colWidths=[cell_w]*gsize, rowHeights=[cell_h]*gsize)
            t.setStyle(TableStyle([
                ('GRID',      (0,0), (-1,-1), 0.5, colors.lightgrey),
                ('ALIGN',     (0,0), (-1,-1), 'CENTER'),
                ('VALIGN',    (0,0), (-1,-1), 'MIDDLE'),
                ('FONTNAME',  (0,0), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE',  (0,0), (-1,-1), 8 if gsize > 12 else 10),
            ]))
            story.append(t)
            story.append(Spacer(1, 0.5*cm))
            story.append(Paragraph("Words to find:", h2_style))
            word_text = '   '.join(act.get('placed_words', []))
            story.append(Paragraph(word_text, body_style))
            story.append(PageBreak())
            page_count += 1

        elif atype == 'sudoku':
            story.append(Paragraph(f"Sudoku – {difficulty.title()}", h1_style))
            story.append(Paragraph(
                "Fill the grid so every row, column, and 3×3 box contains the digits 1–9.",
                body_style))
            story.append(Spacer(1, 0.3*cm))
            puzzle   = act['puzzle']
            solution = act['solution']
            tdata = []
            for r in range(9):
                row_cells = []
                for c in range(9):
                    val = str(puzzle[r][c]) if puzzle[r][c] != 0 else ''
                    p = Paragraph(val, cell_style)
                    row_cells.append(p)
                tdata.append(row_cells)
            cw = 1.4 * cm
            t = Table(tdata, colWidths=[cw]*9, rowHeights=[cw]*9)
            grid_style = [
                ('GRID',      (0,0), (-1,-1), 0.5, colors.grey),
                ('BOX',       (0,0), (2,2), 2, colors.black),
                ('BOX',       (3,0), (5,2), 2, colors.black),
                ('BOX',       (6,0), (8,2), 2, colors.black),
                ('BOX',       (0,3), (2,5), 2, colors.black),
                ('BOX',       (3,3), (5,5), 2, colors.black),
                ('BOX',       (6,3), (8,5), 2, colors.black),
                ('BOX',       (0,6), (2,8), 2, colors.black),
                ('BOX',       (3,6), (5,8), 2, colors.black),
                ('BOX',       (6,6), (8,8), 2, colors.black),
                ('ALIGN',     (0,0), (-1,-1), 'CENTER'),
                ('VALIGN',    (0,0), (-1,-1), 'MIDDLE'),
                ('FONTNAME',  (0,0), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE',  (0,0), (-1,-1), 12),
            ]
            # bold given numbers
            for r in range(9):
                for c in range(9):
                    if puzzle[r][c] != 0:
                        grid_style.append(('BACKGROUND', (c,r), (c,r), colors.HexColor('#f0f0f0')))
            t.setStyle(TableStyle(grid_style))
            story.append(t)
            story.append(PageBreak())
            page_count += 1
            # answer page
            story.append(Paragraph("Sudoku Solution", h1_style))
            sol_data = [[Paragraph(str(solution[r][c]), cell_style) for c in range(9)] for r in range(9)]
            sol_t = Table(sol_data, colWidths=[cw]*9, rowHeights=[cw]*9)
            sol_t.setStyle(TableStyle([
                ('GRID',   (0,0), (-1,-1), 0.5, colors.grey),
                ('BOX',    (0,0), (2,2), 2, colors.black),
                ('BOX',    (3,0), (5,2), 2, colors.black),
                ('BOX',    (6,0), (8,2), 2, colors.black),
                ('BOX',    (0,3), (2,5), 2, colors.black),
                ('BOX',    (3,3), (5,5), 2, colors.black),
                ('BOX',    (6,3), (8,5), 2, colors.black),
                ('BOX',    (0,6), (2,8), 2, colors.black),
                ('BOX',    (3,6), (5,8), 2, colors.black),
                ('BOX',    (6,6), (8,8), 2, colors.black),
                ('ALIGN',  (0,0), (-1,-1), 'CENTER'),
                ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
                ('FONTNAME', (0,0), (-1,-1), 'Helvetica-Bold'),
                ('FONTSIZE', (0,0), (-1,-1), 12),
            ]))
            story.append(sol_t)
            story.append(PageBreak())

        elif atype == 'trivia':
            story.append(Paragraph(f"Trivia Quiz – {theme.title()}", h1_style))
            story.append(Paragraph(f"Difficulty: {difficulty.title()}", body_style))
            story.append(Spacer(1, 0.2*cm))
            qlist = act.get('questions', [])
            for i, q in enumerate(qlist, 1):
                story.append(Paragraph(f"<b>Q{i}.</b> {q.get('question','')}", q_style))
                for j, opt in enumerate(q.get('options', [])):
                    letter = chr(65 + j)
                    story.append(Paragraph(f"&nbsp;&nbsp;&nbsp;{letter}. {opt}", q_style))
                story.append(Spacer(1, 0.15*cm))
            story.append(PageBreak())
            page_count += 1
            # answers
            story.append(Paragraph("Trivia Answers", h1_style))
            for i, q in enumerate(qlist, 1):
                ans_idx = q.get('answer', 0)
                opts    = q.get('options', [])
                ans_txt = opts[ans_idx] if isinstance(ans_idx, int) and ans_idx < len(opts) else str(ans_idx)
                story.append(Paragraph(f"<b>Q{i}.</b> {chr(65+ans_idx) if isinstance(ans_idx,int) else ''} – {ans_txt}", q_style))
            story.append(PageBreak())

        elif atype == 'crossword':
            story.append(Paragraph(f"Crossword Clues – {theme.title()}", h1_style))
            story.append(Paragraph(
                "Use the clues below to fill in the words. (Grid available in printed version.)",
                body_style))
            story.append(Spacer(1, 0.3*cm))
            clues = act.get('clues', [])
            story.append(Paragraph("Clues:", h2_style))
            for i, c in enumerate(clues, 1):
                story.append(Paragraph(f"<b>{i}.</b> {c.get('clue','')}  ({len(c.get('word',''))} letters)", q_style))
            story.append(Spacer(1, 0.5*cm))
            story.append(Paragraph("Answers:", h2_style))
            for i, c in enumerate(clues, 1):
                story.append(Paragraph(f"<b>{i}.</b> {c.get('word','')}", q_style))
            story.append(PageBreak())
            page_count += 1

    doc.build(story)
    return pdf_path

# ─── routes ───────────────────────────────────────────────────────────────────

@actibook_bp.route('/')
def index():
    init_db()
    max_pages = int(get_setting('max_pages', '20'))
    return render_template('actibook/index.html',
                           difficulties=DIFFICULTIES,
                           activity_types=ACTIVITY_TYPES,
                           max_pages=max_pages)

@actibook_bp.route('/generate', methods=['POST'])
def generate():
    init_db()
    theme      = request.form.get('theme', '').strip()
    difficulty = request.form.get('difficulty', 'medium')
    pages      = int(request.form.get('pages', 4))
    sel_acts   = request.form.getlist('activities')

    if not theme:
        flash('Please enter a theme for your activity book.', 'error')
        return redirect(url_for('actibook.index'))
    if not sel_acts:
        flash('Please select at least one activity type.', 'error')
        return redirect(url_for('actibook.index'))

    max_pages = int(get_setting('max_pages', '20'))
    pages     = min(pages, max_pages)
    api_key   = get_setting('groq_api_key')
    model     = get_setting('groq_model', 'llama-3.1-8b-instant')

    uid = str(uuid.uuid4())
    with get_db() as db:
        db.execute(
            "INSERT INTO books (id,theme,difficulty,pages,activities,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (uid, theme, difficulty, pages, ','.join(sel_acts), 'processing', datetime.utcnow().isoformat())
        )
        db.commit()

    try:
        activities_data = []

        for act_type in sel_acts:
            if act_type == 'word_search':
                if api_key:
                    try:
                        words = groq_word_list(theme, difficulty, api_key, model)
                    except Exception:
                        words = [theme.upper()] + ['PUZZLE', 'ACTIVITY', 'BOOK', 'WORD', 'SEARCH',
                                                    'LEARN', 'PLAY', 'FUN', 'GAME', 'QUIZ']
                else:
                    words = ['PUZZLE', 'ACTIVITY', 'BOOK', 'WORD', 'SEARCH',
                             'LEARN', 'PLAY', 'FUN', 'GAME', 'QUIZ']
                grid_size = {'easy': 12, 'medium': 15, 'hard': 18}.get(difficulty, 15)
                grid, placed = gen_word_search(words[:15], grid_size)
                activities_data.append({'type': 'word_search', 'grid': grid, 'placed_words': placed})

            elif act_type == 'sudoku':
                puzzle, solution = gen_sudoku(difficulty)
                activities_data.append({'type': 'sudoku', 'puzzle': puzzle, 'solution': solution})

            elif act_type == 'trivia':
                if not api_key:
                    flash('Trivia requires a Groq API key. Set it in Admin → Settings.', 'warning')
                else:
                    try:
                        questions = groq_trivia(theme, difficulty, api_key, model)
                        activities_data.append({'type': 'trivia', 'questions': questions})
                    except Exception as e:
                        flash(f'Trivia generation failed: {e}. Skipping trivia section.', 'warning')

            elif act_type == 'crossword':
                if not api_key:
                    flash('Crossword requires a Groq API key. Set it in Admin → Settings.', 'warning')
                else:
                    try:
                        clues = groq_crossword_clues(theme, difficulty, api_key, model)
                        activities_data.append({'type': 'crossword', 'clues': clues})
                    except Exception as e:
                        flash(f'Crossword generation failed: {e}. Skipping crossword section.', 'warning')

        if not activities_data:
            raise RuntimeError("No activities could be generated. Please check your settings.")

        pdf_path = build_pdf(uid, theme, difficulty, pages, activities_data, api_key, model)

        with get_db() as db:
            db.execute("UPDATE books SET status='done',pdf_path=? WHERE id=?", (pdf_path, uid))
            db.commit()

        flash('Activity book generated successfully!', 'success')
        return redirect(url_for('actibook.result', uid=uid))

    except Exception as e:
        err = str(e)
        with get_db() as db:
            db.execute("UPDATE books SET status='error',error_msg=? WHERE id=?", (err, uid))
            db.commit()
        flash(f'Error generating book: {err}', 'error')
        return redirect(url_for('actibook.index'))

@actibook_bp.route('/result/<uid>')
def result(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM books WHERE id=?", (uid,)).fetchone()
    if not row:
        abort(404)
    return render_template('actibook/result.html', row=row)

@actibook_bp.route('/download/<uid>')
def download(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM books WHERE id=?", (uid,)).fetchone()
    if not row or row['status'] != 'done' or not row['pdf_path']:
        abort(404)
    slug = row['theme'].replace(' ', '_').lower()
    return send_file(
        row['pdf_path'],
        as_attachment=True,
        download_name=f"activity_book_{slug}.pdf",
        mimetype='application/pdf'
    )

@actibook_bp.route('/history')
def history():
    init_db()
    with get_db() as db:
        rows = db.execute("SELECT * FROM books ORDER BY created_at DESC LIMIT 50").fetchall()
    return render_template('actibook/history.html', rows=rows)

@actibook_bp.route('/settings', methods=['GET', 'POST'])
def groq_settings():
    init_db()
    if request.method == 'POST':
        set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
        flash('Groq API key saved.', 'success')
        return redirect(url_for('actibook.groq_settings'))
    return render_template(
        'actibook/settings.html',
        groq_api_key=get_setting('groq_api_key'),
    )

# ── admin ──

@actibook_bp.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    init_db()
    if session.get('actibook_admin'):
        return redirect(url_for('actibook.admin_dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        with get_db() as db:
            row = db.execute("SELECT * FROM admin WHERE username=?", (username,)).fetchone()
        if row and check_password_hash(row['password_hash'], password):
            session['actibook_admin'] = True
            flash('Welcome back!', 'success')
            return redirect(url_for('actibook.admin_dashboard'))
        flash('Invalid credentials.', 'error')
    return render_template('actibook/admin/login.html')

@actibook_bp.route('/admin/logout')
def admin_logout():
    session.pop('actibook_admin', None)
    flash('Logged out.', 'success')
    return redirect(url_for('actibook.index'))

@actibook_bp.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    init_db()
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        done   = db.execute("SELECT COUNT(*) FROM books WHERE status='done'").fetchone()[0]
        errors = db.execute("SELECT COUNT(*) FROM books WHERE status='error'").fetchone()[0]
        recent = db.execute("SELECT * FROM books ORDER BY created_at DESC LIMIT 10").fetchall()
        reports = list_reports(db)
        pending_reports = pending_report_count(db)
    return render_template('actibook/admin/dashboard.html',
                           total=total, done=done, errors=errors, recent=recent,
                           reports=reports, pending_reports=pending_reports,
                           report_reasons=dict(REPORT_REASONS))

@actibook_bp.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    init_db()
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'api':
            set_setting('groq_api_key', request.form.get('groq_api_key', '').strip())
            set_setting('groq_model',   request.form.get('groq_model', 'llama-3.1-8b-instant').strip())
            set_setting('max_pages',    request.form.get('max_pages', '20').strip())
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
        return redirect(url_for('actibook.admin_settings'))

    settings = {
        'groq_api_key': get_setting('groq_api_key'),
        'groq_model':   get_setting('groq_model', 'llama-3.1-8b-instant'),
        'max_pages':    get_setting('max_pages', '20'),
    }
    return render_template('actibook/admin/settings.html', settings=settings)

@actibook_bp.route('/admin/delete/<uid>', methods=['POST'])
@admin_required
def admin_delete(uid):
    init_db()
    with get_db() as db:
        row = db.execute("SELECT * FROM books WHERE id=?", (uid,)).fetchone()
        if row:
            if row['pdf_path'] and os.path.exists(row['pdf_path']):
                os.remove(row['pdf_path'])
            db.execute("DELETE FROM books WHERE id=?", (uid,))
            db.commit()
    flash('Record deleted.', 'success')
    return redirect(url_for('actibook.admin_dashboard'))


register_reporting_routes(actibook_bp, DB_PATH, 'books', 'actibook_admin')
