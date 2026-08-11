import os
import re
import io
import json
import hashlib
import logging
import functools
from flask import Flask, render_template, request, redirect, url_for, session, flash, Response, send_from_directory, abort
from werkzeug.middleware.proxy_fix import ProxyFix
from datetime import datetime, timedelta
from config import config
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect

logging.basicConfig(level=logging.DEBUG)

# ---------------------------------------------------------------------------
# Firebase initialisation
# ---------------------------------------------------------------------------

_firestore_client = None


def get_db():
    global _firestore_client
    if _firestore_client is not None:
        return _firestore_client

    service_account_raw = os.environ.get('FIREBASE_SERVICE_ACCOUNT')
    if not service_account_raw:
        raise RuntimeError(
            "FIREBASE_SERVICE_ACCOUNT environment variable is not set. "
            "Add your Firebase service-account JSON as that secret."
        )

    import firebase_admin
    from firebase_admin import credentials, firestore as fb_firestore

    if not firebase_admin._apps:
        try:
            service_account_info = json.loads(service_account_raw)
            cred = credentials.Certificate(service_account_info)
        except (json.JSONDecodeError, ValueError):
            cred = credentials.Certificate(service_account_raw)
        firebase_admin.initialize_app(cred)

    _firestore_client = fb_firestore.client()
    return _firestore_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def slugify(text):
    text = str(text).lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[\s_]+', '-', text)
    text = re.sub(r'-+', '-', text)
    return text.strip('-')


def hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip('#')
    if len(hex_color) == 3:
        hex_color = ''.join(c * 2 for c in hex_color)
    try:
        return ', '.join(str(int(hex_color[i:i + 2], 16)) for i in (0, 2, 4))
    except Exception as e:
        logging.warning(f"hex_to_rgb: invalid hex '{hex_color}': {e}")
        return '102, 126, 234'


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class Post:
    def __init__(self, doc_id, data):
        self.id = doc_id
        self.title = data.get('title', '')
        self.content = data.get('content', '')
        self.featured_image = data.get('featured_image') or None
        raw_tags = data.get('tags', [])
        self.tags = raw_tags if isinstance(raw_tags, list) else []
        raw_ts = data.get('created_at')
        self.created_at = raw_ts if isinstance(raw_ts, datetime) else datetime.utcnow()

    def to_dict(self):
        return {
            'title': self.title,
            'content': self.content,
            'featured_image': self.featured_image,
            'tags': self.tags,
            'created_at': self.created_at,
        }


DEFAULT_SETTINGS = {
    'blog_title': 'Blog CMS',
    'blog_description': 'Welcome to Our Blog',
    'primary_color': '#667eea',
    'secondary_color': '#764ba2',
    'background_color': '#667eea',
    'overall_background': '#1a1a2e',
    'card_background': '#ffffff',
    'text_color': '#333333',
    'navbar_color': '#000000',
    'navbar_text_color': '#ffffff',
    'heading_font': 'Inter',
    'body_font': 'Inter',
}

PRESET_THEMES = [
    {
        'id':                 'ocean',
        'name':               'Ocean Breeze',
        'emoji':              '🌊',
        'primary_color':      '#06b6d4',
        'secondary_color':    '#0284c7',
        'background_color':   '#0ea5e9',
        'overall_background': '#0c1a2e',
        'card_background':    '#e0f7fa',
        'text_color':         '#0c1a2e',
        'navbar_color':       '#0a1628',
        'navbar_text_color':  '#e0f7fa',
        'heading_font':       'Space Grotesk',
        'body_font':          'DM Sans',
    },
    {
        'id':                 'sunset',
        'name':               'Sunset Glow',
        'emoji':              '🌅',
        'primary_color':      '#f97316',
        'secondary_color':    '#ec4899',
        'background_color':   '#f97316',
        'overall_background': '#1a0a00',
        'card_background':    '#fff7ed',
        'text_color':         '#1c1917',
        'navbar_color':       '#2d1206',
        'navbar_text_color':  '#fed7aa',
        'heading_font':       'Playfair Display',
        'body_font':          'Lora',
    },
    {
        'id':                 'forest',
        'name':               'Forest Green',
        'emoji':              '🌿',
        'primary_color':      '#22c55e',
        'secondary_color':    '#16a34a',
        'background_color':   '#22c55e',
        'overall_background': '#071a0a',
        'card_background':    '#f0fdf4',
        'text_color':         '#14532d',
        'navbar_color':       '#052e16',
        'navbar_text_color':  '#bbf7d0',
        'heading_font':       'Merriweather',
        'body_font':          'Inter',
    },
    {
        'id':                 'midnight',
        'name':               'Midnight Purple',
        'emoji':              '🔮',
        'primary_color':      '#a855f7',
        'secondary_color':    '#ec4899',
        'background_color':   '#a855f7',
        'overall_background': '#0f0a1a',
        'card_background':    '#faf5ff',
        'text_color':         '#1a0a2e',
        'navbar_color':       '#0a0614',
        'navbar_text_color':  '#e9d5ff',
        'heading_font':       'Poppins',
        'body_font':          'Nunito',
    },
    {
        'id':                 'crimson',
        'name':               'Crimson Gold',
        'emoji':              '🔥',
        'primary_color':      '#dc2626',
        'secondary_color':    '#d97706',
        'background_color':   '#dc2626',
        'overall_background': '#1a0505',
        'card_background':    '#fffbeb',
        'text_color':         '#1c1917',
        'navbar_color':       '#2d0a0a',
        'navbar_text_color':  '#fde68a',
        'heading_font':       'Raleway',
        'body_font':          'Montserrat',
    },
]

AVAILABLE_FONTS = [
    {'name': 'Inter',            'category': 'sans-serif',  'google': False},
    {'name': 'Poppins',          'category': 'sans-serif',  'google': True},
    {'name': 'Montserrat',       'category': 'sans-serif',  'google': True},
    {'name': 'Raleway',          'category': 'sans-serif',  'google': True},
    {'name': 'Space Grotesk',    'category': 'sans-serif',  'google': True},
    {'name': 'DM Sans',          'category': 'sans-serif',  'google': True},
    {'name': 'Nunito',           'category': 'sans-serif',  'google': True},
    {'name': 'Playfair Display', 'category': 'serif',       'google': True},
    {'name': 'Merriweather',     'category': 'serif',       'google': True},
    {'name': 'Lora',             'category': 'serif',       'google': True},
    {'name': 'Source Serif 4',   'category': 'serif',       'google': True},
    {'name': 'Roboto Mono',      'category': 'monospace',   'google': True},
]


class SiteSettings:
    def __init__(self, data=None):
        d = {**DEFAULT_SETTINGS, **(data or {})}
        self.blog_title        = d['blog_title']
        self.blog_description  = d['blog_description']
        self.primary_color     = d['primary_color']
        self.secondary_color   = d['secondary_color']
        self.background_color  = d['background_color']
        self.overall_background= d['overall_background']
        self.card_background   = d['card_background']
        self.text_color        = d['text_color']
        self.navbar_color      = d['navbar_color']
        self.navbar_text_color = d.get('navbar_text_color', '#ffffff')
        self.heading_font      = d.get('heading_font', 'Inter')
        self.body_font         = d.get('body_font', 'Inter')

    def to_dict(self):
        return {
            'blog_title':        self.blog_title,
            'blog_description':  self.blog_description,
            'primary_color':     self.primary_color,
            'secondary_color':   self.secondary_color,
            'background_color':  self.background_color,
            'overall_background':self.overall_background,
            'card_background':   self.card_background,
            'text_color':        self.text_color,
            'navbar_color':      self.navbar_color,
            'navbar_text_color': self.navbar_text_color,
            'heading_font':      self.heading_font,
            'body_font':         self.body_font,
        }


