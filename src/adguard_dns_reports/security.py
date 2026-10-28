"""Headers, session CSRF tokens, password hashing."""
import hashlib
import hmac
import secrets
from flask import request, session, abort
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
    response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response


def token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(24)
    return session['csrf']


def require_csrf():
    expected = session.get('csrf')
    supplied = request.form.get('csrf', '')
    if not expected or not supplied or not hmac.compare_digest(supplied, expected):
        abort(403)


def verify_password(password, saved):
    try:
        scheme, rounds, salt, hexhash = saved.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(hexhash))
    except (ValueError, TypeError):
        return False

def password_hash(password):
    salt = secrets.token_bytes(16)
    rounds = 380000
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
    return f'pbkdf2_sha256${rounds}${salt.hex()}${derived.hex()}'

