"""AdGuard DNS Reports: Flask application factory."""
from datetime import timedelta
from flask import Flask
from .config import config

def create_app():
    app = Flask(__name__)
    app.secret_key = config()["session_secret"]
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=True, PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
                      MAX_CONTENT_LENGTH=100000)
    from .security import security_headers
    from .auth import bp as auth_bp, auth_gate
    from .reports import bp as reports_bp
    app.before_request(auth_gate)
    app.after_request(security_headers)
    app.register_blueprint(auth_bp)
    app.register_blueprint(reports_bp)
    return app