class BlogInfo:
    def __init__(self, blog_id, data):
        self.id          = blog_id
        self.name        = data.get('name', blog_id)
        self.slug        = data.get('slug', blog_id)
        self.description = data.get('description', '')
        raw_ts           = data.get('created_at')
        self.created_at  = raw_ts if isinstance(raw_ts, datetime) else datetime.utcnow()
        self.post_count  = data.get('post_count', 0)

    def to_dict(self):
        return {
            'name':        self.name,
            'slug':        self.slug,
            'description': self.description,
            'created_at':  self.created_at,
        }


# ---------------------------------------------------------------------------
# Blog registry Firestore helpers
# ---------------------------------------------------------------------------

def fs_list_blogs():
    db = get_db()
    docs = list(db.collection('blogs').stream())
    blogs = []
    for doc in docs:
        b = BlogInfo(doc.id, doc.to_dict())
        posts_snap = list(db.collection('blogs').document(doc.id).collection('posts').stream())
        b.post_count = len(posts_snap)
        blogs.append(b)
    return sorted(blogs, key=lambda b: b.created_at, reverse=True)


def fs_get_blog(blog_id):
    db = get_db()
    doc = db.collection('blogs').document(blog_id).get()
    if not doc.exists:
        return None
    return BlogInfo(doc.id, doc.to_dict())


def fs_create_blog(name, description='', initial_username='admin',
                   initial_password='admin123', copy_from=None):
    db = get_db()
    blog_id = slugify(name)
    if not blog_id:
        raise ValueError("Blog name is invalid or produces an empty ID.")
    existing = db.collection('blogs').document(blog_id).get()
    if existing.exists:
        raise ValueError(f"A blog with the ID '{blog_id}' already exists.")

    db.collection('blogs').document(blog_id).set({
        'name':        name,
        'slug':        blog_id,
        'description': description,
        'created_at':  datetime.utcnow(),
    })

    db.collection('blogs').document(blog_id).collection('admin').document('credentials').set({
        'username':      initial_username,
        'password_hash': generate_password_hash(initial_password),
    })

    if copy_from:
        src_settings = db.collection('blogs').document(copy_from).collection('settings').document('main').get()
        settings_data = src_settings.to_dict() if src_settings.exists else dict(DEFAULT_SETTINGS)
        settings_data['blog_title']       = name
        settings_data['blog_description'] = description or settings_data.get('blog_description', 'Welcome to Our Blog')
    else:
        settings_data = {**DEFAULT_SETTINGS, 'blog_title': name,
                         'blog_description': description or 'Welcome to Our Blog'}

    db.collection('blogs').document(blog_id).collection('settings').document('main').set(settings_data)

    if copy_from:
        src_posts = db.collection('blogs').document(copy_from).collection('posts').stream()
        for post_doc in src_posts:
            db.collection('blogs').document(blog_id).collection('posts').add(post_doc.to_dict())

    return blog_id


def fs_delete_blog(blog_id):
    db = get_db()
    for sub in ('posts', 'settings', 'admin', 'activity_log'):
        for doc in db.collection('blogs').document(blog_id).collection(sub).stream():
            doc.reference.delete()
    db.collection('blogs').document(blog_id).delete()


# ---------------------------------------------------------------------------
# Blog-scoped Firestore helpers
# ---------------------------------------------------------------------------

def _blog_col(blog_id, col):
    return get_db().collection('blogs').document(blog_id).collection(col)


def fs_blog_get_all_posts(blog_id):
    docs = _blog_col(blog_id, 'posts').order_by('created_at', direction='DESCENDING').stream()
    return [Post(d.id, d.to_dict()) for d in docs]


def fs_blog_get_post(blog_id, post_id):
    doc = _blog_col(blog_id, 'posts').document(str(post_id)).get()
    return Post(doc.id, doc.to_dict()) if doc.exists else None


def fs_blog_create_post(blog_id, title, content, featured_image=None, tags=None):
    data = {'title': title, 'content': content,
            'featured_image': featured_image, 'tags': tags or [],
            'created_at': datetime.utcnow()}
    _, ref = _blog_col(blog_id, 'posts').add(data)
    return ref.id


def fs_blog_update_post(blog_id, post_id, title, content, featured_image=None, tags=None):
    _blog_col(blog_id, 'posts').document(str(post_id)).update({
        'title': title, 'content': content,
        'featured_image': featured_image, 'tags': tags or [],
    })


def fs_blog_delete_post(blog_id, post_id):
    _blog_col(blog_id, 'posts').document(str(post_id)).delete()


def fs_blog_get_all_tags(blog_id):
    tag_set = {}
    for p in fs_blog_get_all_posts(blog_id):
        for t in p.tags:
            tag_set[slugify(t)] = t
    return sorted(tag_set.values(), key=str.lower)


def fs_blog_get_posts_by_tag(blog_id, tag_slug):
    return [p for p in fs_blog_get_all_posts(blog_id)
            if any(slugify(t) == tag_slug for t in p.tags)]


def fs_blog_find_tag_by_slug(blog_id, slug):
    for t in fs_blog_get_all_tags(blog_id):
        if slugify(t) == slug:
            return t
    return None


def fs_blog_get_related_posts(blog_id, post, limit=3):
    if not post.tags:
        return []
    post_slugs = {slugify(t) for t in post.tags}
    candidates = []
    for p in fs_blog_get_all_posts(blog_id):
        if p.id == post.id:
            continue
        overlap = sum(1 for t in p.tags if slugify(t) in post_slugs)
        if overlap:
            candidates.append((overlap, p))
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [p for _, p in candidates[:limit]]


def fs_blog_get_settings(blog_id):
    doc = _blog_col(blog_id, 'settings').document('main').get()
    return SiteSettings(doc.to_dict() if doc.exists else None)


def fs_blog_save_settings(blog_id, data_dict):
    _blog_col(blog_id, 'settings').document('main').set(data_dict)


def fs_blog_get_admin(blog_id):
    doc = _blog_col(blog_id, 'admin').document('credentials').get()
    return doc.to_dict() if doc.exists else None


# ---------------------------------------------------------------------------
# Blog-scoped activity log helpers
# ---------------------------------------------------------------------------

# Action constants
ACT_CREATED  = 'created'
ACT_EDITED   = 'edited'
ACT_DELETED  = 'deleted'
ACT_IMPORTED = 'imported'
ACT_SETTINGS = 'settings'
ACT_EXPORTED = 'exported'

ACTION_META = {
    ACT_CREATED:  {'icon': 'fa-plus-circle',    'color': '#34d399', 'label': 'Created'},
    ACT_EDITED:   {'icon': 'fa-pen',            'color': '#60a5fa', 'label': 'Edited'},
    ACT_DELETED:  {'icon': 'fa-trash-alt',      'color': '#f87171', 'label': 'Deleted'},
    ACT_IMPORTED: {'icon': 'fa-file-import',    'color': '#a78bfa', 'label': 'Imported'},
    ACT_SETTINGS: {'icon': 'fa-sliders-h',      'color': '#fbbf24', 'label': 'Settings'},
    ACT_EXPORTED: {'icon': 'fa-file-export',    'color': '#818cf8', 'label': 'Exported'},
}


def fs_blog_log_activity(blog_id, action, post_title='', post_id='', details=''):
    """Write one activity-log entry. Non-fatal — errors are swallowed."""
    try:
        _blog_col(blog_id, 'activity_log').add({
            'action':      action,
            'post_title':  post_title,
            'post_id':     post_id,
            'details':     details,
            'timestamp':   datetime.utcnow(),
        })
    except Exception as exc:
        logging.warning(f"Could not write activity log for {blog_id}: {exc}")


