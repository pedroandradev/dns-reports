"""Sign-in and admin-only user management routes."""
import re
import sqlite3
import time
from flask import Blueprint, abort, g, redirect, render_template, request, session, url_for
from .database import db_connect, create_schema
from .security import token, require_csrf, verify_password, password_hash
bp = Blueprint("auth", __name__)
ATTEMPTS = {}
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
def auth_gate():
    if request.path.startswith('/static/') or request.path == '/login':
        return
    uid = session.get('uid')
    if not uid:
        return redirect(url_for('auth.login'))
    c = db_connect()
    try:
        create_schema(c)
        user = c.execute('SELECT id,username,session_version,is_admin FROM users WHERE id=?', (uid,)).fetchone()
    finally:
        c.close()
    if not user or user['session_version'] != session.get('session_version'):
        session.clear()
        return redirect(url_for('auth.login'))
    # Role is read from the database for every request, not trusted from session or UI.
    g.is_admin = bool(user['is_admin'])
    if request.path == '/usuarios' and not g.is_admin:
        abort(403)

@bp.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('uid'):
        return redirect('/')
    error = ''
    if request.method == 'POST':
        require_csrf()
        ip = request.remote_addr or 'unknown'
        history = [ts for ts in ATTEMPTS.get(ip, []) if ts > time.time() - 600]
        if len(history) >= 8:
            error = 'Muitas tentativas. Aguarde 10 minutos.'
        else:
            username = request.form.get('username', '')[:40]
            c = db_connect()
            try:
                create_schema(c)
                user = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            finally:
                c.close()
            if user and verify_password(request.form.get('password', ''), user['password_hash']):
                ATTEMPTS.pop(ip, None)
                session.clear()
                session['uid'] = user['id']
                session['username'] = user['username']
                session['session_version'] = user['session_version']
                session.permanent = True
                token()
                return redirect('/')
            history.append(time.time())
            ATTEMPTS[ip] = history
            error = 'Usuário ou senha incorretos.'
    return render_template('login.html', error=error, csrf=token())


@bp.route('/usuarios', methods=['GET', 'POST'])
def usuarios():
    # Also enforce at the endpoint (GET and POST), including direct URL/API calls.
    if not getattr(g, 'is_admin', False):
        abort(403)
    error, success = '', ''
    conn = db_connect()
    create_schema(conn)
    try:
        if request.method == 'POST':
            require_csrf()
            action = request.form.get('action', '')
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            if action == 'add':
                if not USERNAME_RE.fullmatch(username):
                    error = 'Usuário inválido. Use 3 a 40 letras, números, pontos, hífens ou _. '
                elif len(password) < 10 or len(password) > 256:
                    error = 'A senha deve ter entre 10 e 256 caracteres.'
                else:
                    try:
                        conn.execute('INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,0)',
                                     (username,password_hash(password)))
                        conn.commit()
                        success = f'Usuário {username} criado.'
                    except sqlite3.IntegrityError:
                        error = 'Esse usuário já está cadastrado.'
            elif action in ('reset', 'delete'):
                try:
                    uid = int(request.form.get('user_id', ''))
                except ValueError:
                    uid = -1
                user = conn.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
                if user is None:
                    error = 'Usuário não encontrado.'
                elif action == 'delete':
                    if user['is_admin']:
                        error = 'A conta administradora não pode ser excluída.'
                    elif uid == session['uid']:
                        error = 'Não é possível excluir seu próprio usuário.'
                    elif conn.execute('SELECT COUNT(*) FROM users').fetchone()[0] <= 1:
                        error = 'É necessário manter pelo menos um usuário.'
                    else:
                        conn.execute('DELETE FROM users WHERE id=?', (uid,))
                        conn.commit()
                        success = f'Usuário {user["username"]} excluído.'
                elif len(password) < 10 or len(password) > 256:
                    error = 'A nova senha deve ter entre 10 e 256 caracteres.'
                else:
                    conn.execute('UPDATE users SET password_hash=?,session_version=session_version+1 WHERE id=?',
                                 (password_hash(password),uid))
                    conn.commit()
                    success = f'Senha de {user["username"]} atualizada.'
                    if uid == session['uid']:
                        session.clear()
                        return redirect('/login')
            else:
                error = 'Operação inválida.'
        users = conn.execute('SELECT id,username,is_admin,created_at FROM users ORDER BY is_admin DESC,username').fetchall()
    finally:
        conn.close()
    return render_template('users.html', users=users, error=error, success=success,
                           csrf=token(), username=session.get('username',''))


@bp.post('/logout')
def logout():
    require_csrf()
    session.clear()
    return redirect('/login')

