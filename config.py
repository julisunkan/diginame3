import os
from datetime import timedelta


class Config:
    SECRET_KEY = os.environ.get('SESSION_SECRET') or 'dev-secret-key-change-in-production'
    PERMANENT_SESSION_LIFETIME = timedelta(hours=24)
    MAX_CONTENT_LENGTH = 25 * 1024 * 1024  # 25 MB – supports audio uploads in meetingsummarizer
    WTF_CSRF_ENABLED = True


class DevelopmentConfig(Config):
    DEBUG = True
    FLASK_ENV = 'development'


class ProductionConfig(Config):
    DEBUG = False
    FLASK_ENV = 'production'
    PREFERRED_URL_SCHEME = 'https'
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'

    def __init__(self):
        if not os.environ.get('SESSION_SECRET'):
            raise ValueError("SESSION_SECRET must be set in production.")
        self.SECRET_KEY = os.environ['SESSION_SECRET']

        if not os.environ.get('FIREBASE_SERVICE_ACCOUNT'):
            raise ValueError("FIREBASE_SERVICE_ACCOUNT must be set in production.")


config = {
    'development': DevelopmentConfig,
    'production': ProductionConfig,
    'default': DevelopmentConfig,
}