def fs_blog_get_activity_log(blog_id, limit=100):
    """Return activity entries sorted newest-first."""
    try:
        docs = (
            _blog_col(blog_id, 'activity_log')
            .order_by('timestamp', direction='DESCENDING')
            .limit(limit)
            .stream()
        )
        entries = []
        for doc in docs:
            d = doc.to_dict()
            entries.append({
                'id':         doc.id,
                'action':     d.get('action', ''),
                'post_title': d.get('post_title', ''),
                'post_id':    d.get('post_id', ''),
                'details':    d.get('details', ''),
                'timestamp':  d.get('timestamp'),
                'meta':       ACTION_META.get(d.get('action', ''), ACTION_META[ACT_EDITED]),
            })
        return entries
    except Exception as exc:
        logging.warning(f"Could not read activity log for {blog_id}: {exc}")
        return []


# ---------------------------------------------------------------------------
# Super-admin Firestore helpers
# ---------------------------------------------------------------------------

def fs_get_super_admin():
    db = get_db()
    doc = db.collection('super_admin').document('credentials').get()
    return doc.to_dict() if doc.exists else None


def fs_bootstrap_super_admin():
    db = get_db()
    doc = db.collection('super_admin').document('credentials').get()
    if doc.exists:
        return
    username = os.environ.get('SUPER_ADMIN_USERNAME',
                              os.environ.get('ADMIN_USERNAME', 'superadmin'))
    password = os.environ.get('SUPER_ADMIN_PASSWORD',
                              os.environ.get('ADMIN_PASSWORD', 'superadmin123'))
    db.collection('super_admin').document('credentials').set({
        'username':      username,
        'password_hash': generate_password_hash(password),
    })
    logging.info("Super-admin credentials bootstrapped.")


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

def create_app(config_name='default'):
    app = Flask(__name__)
    config_name = config_name or os.environ.get('FLASK_ENV', 'default')
    config_class = config[config_name]
    if config_name == 'production':
        app.config.from_object(config_class())
    else:
        app.config.from_object(config_class)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
    CSRFProtect(app)
    return app


app = create_app(os.environ.get('FLASK_ENV', 'development'))

# ─── Register otherapps blueprints ────────────────────────────────────────────
try:
    from otherapps import register_blueprints
    register_blueprints(app)
    logging.info("otherapps blueprints registered.")
except Exception as _e:
    logging.warning(f"Could not register otherapps blueprints: {_e}")

try:
    fs_bootstrap_super_admin()
except Exception as e:
    logging.warning(f"Could not bootstrap super-admin credentials: {e}")


# ---------------------------------------------------------------------------
# Auth decorators
# ---------------------------------------------------------------------------

def super_admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('super_admin'):
            return redirect(url_for('super_admin_login'))
        return f(*args, **kwargs)
    return decorated


def blog_admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        blog_id = kwargs.get('blog_id', '')
        if not session.get(f'blog_admin_{blog_id}'):
            return redirect(url_for('blog_admin_login', blog_id=blog_id))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Template filters & context processors
# ---------------------------------------------------------------------------

@app.template_filter('slugify')
def slugify_filter(text):
    return slugify(text)


@app.context_processor
def inject_globals():
    return {'super_admin_logged_in': bool(session.get('super_admin'))}


# ---------------------------------------------------------------------------
# Utility: get blog settings (with fallback)
# ---------------------------------------------------------------------------

def get_blog_settings(blog_id):
    try:
        return fs_blog_get_settings(blog_id)
    except Exception as e:
        logging.warning(f"Could not load settings for blog {blog_id}: {e}")
        return SiteSettings()


def blog_ctx(blog_id):
    """Return common template context variables for a blog route."""
    return {
        'blog_id':              blog_id,
        'site_settings':        get_blog_settings(blog_id),
        'blog_admin_logged_in': bool(session.get(f'blog_admin_{blog_id}')),
    }


# ---------------------------------------------------------------------------
# Root landing page
# ---------------------------------------------------------------------------

@app.route('/')
def index():
    try:
        blogs = fs_list_blogs()
    except Exception as e:
        logging.error(f"Error listing blogs: {e}")
        blogs = []
    return render_template('landing.html', blogs=blogs)


# ---------------------------------------------------------------------------
# Super-admin routes
# ---------------------------------------------------------------------------

@app.route('/super-admin/login', methods=['GET', 'POST'])
def super_admin_login():
    if session.get('super_admin'):
        return redirect(url_for('super_admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        try:
            admin = fs_get_super_admin()
            if admin and username == admin.get('username') and \
                    check_password_hash(admin.get('password_hash', ''), password):
                session['super_admin'] = True
                session.permanent = True
                flash('Welcome, Super Admin!', 'success')
                return redirect(url_for('super_admin_dashboard'))
        except Exception as e:
            logging.error(f"Super-admin login error: {e}")
            flash('Could not verify credentials. Check Firebase configuration.', 'error')
            return render_template('super_admin/login.html')
        flash('Invalid credentials!', 'error')
    return render_template('super_admin/login.html')


@app.route('/super-admin/logout')
@super_admin_required
def super_admin_logout():
    session.pop('super_admin', None)
    flash('Logged out successfully.', 'success')
    return redirect(url_for('index'))


@app.route('/super-admin/dashboard')
@super_admin_required
def super_admin_dashboard():
    try:
        blogs = fs_list_blogs()
    except Exception as e:
        logging.error(f"Error listing blogs: {e}")
        blogs = []
    return render_template('super_admin/dashboard.html', blogs=blogs)


@app.route('/super-admin/change-credentials', methods=['GET', 'POST'])
@super_admin_required
def super_admin_change_credentials():
    if request.method == 'POST':
        action           = request.form.get('action')
        current_password = request.form.get('current_password', '').strip()

        if not current_password:
            flash('Current password is required to make any changes.', 'error')
            return redirect(url_for('super_admin_change_credentials'))

        try:
            admin = fs_get_super_admin()
            if not admin or not check_password_hash(admin.get('password_hash', ''), current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('super_admin_change_credentials'))

            cred_ref = get_db().collection('super_admin').document('credentials')

            if action == 'username':
                new_username = request.form.get('new_username', '').strip()
                if len(new_username) < 3:
                    flash('Username must be at least 3 characters.', 'error')
                    return redirect(url_for('super_admin_change_credentials'))
                cred_ref.update({'username': new_username})
                flash('Super admin username updated successfully!', 'success')

            elif action == 'password':
                new_password     = request.form.get('new_password', '')
                confirm_password = request.form.get('confirm_password', '')
                if new_password != confirm_password:
                    flash('New passwords do not match.', 'error')
                    return redirect(url_for('super_admin_change_credentials'))
                if len(new_password) < 8:
                    flash('New password must be at least 8 characters.', 'error')
                    return redirect(url_for('super_admin_change_credentials'))
                cred_ref.update({'password_hash': generate_password_hash(new_password)})
                flash('Super admin password updated successfully!', 'success')

            return redirect(url_for('super_admin_dashboard'))

        except Exception as e:
            logging.error(f"Error updating super-admin credentials: {e}")
            flash(f'Error updating credentials: {e}', 'error')

    try:
        admin = fs_get_super_admin()
        current_username = admin.get('username', 'superadmin') if admin else 'superadmin'
    except Exception as e:
        logging.warning(f"Could not load super-admin credentials for display: {e}")
        current_username = 'superadmin'

    return render_template('super_admin/change_credentials.html',
                           current_username=current_username)


@app.route('/super-admin/create', methods=['GET', 'POST'])
@super_admin_required
def super_admin_create_blog():
    try:
        blogs = fs_list_blogs()
    except Exception as e:
        logging.warning(f"Could not list blogs for create-blog page: {e}")
        blogs = []

    if request.method == 'POST':
        name           = request.form.get('name', '').strip()
        description    = request.form.get('description', '').strip()
        admin_username = request.form.get('admin_username', 'admin').strip() or 'admin'
        admin_password = request.form.get('admin_password', '').strip()
        clone_from     = request.form.get('clone_from', '').strip() or None

        if not name:
            flash('Blog name is required.', 'error')
            return render_template('super_admin/create_blog.html', blogs=blogs)
        if not admin_password or len(admin_password) < 6:
            flash('Admin password must be at least 6 characters.', 'error')
            return render_template('super_admin/create_blog.html', blogs=blogs)

        try:
            blog_id = fs_create_blog(
                name=name,
                description=description,
                initial_username=admin_username,
                initial_password=admin_password,
                copy_from=clone_from,
            )
            action = 'cloned' if clone_from else 'created'
            flash(f'Blog "{name}" {action} successfully! URL: /{blog_id}/', 'success')
            return redirect(url_for('super_admin_dashboard'))
        except ValueError as e:
            flash(str(e), 'error')
        except Exception as e:
            logging.error(f"Error creating blog: {e}")
            flash(f'Error creating blog: {e}', 'error')

    return render_template('super_admin/create_blog.html', blogs=blogs)


@app.route('/super-admin/delete/<blog_id>', methods=['POST'])
@super_admin_required
def super_admin_delete_blog(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        flash('Blog not found.', 'error')
        return redirect(url_for('super_admin_dashboard'))
    try:
        fs_delete_blog(blog_id)
        flash(f'Blog "{blog.name}" deleted successfully.', 'success')
    except Exception as e:
        logging.error(f"Error deleting blog {blog_id}: {e}")
        flash(f'Error deleting blog: {e}', 'error')
    return redirect(url_for('super_admin_dashboard'))


# ---------------------------------------------------------------------------
# Reserved paths that must NOT be matched as blog IDs
# ---------------------------------------------------------------------------

RESERVED_PATHS = frozenset({
    'super-admin', 'super_admin', 'static', 'offline',
    'favicon.ico', 'manifest.json', 'sw.js', 'robots.txt',
    'sitemap.xml', 'apple-touch-icon.png', 'admin',
})


def is_reserved(blog_id: str) -> bool:
    return blog_id.lower() in RESERVED_PATHS


# ---------------------------------------------------------------------------
# Favicon / static PWA helpers
# ---------------------------------------------------------------------------

@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon') \
        if os.path.exists(os.path.join(app.root_path, 'static', 'favicon.ico')) \
        else ('', 204)


# ---------------------------------------------------------------------------
# Blog public routes
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/')
def blog_index(blog_id):
    if is_reserved(blog_id):
        abort(404)
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    try:
        posts    = fs_blog_get_all_posts(blog_id)
        all_tags = fs_blog_get_all_tags(blog_id)
    except Exception as e:
        logging.error(f"Error loading blog {blog_id}: {e}")
        posts, all_tags = [], []
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'posts': posts, 'all_tags': all_tags})
    return render_template('blog/index.html', **ctx)


@app.route('/<blog_id>/post/<string:post_id>')
def blog_post_detail(blog_id, post_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    post = fs_blog_get_post(blog_id, post_id)
    if post is None:
        abort(404)
    try:
        related = fs_blog_get_related_posts(blog_id, post)
    except Exception as e:
        logging.warning(f"Could not load related posts for {blog_id}/{post_id}: {e}")
        related = []
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'post': post, 'related_posts': related})
    return render_template('blog/post_detail.html', **ctx)


