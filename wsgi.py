"""
WSGI entry point for production deployment (Render, Gunicorn, etc.)
"""
from app import app as application

if __name__ == '__main__':
    application.run()