@app.route('/<blog_id>/category/<string:tag>')
def blog_category(blog_id, tag):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    tag_name = fs_blog_find_tag_by_slug(blog_id, tag) or tag.replace('-', ' ').title()
    try:
        posts    = fs_blog_get_posts_by_tag(blog_id, tag)
        all_tags = fs_blog_get_all_tags(blog_id)
    except Exception as e:
        logging.error(f"Error loading category: {e}")
        posts, all_tags = [], []
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'posts': posts, 'tag': tag_name, 'all_tags': all_tags})
    return render_template('blog/category.html', **ctx)


# ---------------------------------------------------------------------------
# Blog admin – authentication
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/admin/login', methods=['GET', 'POST'])
def blog_admin_login(blog_id):
    if session.get(f'blog_admin_{blog_id}'):
        return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))

    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)

    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        try:
            admin = fs_blog_get_admin(blog_id)
            if admin and username == admin.get('username') and \
                    check_password_hash(admin.get('password_hash', ''), password):
                session[f'blog_admin_{blog_id}'] = True
                session.permanent = True
                flash('Logged in successfully!', 'success')
                return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))
        except Exception as e:
            logging.error(f"Blog admin login error: {e}")
            flash('Could not verify credentials.', 'error')
            return render_template('blog/login.html', blog=blog, **blog_ctx(blog_id))
        flash('Invalid credentials!', 'error')

    ctx = blog_ctx(blog_id)
    ctx['blog'] = blog
    return render_template('blog/login.html', **ctx)


@app.route('/<blog_id>/admin/logout')
def blog_admin_logout(blog_id):
    session.pop(f'blog_admin_{blog_id}', None)
    flash('Logged out successfully!', 'success')
    return redirect(url_for('blog_index', blog_id=blog_id))


# ---------------------------------------------------------------------------
# Blog admin – dashboard & post management
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/admin/dashboard')
@blog_admin_required
def blog_admin_dashboard(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    try:
        posts = fs_blog_get_all_posts(blog_id)
    except Exception as e:
        logging.error(f"Error loading posts: {e}")
        posts = []
    recent_activity = fs_blog_get_activity_log(blog_id, limit=8)
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'posts': posts, 'recent_activity': recent_activity})
    return render_template('blog/dashboard.html', **ctx)


@app.route('/<blog_id>/admin/activity-log')
@blog_admin_required
def blog_activity_log(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    activity = fs_blog_get_activity_log(blog_id, limit=100)
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'activity': activity})
    return render_template('blog/activity_log.html', **ctx)


@app.route('/<blog_id>/admin/new', methods=['GET', 'POST'])
@blog_admin_required
def blog_new_post(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)

    if request.method == 'POST':
        title   = request.form.get('title', '').strip()
        content = request.form.get('content', '').strip()
        if not title or not content:
            flash('Title and content are required.', 'error')
            return redirect(url_for('blog_new_post', blog_id=blog_id))
        featured_image = request.form.get('featured_image', '').strip() or None
        tags           = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
        try:
            new_id = fs_blog_create_post(blog_id, title, content, featured_image, tags)
            fs_blog_log_activity(blog_id, ACT_CREATED, post_title=title, post_id=new_id,
                                 details=f'{len(tags)} tag(s)' if tags else '')
            flash('Post created successfully!', 'success')
        except Exception as e:
            logging.error(f"Error creating post: {e}")
            flash(f'Error creating post: {e}', 'error')
        return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))

    try:
        all_tags = fs_blog_get_all_tags(blog_id)
    except Exception as e:
        logging.warning(f"Could not load tags for new_post ({blog_id}): {e}")
        all_tags = []
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'all_tags': all_tags})
    return render_template('blog/new_post.html', **ctx)


@app.route('/<blog_id>/admin/edit/<string:post_id>', methods=['GET', 'POST'])
@blog_admin_required
def blog_edit_post(blog_id, post_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    post = fs_blog_get_post(blog_id, post_id)
    if post is None:
        abort(404)

    if request.method == 'POST':
        new_title = request.form.get('title', '').strip()
        content   = request.form.get('content', '').strip()
        if not new_title or not content:
            flash('Title and content are required.', 'error')
            return redirect(url_for('blog_edit_post', blog_id=blog_id, post_id=post_id))
        tags = [t.strip() for t in request.form.get('tags', '').split(',') if t.strip()]
        try:
            fs_blog_update_post(
                blog_id, post_id,
                new_title,
                content,
                request.form.get('featured_image', '').strip() or None,
                tags,
            )
            fs_blog_log_activity(blog_id, ACT_EDITED, post_title=new_title, post_id=post_id,
                                 details=f'Previously: "{post.title}"' if post.title != new_title else '')
            flash('Post updated successfully!', 'success')
        except Exception as e:
            logging.error(f"Error updating post: {e}")
            flash(f'Error updating post: {e}', 'error')
        return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))

    try:
        all_tags = fs_blog_get_all_tags(blog_id)
    except Exception as e:
        logging.warning(f"Could not load tags for edit_post ({blog_id}/{post_id}): {e}")
        all_tags = []
    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'post': post, 'all_tags': all_tags})
    return render_template('blog/edit_post.html', **ctx)


@app.route('/<blog_id>/admin/delete/<string:post_id>', methods=['POST'])
@blog_admin_required
def blog_delete_post(blog_id, post_id):
    post = fs_blog_get_post(blog_id, post_id)
    saved_title = post.title if post else post_id
    try:
        fs_blog_delete_post(blog_id, post_id)
        fs_blog_log_activity(blog_id, ACT_DELETED, post_title=saved_title, post_id=post_id)
        flash('Post deleted successfully!', 'success')
    except Exception as e:
        logging.error(f"Error deleting post: {e}")
        flash(f'Error deleting post: {e}', 'error')
    return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))


# ---------------------------------------------------------------------------
# Blog admin – settings
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/admin/settings', methods=['GET', 'POST'])
@blog_admin_required
def blog_admin_settings(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)
    settings = get_blog_settings(blog_id)

    if request.method == 'POST':
        new_settings = SiteSettings({
            'blog_title':        request.form.get('blog_title', settings.blog_title),
            'blog_description':  request.form.get('blog_description', settings.blog_description),
            'primary_color':     request.form.get('primary_color', settings.primary_color),
            'secondary_color':   request.form.get('secondary_color', settings.secondary_color),
            'background_color':  request.form.get('background_color', settings.background_color),
            'overall_background':request.form.get('overall_background', settings.overall_background),
            'card_background':   request.form.get('card_background', settings.card_background),
            'text_color':        request.form.get('text_color', settings.text_color),
            'navbar_color':      request.form.get('navbar_color', settings.navbar_color),
            'navbar_text_color': request.form.get('navbar_text_color', settings.navbar_text_color),
            'heading_font':      request.form.get('heading_font', settings.heading_font),
            'body_font':         request.form.get('body_font', settings.body_font),
        })
        try:
            fs_blog_save_settings(blog_id, new_settings.to_dict())
            fs_blog_log_activity(blog_id, ACT_SETTINGS,
                                 details=f'Title: {new_settings.blog_title}')
            flash('Settings updated successfully!', 'success')
        except Exception as e:
            logging.error(f"Error saving settings: {e}")
            flash(f'Error saving settings: {e}', 'error')
        return redirect(url_for('blog_admin_settings', blog_id=blog_id))

    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'settings': settings, 'preset_themes': PRESET_THEMES})
    return render_template('blog/admin_settings.html', **ctx)


@app.route('/<blog_id>/admin/reset-settings', methods=['POST'])
@blog_admin_required
def blog_reset_settings(blog_id):
    try:
        fs_blog_save_settings(blog_id, DEFAULT_SETTINGS)
        fs_blog_log_activity(blog_id, ACT_SETTINGS, details='Reset to default theme')
        flash('Colors reset to default theme.', 'success')
    except Exception as e:
        logging.error(f"Error resetting settings: {e}")
        flash(f'Error resetting settings: {e}', 'error')
    return redirect(url_for('blog_admin_settings', blog_id=blog_id))


# ---------------------------------------------------------------------------
# Blog admin – credentials
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/admin/change-credentials', methods=['GET', 'POST'])
@blog_admin_required
def blog_change_credentials(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)

    if request.method == 'POST':
        action           = request.form.get('action')
        current_password = request.form.get('current_password', '').strip()

        if not current_password:
            flash('Current password is required to make any changes.', 'error')
            return redirect(url_for('blog_change_credentials', blog_id=blog_id))

        try:
            admin = fs_blog_get_admin(blog_id)
            if not admin or not check_password_hash(admin.get('password_hash', ''), current_password):
                flash('Current password is incorrect.', 'error')
                return redirect(url_for('blog_change_credentials', blog_id=blog_id))

            cred_ref = _blog_col(blog_id, 'admin').document('credentials')

            if action == 'username':
                new_username = request.form.get('new_username', '').strip()
                if len(new_username) < 3:
                    flash('Username must be at least 3 characters.', 'error')
                    return redirect(url_for('blog_change_credentials', blog_id=blog_id))
                cred_ref.update({'username': new_username})
                flash('Username updated successfully!', 'success')

            elif action == 'password':
                new_password     = request.form.get('new_password', '')
                confirm_password = request.form.get('confirm_password', '')
                if new_password != confirm_password:
                    flash('New passwords do not match.', 'error')
                    return redirect(url_for('blog_change_credentials', blog_id=blog_id))
                if len(new_password) < 8:
                    flash('New password must be at least 8 characters.', 'error')
                    return redirect(url_for('blog_change_credentials', blog_id=blog_id))
                cred_ref.update({'password_hash': generate_password_hash(new_password)})
                flash('Password updated successfully!', 'success')

            return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))

        except Exception as e:
            logging.error(f"Error updating credentials: {e}")
            flash(f'Error updating credentials: {e}', 'error')

    try:
        admin = fs_blog_get_admin(blog_id)
        current_username = admin.get('username', 'admin') if admin else 'admin'
    except Exception as e:
        logging.warning(f"Could not load blog admin credentials for display ({blog_id}): {e}")
        current_username = 'admin'

    ctx = blog_ctx(blog_id)
    ctx.update({'blog': blog, 'current_username': current_username})
    return render_template('blog/change_password.html', **ctx)


# ---------------------------------------------------------------------------
# Blog admin – export / import
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/admin/export')
@blog_admin_required
def blog_export(blog_id):
    try:
        posts = fs_blog_get_all_posts(blog_id)
    except Exception as e:
        logging.error(f"Error exporting posts: {e}")
        flash(f'Error exporting: {e}', 'error')
        return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))

    data = {
        'export_date': datetime.now().isoformat(),
        'blog_id':     blog_id,
        'total_posts': len(posts),
        'posts': [
            {
                'title':          p.title,
                'content':        p.content,
                'featured_image': p.featured_image,
                'tags':           p.tags,
                'created_at':     p.created_at.isoformat() if p.created_at else None,
            }
            for p in posts
        ],
    }
    fs_blog_log_activity(blog_id, ACT_EXPORTED,
                         details=f'{len(posts)} post(s) exported')
    return Response(
        json.dumps(data, indent=2, ensure_ascii=False),
        mimetype='application/json',
        headers={
            'Content-Disposition': (
                f'attachment; filename={blog_id}_export_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            )
        },
    )


@app.route('/<blog_id>/admin/import', methods=['GET', 'POST'])
@blog_admin_required
def blog_import(blog_id):
    blog = fs_get_blog(blog_id)
    if not blog:
        abort(404)

    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file selected!', 'error')
            return redirect(request.url)
        file = request.files['file']
        if not file.filename:
            flash('No file selected!', 'error')
            return redirect(request.url)
        if not file.filename.lower().endswith('.json'):
            flash('Please upload a JSON file!', 'error')
            return redirect(request.url)
        try:
            data = json.loads(file.read().decode('utf-8'))
            if 'posts' not in data:
                flash('Invalid file format! Missing posts data.', 'error')
                return redirect(request.url)
            existing_titles = {p.title for p in fs_blog_get_all_posts(blog_id)}
            imported_count = skipped_count = 0
            for post_data in data['posts']:
                if not post_data.get('title') or not post_data.get('content'):
                    skipped_count += 1
                    continue
                if post_data['title'] in existing_titles:
                    skipped_count += 1
                    continue
                created_at = datetime.utcnow()
                if post_data.get('created_at'):
                    try:
                        created_at = datetime.fromisoformat(
                            post_data['created_at'].replace('Z', '+00:00'))
                    except Exception as e:
                        logging.warning(f"Could not parse created_at '{post_data['created_at']}': {e}")
                _blog_col(blog_id, 'posts').add({
                    'title':          post_data['title'],
                    'content':        post_data['content'],
                    'featured_image': post_data.get('featured_image'),
                    'tags':           post_data.get('tags', []),
                    'created_at':     created_at,
                })
                existing_titles.add(post_data['title'])
                imported_count += 1
            if imported_count > 0:
                fs_blog_log_activity(blog_id, ACT_IMPORTED,
                                     details=f'{imported_count} post(s) imported, {skipped_count} skipped')
                flash(f'Successfully imported {imported_count} posts! Skipped {skipped_count} duplicates.', 'success')
            else:
                flash(f'No new posts imported. Skipped {skipped_count} duplicates or invalid entries.', 'info')
            return redirect(url_for('blog_admin_dashboard', blog_id=blog_id))
        except json.JSONDecodeError:
            flash('Invalid JSON file format!', 'error')
            return redirect(request.url)
        except Exception as e:
            flash(f'Error importing posts: {e}', 'error')
            return redirect(request.url)

    ctx = blog_ctx(blog_id)
    ctx['blog'] = blog
    return render_template('blog/import_tutorials.html', **ctx)


# ---------------------------------------------------------------------------
# Blog – certificate download
# ---------------------------------------------------------------------------

@app.route('/<blog_id>/certificate/<string:post_id>/<path:student_name>')
def blog_download_certificate(blog_id, post_id, student_name):
    if is_reserved(blog_id):
        abort(404)
    post = fs_blog_get_post(blog_id, post_id)
    if post is None:
        abort(404)
    s = get_blog_settings(blog_id)
    completion_date = datetime.now().strftime('%B %d, %Y')
    certificate_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Certificate – {student_name}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body {{ font-family:'Times New Roman',serif; background:linear-gradient(135deg,{s.background_color} 0%,{s.secondary_color} 100%); min-height:100vh; padding:20px; }}
        .certificate {{ max-width:800px; margin:50px auto; background:#fff; padding:60px; border:8px solid {s.primary_color}; position:relative; box-shadow:0 20px 40px rgba(0,0,0,.1); }}
        .certificate::before {{ content:''; position:absolute; top:20px;left:20px;right:20px;bottom:20px; border:3px solid {s.secondary_color}; pointer-events:none; }}
        .cert-title {{ font-size:3rem; font-weight:bold; color:{s.primary_color}; margin-bottom:10px; letter-spacing:3px; }}
        .cert-subtitle {{ font-size:1.2rem; color:{s.secondary_color}; margin-bottom:30px; }}
        .student-name {{ font-size:2.5rem; font-weight:bold; color:{s.secondary_color}; text-decoration:underline; text-decoration-color:{s.primary_color}; margin:20px 0; }}
        .tutorial-title {{ font-size:1.8rem; font-style:italic; color:{s.primary_color}; margin:20px 0; }}
        .completion-text {{ font-size:1.1rem; line-height:1.8; text-align:center; margin:30px 0; }}
        .date-signature {{ display:flex; justify-content:space-between; margin-top:60px; }}
        .signature-line {{ text-align:center; min-width:200px; }}
        .signature-line hr {{ border:2px solid {s.primary_color}; margin:10px 0; }}
        .signature-line small {{ color:{s.secondary_color}; font-size:.9rem; }}
    </style>
</head>
<body>
    <div class="certificate">
        <div class="text-center" style="margin-bottom:40px;">
            <h1 class="cert-title">CERTIFICATE</h1>
            <h2 class="cert-subtitle">of Completion</h2>
        </div>
        <div class="text-center">
            <p class="completion-text">This is to certify that</p>
            <h2 class="student-name">{student_name}</h2>
            <p class="completion-text">has successfully completed the tutorial</p>
            <h3 class="tutorial-title">"{post.title}"</h3>
            <p class="completion-text">on this day of <strong>{completion_date}</strong></p>
        </div>
        <div class="date-signature">
            <div class="signature-line"><hr><small>Date</small></div>
            <div class="signature-line"><hr><small>{s.blog_title}</small></div>
        </div>
    </div>
</body>
</html>"""
    resp = Response(certificate_html, mimetype='text/html')
    resp.headers['Content-Disposition'] = (
        f'attachment; filename="certificate-{student_name.replace(" ","-").lower()}.html"'
    )
    return resp


# ---------------------------------------------------------------------------
# Blog – dynamic CSS
# ---------------------------------------------------------------------------

def _font_stack(name):
    fallbacks = {
        'serif':     'Georgia, "Times New Roman", serif',
        'monospace': '"Courier New", Courier, monospace',
    }
    cat = next((f['category'] for f in AVAILABLE_FONTS if f['name'] == name), 'sans-serif')
    fb = fallbacks.get(cat, '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif')
    return f'"{name}", {fb}'


def _build_dynamic_css(s: SiteSettings) -> str:
    google_fonts = {f['name'] for f in AVAILABLE_FONTS if f['google']}
    fonts_to_load = {f for f in [s.heading_font, s.body_font] if f in google_fonts}
    if fonts_to_load:
        family_params = '&family='.join(
            f.replace(' ', '+') + ':ital,wght@0,400;0,600;0,700;0,800;1,400'
            for f in sorted(fonts_to_load)
        )
        font_import = f"@import url('https://fonts.googleapis.com/css2?family={family_params}&display=swap');\n"
    else:
        font_import = ''

    return f"""{font_import}
:root {{
    --clr-primary:       {s.primary_color};
    --clr-secondary:     {s.secondary_color};
    --clr-bg-start:      {s.background_color};
    --clr-overall-bg:    {s.overall_background};
    --clr-card:          {s.card_background};
    --clr-text:          {s.text_color};
    --clr-navbar:        {s.navbar_color};
    --clr-nav-text:      {s.navbar_text_color};
    --clr-primary-rgb:   {hex_to_rgb(s.primary_color)};
    --clr-secondary-rgb: {hex_to_rgb(s.secondary_color)};
    --clr-card-rgb:      {hex_to_rgb(s.card_background)};
    --clr-navbar-rgb:    {hex_to_rgb(s.navbar_color)};
    --font-heading:      {_font_stack(s.heading_font)};
    --font-body:         {_font_stack(s.body_font)};
}}
body, p, .card-text, .post-content, .form-control, small, .small, .nav-item span, .btn {{
    font-family: var(--font-body) !important;
}}
h1, h2, h3, h4, h5, h6, .card-title, .app-title, .blog-card .card-title,
.hero-slide-title, .category-title, .navbar-brand {{
    font-family: var(--font-heading) !important;
}}
body.mobile-app-body {{
    background: linear-gradient(135deg, var(--clr-bg-start) 0%, var(--clr-secondary) 100%) !important;
    background-attachment: fixed !important;
}}
.btn-gradient {{
    background: linear-gradient(45deg, var(--clr-primary), var(--clr-secondary)) !important;
    position: relative; overflow: hidden;
}}
.btn-gradient::before {{
    content: ''; position: absolute; top: 0; left: -100%;
    width: 100%; height: 100%;
    background: linear-gradient(90deg, transparent, rgba(255,255,255,0.2), transparent);
    transition: left 0.6s ease;
}}
.btn-gradient:hover::before {{ left: 100%; }}
.btn-gradient:hover {{
    background: linear-gradient(45deg, var(--clr-secondary), var(--clr-primary)) !important;
    transform: translateY(-2px) scale(1.02);
    box-shadow: 0 10px 25px rgba(var(--clr-primary-rgb), 0.35) !important;
}}
@keyframes ripple {{ to {{ width: 300px; height: 300px; opacity: 0; }} }}
.blog-card {{
    background: rgba(var(--clr-card-rgb), 0.95) !important;
    backdrop-filter: blur(10px);
}}
.blog-card:hover {{
    box-shadow: 0 20px 40px rgba(var(--clr-primary-rgb), 0.2) !important;
}}
.navbar-dark {{
    background: rgba(var(--clr-navbar-rgb), 0.92) !important;
    backdrop-filter: blur(20px);
}}
.navbar-dark .nav-link:hover,
.navbar-dark .nav-link.active {{
    background: rgba(var(--clr-primary-rgb), 0.2) !important;
}}
.mobile-header {{
    background: rgba(var(--clr-card-rgb), 0.97) !important;
    backdrop-filter: blur(20px);
}}
.bottom-nav {{
    background: rgba(var(--clr-card-rgb), 0.97) !important;
    backdrop-filter: blur(20px);
}}
.nav-item:hover {{
    color: var(--clr-primary) !important;
    background: rgba(var(--clr-primary-rgb), 0.1) !important;
}}
.nav-item.active {{
    background: linear-gradient(45deg, var(--clr-primary), var(--clr-secondary)) !important;
    color: white !important;
    border-radius: 15px;
    box-shadow: 0 8px 20px rgba(var(--clr-primary-rgb), 0.3) !important;
}}
.nav-item::after {{ background: var(--clr-primary) !important; }}
.app-title {{
    background: linear-gradient(45deg, var(--clr-primary), var(--clr-secondary), var(--clr-primary)) !important;
    background-size: 200% 200% !important;
    -webkit-background-clip: text !important;
    -webkit-text-fill-color: transparent !important;
    background-clip: text !important;
}}
.form-control:focus {{
    border-color: var(--clr-primary) !important;
    box-shadow: 0 0 0 0.2rem rgba(var(--clr-primary-rgb), 0.25) !important;
}}
.tags-input-wrap {{ border-color: rgba(var(--clr-primary-rgb), 0.3) !important; }}
.tags-input-wrap:focus-within {{
    border-color: var(--clr-primary) !important;
    box-shadow: 0 0 0 3px rgba(var(--clr-primary-rgb), .2) !important;
}}
.tag-chip {{
    background: rgba(var(--clr-primary-rgb), 0.18) !important;
    color: var(--clr-primary) !important;
    border-color: rgba(var(--clr-primary-rgb), 0.3) !important;
}}
.tag-chip:hover {{ background: rgba(var(--clr-primary-rgb), 0.35) !important; }}
.tag-chip-active {{
    background: linear-gradient(135deg, var(--clr-primary), var(--clr-secondary)) !important;
    color: #fff !important; border-color: transparent !important;
}}
.tag-chip-suggest {{
    background: rgba(var(--clr-secondary-rgb), 0.15) !important;
    border-color: rgba(var(--clr-secondary-rgb), 0.3) !important;
    color: var(--clr-secondary) !important;
}}
.tag-chip-suggest:hover {{ background: rgba(var(--clr-secondary-rgb), 0.3) !important; }}
.tag-chip-input {{
    background: linear-gradient(135deg, rgba(var(--clr-primary-rgb), .3), rgba(var(--clr-secondary-rgb), .3)) !important;
    border-color: rgba(var(--clr-secondary-rgb), .4) !important;
}}
.category-pill.active {{
    background: linear-gradient(135deg, var(--clr-primary), var(--clr-secondary)) !important;
    box-shadow: 0 6px 20px rgba(var(--clr-primary-rgb), 0.4) !important;
}}
.card-img-placeholder {{
    background: linear-gradient(135deg, rgba(var(--clr-primary-rgb), .25), rgba(var(--clr-secondary-rgb), .25)) !important;
    color: rgba(var(--clr-primary-rgb), .7) !important;
}}
.category-hero {{
    background: linear-gradient(135deg, rgba(var(--clr-primary-rgb), .3), rgba(var(--clr-secondary-rgb), .2)) !important;
}}
.category-badge-lg {{
    background: linear-gradient(135deg, var(--clr-primary), var(--clr-secondary)) !important;
    box-shadow: 0 8px 24px rgba(var(--clr-primary-rgb), .4) !important;
}}
.hero-slide-branded-bg {{
    background: linear-gradient(135deg, var(--clr-primary) 0%, var(--clr-secondary) 100%) !important;
}}
.splash-screen {{
    background: linear-gradient(135deg, var(--clr-primary) 0%, var(--clr-secondary) 100%) !important;
}}
.certificate {{ border: 3px solid var(--clr-primary) !important; }}
.certificate:hover {{ box-shadow: 0 15px 35px rgba(var(--clr-primary-rgb), 0.2) !important; }}
.certificate::before {{ border: 2px solid var(--clr-secondary) !important; }}
.certificate-header h2 {{ color: var(--clr-primary) !important; }}
.student-name {{ color: var(--clr-secondary) !important; text-decoration-color: var(--clr-primary) !important; }}
.tutorial-title {{ color: var(--clr-primary) !important; }}
.signature-line hr {{ border-color: var(--clr-primary) !important; }}
.signature-line small {{ color: var(--clr-secondary) !important; }}
/* ── Text colour: all public content elements ───────────────── */
.blog-card .card-title,
.blog-card h1, .blog-card h2, .blog-card h3,
.blog-card h4, .blog-card h5, .blog-card h6 {{
    color: var(--clr-text) !important;
}}
.blog-card .card-text,
.blog-card .card-body > p,
.blog-card .card-body > small,
.blog-card .card-body > ul,
.blog-card .card-body > ol,
.blog-card .card-body > li {{
    color: var(--clr-text) !important;
}}
.post-content {{ color: var(--clr-text); line-height: 1.8; }}
.post-content p, .post-content li, .post-content blockquote,
.post-content td, .post-content th,
.post-content h1, .post-content h2, .post-content h3,
.post-content h4, .post-content h5, .post-content h6 {{
    color: var(--clr-text) !important;
}}
.related-posts-heading {{ color: var(--clr-text) !important; }}
#readingProgress {{
    background: linear-gradient(90deg, var(--clr-primary), var(--clr-secondary), var(--clr-primary)) !important;
}}
.stat-card:hover {{ border-color: rgba(var(--clr-primary-rgb), 0.35) !important; }}
.stat-icon {{ color: var(--clr-primary) !important; }}
.btn-edit {{ background: rgba(var(--clr-primary-rgb), 0.2) !important; color: var(--clr-primary) !important; }}
.btn-edit:hover {{ background: rgba(var(--clr-primary-rgb), 0.4) !important; color: #fff !important; }}
.login-avatar {{
    background: linear-gradient(135deg, var(--clr-primary), var(--clr-secondary)) !important;
    box-shadow: 0 12px 30px rgba(var(--clr-primary-rgb), .4) !important;
}}
::-webkit-scrollbar-thumb {{
    background: linear-gradient(var(--clr-primary), var(--clr-secondary));
    border-radius: 4px;
}}
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: rgba(var(--clr-primary-rgb), .05); }}
"""


@app.route('/<blog_id>/dynamic-styles.css')
def blog_dynamic_styles(blog_id):
    if is_reserved(blog_id):
        abort(404)
    s = get_blog_settings(blog_id)
    resp = Response(_build_dynamic_css(s), mimetype='text/css')
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp


# ---------------------------------------------------------------------------
# Static / PWA routes (global)
# ---------------------------------------------------------------------------

@app.route('/offline')
def offline():
    return render_template('offline.html')


@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/manifest+json')


@app.route('/sw.js')
def serve_sw():
    resp = send_from_directory('static', 'sw.js', mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


# ---------------------------------------------------------------------------
# Per-blog PWA – icon generation helpers
# ---------------------------------------------------------------------------

_ICON_FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
_ICON_CACHE: dict = {}


def _hex_to_rgb(hex_color: str) -> tuple:
    h = hex_color.lstrip('#')
    if len(h) == 3:
        h = h[0] * 2 + h[1] * 2 + h[2] * 2
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _make_blog_icon_png(size: int, primary: str, secondary: str,
                        initials: str, maskable: bool = False) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    pr = _hex_to_rgb(primary)
    sr = _hex_to_rgb(secondary)

    # ── Base diagonal gradient ──────────────────────────────────────────────
    img = Image.new('RGB', (size, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        r = int(pr[0] + (sr[0] - pr[0]) * t)
        g = int(pr[1] + (sr[1] - pr[1]) * t)
        b = int(pr[2] + (sr[2] - pr[2]) * t)
        ImageDraw.Draw(img).line([(0, y), (size - 1, y)], fill=(r, g, b))

    img = img.convert('RGBA')

    # ── Decorative accent circles ────────────────────────────────────────────
    for cx_f, cy_f, r_f, alpha in [
        (0.78, 0.22, 0.55, 55),
        (0.10, 0.88, 0.30, 40),
        (0.50, 0.50, 0.18, 25),
    ]:
        ov = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        cx, cy, cr = int(cx_f * size), int(cy_f * size), int(r_f * size)
        od.ellipse(
            [cx - cr, cy - cr, cx + cr, cy + cr],
            fill=(sr[0], sr[1], sr[2], alpha),
        )
        img = Image.alpha_composite(img, ov)

    draw = ImageDraw.Draw(img)

    # ── Maskable safe-zone: text lives in central 80 % ───────────────────────
    pad = int(size * 0.10) if maskable else 0
    content = size - 2 * pad

    # ── Initials text ────────────────────────────────────────────────────────
    text = (initials[:2] if len(initials) >= 2 else initials[:1] or '?').upper()
    font_sz = int(content * 0.46)
    try:
        font = ImageFont.truetype(_ICON_FONT, font_sz)
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = pad + (content - tw) / 2 - bbox[0]
    ty = pad + (content - th) / 2 - bbox[1]

    # Drop shadow
    draw.text((tx + max(2, size // 80), ty + max(2, size // 80)),
              text, font=font, fill=(0, 0, 0, 90))
    # White text
    draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 245))

    buf = io.BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    buf.seek(0)
    return buf.getvalue()


def _get_blog_icon(blog_id: str, size: int, maskable: bool = False) -> bytes:
    s = get_blog_settings(blog_id)
    initials = ''.join(w[0] for w in s.blog_title.split() if w)[:2] or blog_id[:2].upper()
    cache_key = (blog_id, size, maskable,
                 s.primary_color, s.secondary_color, initials)
    if cache_key not in _ICON_CACHE:
        _ICON_CACHE[cache_key] = _make_blog_icon_png(
            size, s.primary_color, s.secondary_color,
            initials, maskable=maskable,
        )
    return _ICON_CACHE[cache_key]


# ---------------------------------------------------------------------------
# Per-blog PWA routes
# ---------------------------------------------------------------------------

_ICON_SIZES = [72, 96, 128, 144, 152, 192, 384, 512]
_ICON_RE = re.compile(r'^(icon|maskable)-(\d+)\.png$')


@app.route('/<blog_id>/icons/<path:filename>')
def blog_pwa_icon(blog_id, filename):
    if is_reserved(blog_id):
        abort(404)
    m = _ICON_RE.match(filename)
    if not m:
        abort(404)
    kind, size_str = m.group(1), m.group(2)
    size = int(size_str)
    if size not in _ICON_SIZES:
        abort(404)
    maskable = (kind == 'maskable')
    try:
        png_bytes = _get_blog_icon(blog_id, size, maskable=maskable)
    except Exception as e:
        logging.error(f'Icon generation error for {blog_id}/{filename}: {e}')
        abort(500)
    resp = Response(png_bytes, mimetype='image/png')
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    return resp


@app.route('/<blog_id>/manifest.json')
def blog_pwa_manifest(blog_id):
    if is_reserved(blog_id):
        abort(404)
    try:
        if not fs_get_blog(blog_id):
            abort(404)
    except Exception as e:
        logging.warning(f"Could not verify blog {blog_id} for manifest: {e}")
    s = get_blog_settings(blog_id)
    short_name = s.blog_title[:12] if len(s.blog_title) > 12 else s.blog_title

    icons = []
    for sz in _ICON_SIZES:
        icons.append({
            'src': f'/{blog_id}/icons/icon-{sz}.png',
            'sizes': f'{sz}x{sz}',
            'type': 'image/png',
            'purpose': 'any',
        })
    for sz in [192, 512]:
        icons.append({
            'src': f'/{blog_id}/icons/maskable-{sz}.png',
            'sizes': f'{sz}x{sz}',
            'type': 'image/png',
            'purpose': 'maskable',
        })

    manifest = {
        'name': s.blog_title,
        'short_name': short_name,
        'description': s.blog_description or s.blog_title,
        'start_url': f'/{blog_id}/',
        'scope': f'/{blog_id}/',
        'display': 'standalone',
        'orientation': 'any',
        'theme_color': s.primary_color,
        'background_color': s.background_color,
        'lang': 'en',
        'categories': ['education', 'productivity'],
        'icons': icons,
        'shortcuts': [
            {
                'name': 'Latest Posts',
                'url': f'/{blog_id}/',
                'icons': [{'src': f'/{blog_id}/icons/icon-96.png', 'sizes': '96x96'}],
            }
        ],
    }
    resp = Response(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        mimetype='application/manifest+json',
    )
    resp.headers['Cache-Control'] = 'public, max-age=300'
    return resp


@app.route('/<blog_id>/sw.js')
def blog_pwa_sw(blog_id):
    if is_reserved(blog_id):
        abort(404)
    try:
        if not fs_get_blog(blog_id):
            abort(404)
    except Exception as e:
        logging.warning(f"Could not verify blog {blog_id} for service worker: {e}")
    s = get_blog_settings(blog_id)
    version = hashlib.md5(
        f'{s.primary_color}{s.secondary_color}{s.blog_title}'.encode()
    ).hexdigest()[:8]

    sw_js = f"""/* Auto-generated service worker for /{blog_id}/ */
const BLOG_ID = '{blog_id}';
const CACHE_VER = '{version}';
const CACHE_NAME = 'blog-' + BLOG_ID + '-v' + CACHE_VER;
const OFFLINE_URL = '/offline';

const PRECACHE = [
  '/{blog_id}/',
  '/offline',
  '/{blog_id}/icons/icon-192.png',
  '/{blog_id}/icons/icon-512.png',
  '/{blog_id}/manifest.json',
];

self.addEventListener('install', e => {{
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(c => c.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
}});

self.addEventListener('activate', e => {{
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('blog-' + BLOG_ID + '-') && k !== CACHE_NAME)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
}});

self.addEventListener('fetch', e => {{
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.includes('/admin/')) return;
  if (url.pathname.includes('/dynamic-styles.css')) return;

  if (req.mode === 'navigate') {{
    e.respondWith(
      fetch(req)
        .then(res => {{
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(req, clone));
          return res;
        }})
        .catch(() =>
          caches.match(req).then(r => r || caches.match(OFFLINE_URL))
        )
    );
    return;
  }}

  e.respondWith(
    caches.match(req).then(cached => {{
      if (cached) return cached;
      return fetch(req).then(res => {{
        if (res && res.ok) {{
          const clone = res.clone();
          caches.open(CACHE_NAME).then(c => c.put(req, clone));
        }}
        return res;
      }});
    }})
  );
}});
"""
    resp = Response(sw_js, mimetype='application/javascript')
    resp.headers['Service-Worker-Allowed'] = f'/{blog_id}/'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)