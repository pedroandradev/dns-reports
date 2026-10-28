#!/usr/bin/env bash
# AdGuard DNS Reports - instalador generico para Proxmox VE 8/9 e LXC Debian 13
# Installer created from the user's existing DNS Reports project.
# Instalar: bash <(curl -fsSL URL_RAW)   (prefer local review before executing)
# Atualizar sem perder registros: bash instalador.sh --update ID_DO_CONTAINER
set -Eeuo pipefail
umask 077
export LC_ALL=C
GREEN='\033[0;32m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; RESET='\033[0m'
info(){ printf '%b  ⚙️  %s%b\n' "$BLUE" "$*" "$RESET"; }
ok(){ printf '%b  ✔️  %s%b\n' "$GREEN" "$*" "$RESET"; }
warn(){ printf '%b  ⚠️  %s%b\n' "$YELLOW" "$*" "$RESET" >&2; }
fail(){ printf '%b  ✖  %s%b\n' "$RED" "$*" "$RESET" >&2; exit 1; }
errline(){ warn "Erro na linha $1. O container nao sera apagado; confira os logs e use --update ID se necessario."; }
trap 'errline "$LINENO"' ERR
[[ $EUID -eq 0 ]] || fail 'Execute como root no shell do Proxmox.'
for cmd in pct pvesh pveam pvesm python3 openssl curl; do command -v "$cmd" >/dev/null || fail "Ausente no Proxmox: $cmd"; done
printf '\n%b' "$CYAN"
cat <<'HEADER'
    _       _  ____                     _
   / \   __| |/ ___|_   _  __ _ _ __ __| |
  / _ \ / _` | |  _| | | |/ _` | '__/ _` |
 / ___ \ (_| | |_| | |_| | (_| | | | (_| |
/_/   \_\__,_|\____|\__,_|\__,_|_|  \__,_|

           DNS REPORTS
  Consultas DNS | Relatorios | Usuarios
HEADER
printf '%b\n' "$RESET"
printf '%b  Painel DNS | HTTPS | CSV / Excel / PDF%b\n\n' "$BLUE" "$RESET"
MODE=install
if [[ ${1:-} == --update || ${1:-} == --upgrade ]]; then
  MODE=update
  CTID="${2:-}"
  [[ "$CTID" =~ ^[0-9]+$ ]] || fail 'Informe o CTID: bash instalador.sh --update ID_DO_CONTAINER'
elif [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Instalar: bash instalador.sh\nAtualizar painel existente: bash instalador.sh --update ID_DO_CONTAINER\n'
  exit 0
elif [[ $# -gt 0 ]]; then
  fail 'Argumento desconhecido. Use --help.'
fi
# Padroes do Proxmox; outras infraestruturas podem sobrescrever com variaveis de ambiente.
STORAGE="${REPORTS_STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${REPORTS_TEMPLATE_STORAGE:-local}"
BRIDGE="${REPORTS_BRIDGE:-vmbr0}"
TZ_NAME="${REPORTS_TIMEZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"
[[ -n "$TZ_NAME" ]] || TZ_NAME=Etc/UTC
[[ -f "/usr/share/zoneinfo/$TZ_NAME" ]] || fail "Fuso horario invalido: $TZ_NAME. Use REPORTS_TIMEZONE."
ADGUARD_URL=''
RETENTION=90
APPDIR=/opt/dnsreports
if [[ $MODE == install ]]; then
  CTID="$(pvesh get /cluster/nextid 2>/dev/null | tr -dc '0-9')"
  [[ "$CTID" =~ ^[0-9]+$ ]] || fail 'Nao foi possivel encontrar um ID livre.'
  [[ ! -e "/etc/pve/lxc/${CTID}.conf" ]] || fail "CT ${CTID} ja existe."
  ip -o link show "$BRIDGE" >/dev/null 2>&1 || fail "Bridge ${BRIDGE} nao existe."
  pvesm status | awk 'NR>1{print $1}' | grep -Fx "$STORAGE" >/dev/null || fail "Storage $STORAGE ausente."
  pvesm status | awk 'NR>1{print $1}' | grep -Fx "$TEMPLATE_STORAGE" >/dev/null || fail "Storage de templates $TEMPLATE_STORAGE ausente."
  AVAIL=$(pvesm status | awk -v s="$STORAGE" '$1==s {print $6}')
  [[ "$AVAIL" =~ ^[0-9]+$ ]] && (( AVAIL > 10*1024*1024 )) || fail "Pouco espaco em $STORAGE; mantenha pelo menos 10 GiB livres."
  printf '  📦 Container:   %s (automatico)\n  🐧 Sistema:     Debian 13 | LXC nao privilegiado\n  🌐 Rede:        DHCP em %s\n  💾 Armazenamento: %s | Templates: %s\n  🧠 Recursos:    2 CPU / 1024 MB RAM / 8 GB disco\n  🗓️  Historico:    90 dias | Fuso: %s\n\n' "$CTID" "$BRIDGE" "$STORAGE" "$TEMPLATE_STORAGE" "$TZ_NAME"
  printf '%b  Informe o endereco e os acessos do AdGuard e do painel:%b\n' "$BLUE" "$RESET"
  read -r -p '  URL do AdGuard Home (ex.: http://IP:3000): ' ADGUARD_URL
  ADGUARD_URL="${ADGUARD_URL%/}"
  [[ "$ADGUARD_URL" =~ ^https?://[^[:space:]]+$ ]] || fail 'Informe uma URL http(s) valida do AdGuard Home.'
  [[ "$ADGUARD_URL" != *'@'* ]] || fail 'Nao inclua credenciais na URL; forneca usuario/senha separadamente.'
  read -r -p '  Usuario AdGuard [admin]: ' ADGUARD_USER
  ADGUARD_USER="${ADGUARD_USER:-admin}"
  read -r -s -p '  Senha do AdGuard: ' ADGUARD_PASS; printf '\n'
  [[ -n "$ADGUARD_PASS" ]] || fail 'Senha do AdGuard vazia.'
  read -r -p '  Administrador do painel [admin]: ' PANEL_USER
  PANEL_USER="${PANEL_USER:-admin}"
  [[ "$PANEL_USER" =~ ^[a-zA-Z0-9._-]{3,40}$ ]] || fail 'Usuario do painel invalido (3-40 caracteres, letras, numeros, ponto, _ ou -).'
  read -r -s -p '  Senha do administrador (minimo 10 caracteres): ' PANEL_PASS; printf '\n\n'
  (( ${#PANEL_PASS} >= 10 && ${#PANEL_PASS} <= 256 )) || fail 'Senha deve ter de 10 a 256 caracteres.'
else
  pct config "$CTID" >/dev/null || fail "Container $CTID nao existe."
  if [[ $(pct status "$CTID") != *running* ]]; then pct start "$CTID"; fi
  pct exec "$CTID" -- test -f "$APPDIR/config.json" || fail 'O CT informado nao parece conter o painel original.'
  pct exec "$CTID" -- test -f '/etc/systemd/system/dnsreports.service' || fail 'Unidade dnsreports nao encontrada neste CT.'
  info "Atualizando o painel do CT $CTID sem recriar o container ou apagar o banco."
fi
TMP="$(mktemp -d /root/adguard-reports.XXXXXXXX)"
trap 'rm -rf "${TMP:-}"' EXIT
if [[ $MODE == install ]]; then
  info 'Localizando o template Debian 13...'
  TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
  if [[ -z "$TEMPLATE" ]]; then
    pveam update
    TEMPLATE="$(pveam available --section system | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
    [[ -n "$TEMPLATE" ]] || fail 'Template Debian 13 indisponivel.'
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi
  ok "Template: $TEMPLATE"
  export ADGUARD_URL ADGUARD_USER ADGUARD_PASS PANEL_USER PANEL_PASS RETENTION TZ_NAME
  python3 - "$TMP/config.json" <<'PYCONF'
import os,sys,json,secrets,hashlib
salt=secrets.token_bytes(16); rounds=380000
h=hashlib.pbkdf2_hmac('sha256',os.environ['PANEL_PASS'].encode(),salt,rounds)
settings={
 'adguard_url':os.environ['ADGUARD_URL'],
 'adguard_username':os.environ['ADGUARD_USER'],
 'adguard_password':os.environ['ADGUARD_PASS'],
 'panel_username':os.environ['PANEL_USER'],
 'panel_password_hash':f'pbkdf2_sha256${rounds}${salt.hex()}${h.hex()}',
 'session_secret':secrets.token_hex(48),
 'retention_days':90,
 'timezone':os.environ['TZ_NAME'],
 'initial_history_days':7,
}
with open(sys.argv[1],'w') as f: json.dump(settings,f,ensure_ascii=False)
os.chmod(sys.argv[1],0o600)
PYCONF
  unset ADGUARD_PASS PANEL_PASS
  info "Criando LXC $CTID com DHCP..."
  # 022 ONLY for pct create: unprivileged lxc-usernsexec needs to traverse its rootfs.
  umask 022
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname adguard-dns-reports --ostype debian --unprivileged 1 \
    --rootfs "${STORAGE}:8" --cores 2 --memory 1024 --swap 512 \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --timezone "$TZ_NAME" --onboot 1 --start 1
  umask 077
  ok 'LXC criado.'
  info 'Instalando nginx, Python e dependencias...'
  pct exec "$CTID" -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates nginx openssl curl && apt-get clean'
  pct exec "$CTID" -- mkdir -p "$APPDIR/templates" "$APPDIR/static" "$APPDIR/data"
  pct exec "$CTID" -- python3 -m venv "$APPDIR/venv"
  pct exec "$CTID" -- "$APPDIR/venv/bin/pip" install --no-cache-dir 'Flask>=3.1,<4' 'requests>=2.31,<3' 'openpyxl>=3.1,<4' 'reportlab>=4,<5' 'gunicorn>=23,<25'
else
  info 'Criando backup da configuracao de acesso antiga...'
  pct exec "$CTID" -- bash -c 'cp -a /opt/dnsreports/config.json /opt/dnsreports/config.json.pre-generic-backup; python3 - <<"PYUP"
import json,os
p="/opt/dnsreports/config.json"
with open(p) as f: s=json.load(f)
s["retention_days"]=90
if not s.get("timezone"):
    try:
        from pathlib import Path
        s["timezone"]=Path("/etc/timezone").read_text().strip() or "Etc/UTC"
    except (OSError, ValueError):
        s["timezone"]="Etc/UTC"
with open(p,"w") as f: json.dump(s,f,ensure_ascii=False)
os.chmod(p,0o640)
PYUP'
fi
info 'Instalando AdGuard DNS Reports, relatórios e permissões exclusivas do administrador...'
cat >"$TMP/app.py" <<'ADGUARD_REPORTS_ASSET_APP_END'
"""Read-only, internal DNS reporting panel."""
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from html import escape

from flask import Flask, Response, abort, g, redirect, render_template, request, send_file, session, url_for
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

from core import config, db_connect, create_schema

settings = config()
app = Flask(__name__)
app.secret_key = settings['session_secret']
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax', SESSION_COOKIE_SECURE=True,
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=8), MAX_CONTENT_LENGTH=100000)
ZONE = ZoneInfo(settings.get('timezone', 'Etc/UTC'))
ATTEMPTS = {}
USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")
FIELDS = ['Data/Hora', 'Hostname', 'IP', 'Dominio', 'Servico provavel', 'Categoria', 'Tipo DNS', 'Resultado', 'Bloqueada', 'Motivo']


@app.after_request
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
    if not hmac.compare_digest(request.form.get('csrf', ''), session.get('csrf', '')):
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


@app.before_request
def auth_gate():
    if request.path.startswith('/static/') or request.path == '/login':
        return
    uid = session.get('uid')
    if not uid:
        return redirect(url_for('login'))
    c = db_connect()
    try:
        create_schema(c)
        user = c.execute('SELECT id,username,session_version,is_admin FROM users WHERE id=?', (uid,)).fetchone()
    finally:
        c.close()
    if not user or user['session_version'] != session.get('session_version'):
        session.clear()
        return redirect(url_for('login'))
    # Role is read from the database for every request, not trusted from session or UI.
    g.is_admin = bool(user['is_admin'])
    if request.path == '/usuarios' and not g.is_admin:
        abort(403)


@app.route('/login', methods=['GET', 'POST'])
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


def password_hash(password):
    salt = secrets.token_bytes(16)
    rounds = 380000
    derived = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, rounds)
    return f'pbkdf2_sha256${rounds}${salt.hex()}${derived.hex()}'


@app.route('/usuarios', methods=['GET', 'POST'])
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


@app.post('/logout')
def logout():
    require_csrf()
    session.clear()
    return redirect('/login')


def filters():
    period_raw = request.args.get('period', '7')
    period = int(period_raw) if period_raw in ('1', '7', '30', '90') else 7
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=period)
    till = now
    start = request.args.get('start', '')
    end = request.args.get('end', '')
    if start or end:
        try:
            if start:
                since = datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=ZONE).astimezone(timezone.utc)
            if end:
                till = (datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)).replace(tzinfo=ZONE).astimezone(timezone.utc)
        except ValueError:
            abort(400, 'Data inválida')
        if since >= till or till - since > timedelta(days=366):
            abort(400, 'Intervalo inválido ou maior que 366 dias')
    clauses = ['ts >= ?', 'ts < ?']
    values = [since.isoformat(timespec='microseconds').replace('+00:00', 'Z'),
              till.isoformat(timespec='microseconds').replace('+00:00', 'Z')]
    client = request.args.get('client', '').strip()[:120]
    search = request.args.get('search', '').strip()[:150]
    status = request.args.get('status', 'all')
    if client:
        clauses.append('(ip=? OR hostname=?)')
        values.extend([client, client])
    if search:
        clauses.append('(domain LIKE ? OR service LIKE ? OR category LIKE ?)')
        values.extend(['%' + search + '%'] * 3)
    if status in ('blocked', 'allowed'):
        clauses.append('blocked = ?')
        values.append(1 if status == 'blocked' else 0)
    return (' AND '.join(clauses), values, period)


def human_time(utc):
    try:
        return datetime.fromisoformat(utc.replace('Z', '+00:00')).astimezone(ZONE).strftime('%d/%m/%Y %H:%M:%S')
    except (ValueError, AttributeError):
        return utc


def label(row):
    return row['hostname'] if row['hostname'] and row['hostname'] != row['ip'] else row['ip']


def excel_safe(value):
    text = str(value if value is not None else '')
    # Prevent formula injection when opened in spreadsheet programs.
    return "'" + text if text.startswith(('=', '+', '-', '@', '\t', '\r', '\n')) else text


def row_fields(row):
    return [human_time(row['ts']), label(row), row['ip'], row['domain'], row['service'], row['category'],
            row['qtype'], row['status'], 'Sim' if row['blocked'] else 'Não', row['reason']]


@app.get('/')
def dashboard():
    where, values, period = filters()
    conn = db_connect()
    create_schema(conn)
    try:
        summary = conn.execute('''SELECT COUNT(*) count, COUNT(DISTINCT ip) computers,
                COALESCE(SUM(blocked),0) blocked FROM queries WHERE ''' + where, values).fetchone()
        services = conn.execute('''SELECT service name, COUNT(*) hits FROM queries WHERE ''' + where +
                                ''' GROUP BY service ORDER BY hits DESC LIMIT 12''', values).fetchall()
        clients = conn.execute('''SELECT CASE WHEN hostname=ip OR hostname='' THEN ip ELSE hostname END name,
               ip, COUNT(*) hits FROM queries WHERE ''' + where +
               ''' GROUP BY ip ORDER BY hits DESC LIMIT 12''', values).fetchall()
        domains = conn.execute('SELECT domain name,COUNT(*) hits FROM queries WHERE ' + where +
                               ' GROUP BY domain ORDER BY hits DESC LIMIT 10', values).fetchall()
        rows = conn.execute('SELECT * FROM queries WHERE ' + where +
                            ' ORDER BY ts DESC LIMIT 150', values).fetchall()
        choices = conn.execute('''SELECT ip, MAX(CASE WHEN hostname!=ip THEN hostname ELSE '' END) name
                                  FROM queries GROUP BY ip ORDER BY name,ip''').fetchall()
        meta = dict(conn.execute('SELECT key,value FROM metadata').fetchall())
    finally:
        conn.close()
    max_service = max((r['hits'] for r in services), default=1)
    max_clients = max((r['hits'] for r in clients), default=1)
    return render_template('dashboard.html', username=session.get('username',''), summary=summary, services=services, clients=clients,
                           domains=domains, rows=rows, choices=choices, meta=meta, period=period,
                           args=request.args, max_service=max_service, max_clients=max_clients,
                           dt=human_time, csrf=token(), total=sum(r['hits'] for r in services))


@app.get('/export/<format_name>')
def export(format_name):
    if format_name not in ('csv', 'xlsx', 'pdf'):
        abort(404)
    where, values, _ = filters()
    conn = db_connect()
    create_schema(conn)
    limit = 1000 if format_name == 'pdf' else 100000
    rows = conn.execute('SELECT * FROM queries WHERE ' + where +
                        ' ORDER BY ts DESC LIMIT ?', [*values, limit]).fetchall()
    conn.close()
    stamp = datetime.now(ZONE).strftime('%Y%m%d-%H%M')
    filename = f'adguard-reports-consulta-acesso-redes-{stamp}.{format_name}'
    if format_name == 'csv':
        out = io.StringIO()
        writer = csv.writer(out, delimiter=';', lineterminator='\r\n')
        writer.writerow(FIELDS)
        for item in rows:
            writer.writerow([excel_safe(x) for x in row_fields(item)])
        return Response('\ufeff' + out.getvalue(), mimetype='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})
    if format_name == 'xlsx':
        wb = Workbook(write_only=True)
        ws = wb.create_sheet('Consultas DNS')
        ws.append(FIELDS)
        for item in rows:
            ws.append([excel_safe(x) for x in row_fields(item)])
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        return send_file(output, as_attachment=True, download_name=filename,
                         mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    output = io.BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=26, rightMargin=26, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = [Paragraph('AdGuard DNS Reports | Relatorios de Consultas DNS — Consultas DNS', styles['Title']),
             Paragraph('Documento gerado a partir das consultas DNS. Domínios e serviços prováveis não comprovam acesso efetivo.', styles['Normal']),
             Paragraph(f'Gerado em {escape(datetime.now(ZONE).strftime("%d/%m/%Y %H:%M"))} — {len(rows)} consultas (PDF limitado a 1.000 linhas)', styles['Normal']),
             Spacer(1, 14)]
    head = ['Data / Hora', 'Computador', 'IP', 'Domínio', 'Serviço provável', 'Bloqueada']
    tdata = [head]
    for r in rows:
        vals = [human_time(r['ts']), label(r), r['ip'], r['domain'], r['service'], 'Sim' if r['blocked'] else 'Não']
        tdata.append([Paragraph(escape(str(x)), styles['BodyText']) for x in vals])
    table = Table(tdata, repeatRows=1, colWidths=[107, 131, 91, 193, 131, 57], hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND', (0,0),(-1,0),colors.HexColor('#e7edf5')),
                               ('VALIGN', (0,0),(-1,-1),'TOP'),
                               ('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),
                               ('LINEBELOW',(0,0),(-1,-1),0.3,colors.HexColor('#dbe4ed'))]))
    story.append(table)
    doc.build(story)
    output.seek(0)
    return send_file(output, as_attachment=True, download_name=filename, mimetype='application/pdf')


@app.get('/healthz')
def healthz():
    # Health check still requires login for all other URLs. nginx can check locally.
    return Response('ok', mimetype='text/plain')

ADGUARD_REPORTS_ASSET_APP_END
cat >"$TMP/core.py" <<'ADGUARD_REPORTS_ASSET_CORE_END'
"""DNS Reports: read-only collector for AdGuard Home's querylog API."""
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE = Path('/opt/dnsreports')
CONFIG = Path(os.environ.get('DNS_REPORTS_CONFIG', str(BASE / 'config.json')))

# Specific patterns first. Labels describe DNS domains, not proof of app usage.
SERVICES = [
    ('YouTube', 'Streaming', ('youtube.com', 'youtu.be', 'googlevideo.com', 'ytimg.com')),
    ('Netflix', 'Streaming', ('netflix.com', 'nflxvideo.net', 'nflximg.net', 'nflxso.net')),
    ('Spotify', 'Streaming', ('spotify.com', 'scdn.co', 'spotifycdn.com')),
    ('Twitch', 'Streaming', ('twitch.tv', 'ttvnw.net')),
    ('Instagram', 'Redes sociais', ('instagram.com', 'cdninstagram.com')),
    ('Facebook', 'Redes sociais', ('facebook.com', 'fbcdn.net', 'fbsbx.com')),
    ('TikTok', 'Redes sociais', ('tiktok.com', 'tiktokcdn.com', 'tiktokv.com', 'byteoversea.com')),
    ('X / Twitter', 'Redes sociais', ('x.com', 'twitter.com', 'twimg.com')),
    ('LinkedIn', 'Redes sociais', ('linkedin.com', 'licdn.com')),
    ('WhatsApp', 'Comunicação', ('whatsapp.com', 'whatsapp.net')),
    ('Telegram', 'Comunicação', ('telegram.org', 't.me')),
    ('Microsoft Teams', 'Comunicação', ('teams.microsoft.com', 'teams.live.com', 'skype.com')),
    ('Zoom', 'Comunicação', ('zoom.us', 'zoom.com')),
    ('Google Meet', 'Comunicação', ('meet.google.com',)),
    ('Discord', 'Comunicação', ('discord.com', 'discordapp.com', 'discord.gg')),
    ('OneDrive / SharePoint', 'Trabalho', ('sharepoint.com', 'onedrive.com', '1drv.com', 'storage.live.com')),
    ('Microsoft 365', 'Trabalho', ('office.com', 'office365.com', 'outlook.com', 'graph.microsoft.com', 'microsoftonline.com', 'login.microsoftonline.com')),
    ('Google Drive', 'Trabalho', ('drive.google.com', 'docs.google.com', 'sheets.google.com')),
    ('Google Workspace', 'Trabalho', ('gmail.com', 'mail.google.com', 'workspace.google.com')),
    ('TOTVS', 'Trabalho', ('totvs.com', 'totvs.com.br', 'cloudtotvs.com.br')),
    ('Windows Update', 'Sistema / atualizações', ('windowsupdate.com', 'update.microsoft.com', 'delivery.mp.microsoft.com')),
    ('Microsoft', 'Sistema / serviços', ('microsoft.com', 'msftconnecttest.com', 'msftncsi.com', 'azure.com', 'azureedge.net')),
    ('Google', 'Sistema / serviços', ('google.com', 'googleapis.com', 'gstatic.com', 'googleusercontent.com')),
    ('Cloudflare', 'Infraestrutura', ('cloudflare.com', 'cloudflare-dns.com')),
    ('Amazon AWS', 'Infraestrutura', ('amazonaws.com', 'aws.amazon.com', 'cloudfront.net')),
    ('AnyDesk', 'Acesso remoto', ('anydesk.com',)),
    ('TeamViewer', 'Acesso remoto', ('teamviewer.com',)),
    ('Ubiquiti', 'Infraestrutura', ('ui.com', 'ubnt.com')),
]


def classify(domain):
    d = (domain or '').lower().strip('. ')
    for service, category, patterns in SERVICES:
        if any(d == p or d.endswith('.' + p) for p in patterns):
            return service, category
    return 'Não identificado', 'Outros'


def config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))


def db_connect():
    c = sqlite3.connect(str(BASE / 'data' / 'queries.sqlite3'), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=30000')
    c.execute('PRAGMA journal_mode=WAL')
    return c


def create_schema(c):
    c.executescript('''
      CREATE TABLE IF NOT EXISTS queries (
        uid TEXT PRIMARY KEY,
        ts TEXT NOT NULL,
        ip TEXT NOT NULL,
        hostname TEXT NOT NULL,
        domain TEXT NOT NULL,
        service TEXT NOT NULL,
        category TEXT NOT NULL,
        qtype TEXT NOT NULL,
        status TEXT NOT NULL,
        blocked INTEGER NOT NULL,
        reason TEXT NOT NULL
      );
      CREATE INDEX IF NOT EXISTS idx_queries_ts ON queries(ts);
      CREATE INDEX IF NOT EXISTS idx_queries_ip_ts ON queries(ip,ts);
      CREATE INDEX IF NOT EXISTS idx_queries_domain_ts ON queries(domain,ts);
      CREATE INDEX IF NOT EXISTS idx_queries_service_ts ON queries(service,ts);
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        password_hash TEXT NOT NULL,
        session_version INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        is_admin INTEGER NOT NULL DEFAULT 0
      );
      CREATE TABLE IF NOT EXISTS client_names (
        ip TEXT PRIMARY KEY,
        hostname TEXT NOT NULL,
        last_checked INTEGER NOT NULL
      );
    ''')
    # Safe, additive SQLite migration for existing multi-user installations.
    columns = {r['name'] for r in c.execute('PRAGMA table_info(users)').fetchall()}
    if 'is_admin' not in columns:
        c.execute('ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0')
    # Existing installations have a single administrator in config.json.
    # Import it exactly once without changing the credentials or existing data.
    legacy = config()
    if not c.execute("SELECT 1 FROM metadata WHERE key='users_imported'").fetchone():
        if legacy.get('panel_username') and legacy.get('panel_password_hash'):
            c.execute('INSERT OR IGNORE INTO users(username,password_hash) VALUES(?,?)',
                      (legacy['panel_username'],legacy['panel_password_hash']))
        c.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES('users_imported','true')")
    # Assign administration only ONCE to the account originally set up by the installer.
    # Other existing users remain read-only for user management. Re-running the
    # migration cannot promote a later account with the same username.
    if not c.execute("SELECT 1 FROM metadata WHERE key='admin_role_migrated_v1'").fetchone():
        admin_username = legacy.get('panel_username') or 'admin'
        c.execute('UPDATE users SET is_admin=1 WHERE username=? COLLATE NOCASE',
                  (admin_username,))
        c.execute("INSERT INTO metadata(key,value) VALUES('admin_role_migrated_v1','true')")
    c.commit()


def iso_timestamp(ts):
    """Convert timestamp to sortable UTC at 1 microsecond resolution."""
    try:
        d = datetime.fromisoformat(ts.strip().replace('Z', '+00:00'))
        if not d.tzinfo:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
    except (ValueError, AttributeError):
        return ''


def valid_private_ip(value):
    try:
        x = ipaddress.ip_address(value)
        return x.is_private and not x.is_loopback
    except ValueError:
        return False


def lookup_hostname(ip, c, fallback=''):
    if not valid_private_ip(ip):
        return fallback or ip
    cached = c.execute('SELECT hostname,last_checked FROM client_names WHERE ip=?', (ip,)).fetchone()
    now = int(time.time())
    if cached and cached['last_checked'] > now - 3600:
        return cached['hostname'] or fallback or ip
    result = fallback or ip
    try:
        name = socket.gethostbyaddr(ip)[0].rstrip('.')
        if name and name != ip:
            result = name
    except (socket.herror, OSError, TimeoutError):
        pass
    c.execute('INSERT INTO client_names(ip,hostname,last_checked) VALUES(?,?,?) '
              'ON CONFLICT(ip) DO UPDATE SET hostname=excluded.hostname,last_checked=excluded.last_checked',
              (ip, result, now))
    return result


def row_from_api(entry, c):
    question = entry.get('question') or {}
    domain = (question.get('name') or question.get('host') or '').rstrip('.').lower()
    ip = str(entry.get('client') or '').strip()
    ts_original = entry.get('time') or ''
    ts = iso_timestamp(ts_original)
    if not ts or not domain:
        return None
    info = entry.get('client_info') or {}
    fallback = str(info.get('name') or '').strip() if isinstance(info, dict) else ''
    hostname = lookup_hostname(ip, c, fallback)
    service, category = classify(domain)
    reason = str(entry.get('reason') or '')
    status = str(entry.get('status') or '')
    blocked = int(reason.startswith('Filtered') or reason in ('SafeBrowsing', 'SafeSearch', 'Parental', 'BlockedService', 'FilteredBlockedService'))
    # Hash of the raw event makes repeated overlap downloads idempotent.
    digest = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str).encode('utf-8')).hexdigest()
    return (digest, ts, ip, hostname, domain, service, category,
            str(question.get('type') or ''), status, blocked, reason)


def collect(max_pages=500):
    settings = config()
    root = settings['adguard_url'].rstrip('/') + '/control/querylog'
    session = requests.Session()
    session.auth = (settings['adguard_username'], settings['adguard_password'])
    c = db_connect()
    create_schema(c)
    previous = c.execute("SELECT value FROM metadata WHERE key='last_success'").fetchone()
    # Backtrack 5 minutes to compensate for delays, then deduplicate by event hash.
    previous_dt = datetime.fromisoformat(previous[0].replace('Z', '+00:00')) - timedelta(minutes=5) if previous else None
    now = datetime.now(timezone.utc)
    first_import_cutoff = now - timedelta(days=int(settings.get('initial_history_days', 7)))
    cutoff = previous_dt or first_import_cutoff
    cursor = ''
    total_added, pages, seen = 0, 0, set()
    complete = False
    try:
        for pages in range(1, max_pages + 1):
            params = {'limit': 500}
            if cursor:
                params['older_than'] = cursor
            r = session.get(root, params=params, timeout=(8, 30))
            r.raise_for_status()
            payload = r.json()
            data = payload.get('data') or []
            if not data:
                complete = True
                break
            rows = []
            oldest_time = None
            for entry in data:
                ts = iso_timestamp(entry.get('time', ''))
                if ts:
                    dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                    if oldest_time is None or dt < oldest_time:
                        oldest_time = dt
                    if dt < cutoff:
                        continue
                row = row_from_api(entry, c)
                if row:
                    rows.append(row)
            before = c.total_changes
            c.executemany('''INSERT OR IGNORE INTO queries
                  (uid,ts,ip,hostname,domain,service,category,qtype,status,blocked,reason)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''', rows)
            total_added += c.total_changes - before
            c.commit()
            new_cursor = payload.get('oldest') or ''
            if not new_cursor or new_cursor == cursor or new_cursor in seen or (oldest_time and oldest_time < cutoff):
                complete = True
                break
            seen.add(new_cursor)
            cursor = new_cursor
        # Even if initial backfill hit the limit, advance the watermark for future captures;
        # tell the operator in the logs that old history is incomplete.
        c.execute("INSERT INTO metadata(key,value) VALUES('last_success', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (now.isoformat().replace('+00:00', 'Z'),))
        c.execute("INSERT INTO metadata(key,value) VALUES('last_pages', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(pages),))
        c.execute("INSERT INTO metadata(key,value) VALUES('last_import_complete', ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('true' if complete else 'false',))
        ttl = int(settings.get('retention_days', 90))
        delete_before = (now - timedelta(days=ttl)).isoformat().replace('+00:00', 'Z')
        c.execute('DELETE FROM queries WHERE ts < ?', (delete_before,))
        # Refresh hostname information for existing devices whenever a PTR first becomes available.
        c.execute('''UPDATE queries SET hostname=(SELECT hostname FROM client_names WHERE ip=queries.ip)
                     WHERE ip IN (SELECT ip FROM client_names WHERE hostname != ip)
                     AND hostname=ip''')
        c.commit()
        print(f'Consulta OK: {pages} paginas, {total_added} novos registros, completo={complete}', flush=True)
    finally:
        c.close()
    return total_added


if __name__ == '__main__':
    collect()

ADGUARD_REPORTS_ASSET_CORE_END
cat >"$TMP/login.html" <<'ADGUARD_REPORTS_ASSET_LOGIN_END'
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entrar · Relatorios de Consultas DNS | AdGuard DNS Reports</title><link rel="stylesheet" href="{{ url_for('static',filename='style.css') }}"></head>
<body class="login-body"><main class="login-card"><div class="brand"><div class="brand-mark">◈</div><div><strong>AdGuard DNS Reports</strong><small>Relatorios de Consultas DNS</small></div></div><h1>Acesso ao painel</h1><p class="muted">Entre com suas credenciais para consultar o histórico de sua rede.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}"><label for="username">Usuário</label><input autofocus id="username" name="username" autocomplete="username" required><label for="password">Senha</label><input id="password" type="password" name="password" autocomplete="current-password" required><button class="btn primary full">Entrar</button></form><p class="login-foot">AdGuard DNS Reports · Consulta de acessos de rede · ambiente interno</p></main></body></html>

ADGUARD_REPORTS_ASSET_LOGIN_END
cat >"$TMP/dashboard.html" <<'ADGUARD_REPORTS_ASSET_DASH_END'
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Relatorios de Consultas DNS · AdGuard DNS Reports</title><link rel="stylesheet" href="{{ url_for('static',filename='style.css') }}"></head><body>
<div class="shell"><aside class="sidebar"><div class="brand"><div class="brand-mark">◈</div><div><strong>AdGuard DNS Reports</strong><small>Relatorios de Consultas DNS</small></div></div><div class="aside-section">PAINEL</div><a class="nav active" href="/">▦ <span>Visão geral</span></a><a class="nav" href="#consultas">⌕ <span>Consultas DNS</span></a>{% if g.is_admin %}<a class="nav" href="/usuarios">♙ <span>Usuários do painel</span></a>{% endif %}<a class="nav" href="#servicos">◫ <span>Serviços e domínios</span></a><div class="sidebar-bottom"><span class="status-dot"></span> Servidor interno <div class="aside-note">Relatórios de consultas DNS<br>Não equivale a histórico de navegação.</div></div></aside>
<main class="main"><div class="topbar"><span>AdGuard DNS Reports <span class="crumb">/ Relatorios de Consultas DNS</span></span><span class="user-tag">{{username}}</span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{csrf}}"><button class="btn subtle" type="submit">Sair ↗</button></form></div><div class="content"><div class="pageheading"><div><div class="eyebrow">MONITORAMENTO DNS</div><h1>Relatorios de Consultas DNS</h1><p class="muted">Consultas realizadas pelos dispositivos monitorados pelo AdGuard Home.</p></div><div class="updated"><div class="actions export-top"><a class="btn outline" href="{{ url_for('export',format_name='csv') }}?{{ request.query_string.decode() }}">↓ CSV</a><a class="btn primary" href="{{ url_for('export',format_name='xlsx') }}?{{ request.query_string.decode() }}">↓ Excel</a><a class="btn outline" href="{{ url_for('export',format_name='pdf') }}?{{ request.query_string.decode() }}">↓ PDF</a></div>Última coleta<br><strong>{{ dt(meta.get('last_success','')) if meta.get('last_success') else 'Aguardando primeira coleta' }}</strong></div></div>
<form class="filters" method="get"><div class="filter-group"><label for="period">Período</label><select name="period" id="period"><option value="1" {% if period==1 %}selected{% endif %}>Últimas 24 horas</option><option value="7" {% if period==7 %}selected{% endif %}>Últimos 7 dias</option><option value="30" {% if period==30 %}selected{% endif %}>Últimos 30 dias</option><option value="90" {% if period==90 %}selected{% endif %}>Últimos 90 dias</option></select></div>
<div class="filter-group"><label for="client">Computador</label><select name="client" id="client"><option value="">Todos os computadores</option>{% for c in choices %}<option value="{{c.ip}}" {% if args.get('client','')==c.ip %}selected{% endif %}>{{ c.name or c.ip }} {% if c.name and c.name != c.ip %}({{c.ip}}){% endif %}</option>{% endfor %}</select></div>
<div class="filter-group"><label for="status">Resultado</label><select name="status" id="status"><option value="all">Todas</option><option value="allowed" {% if args.get('status')=='allowed' %}selected{% endif %}>Permitidas</option><option value="blocked" {% if args.get('status')=='blocked' %}selected{% endif %}>Bloqueadas</option></select></div>
<div class="filter-group search"><label for="search">Domínio, serviço ou categoria</label><input id="search" name="search" placeholder="Ex.: youtube.com ou Teams" value="{{args.get('search','')}}"></div>
<div class="filter-actions"><button class="btn primary">Aplicar filtros</button><a class="btn subtle" href="/">Limpar</a></div><div class="date-options"><div><label for="start">Início (opcional)</label><input type="date" id="start" name="start" value="{{args.get('start','')}}"></div><div><label for="end">Fim (opcional)</label><input type="date" id="end" name="end" value="{{args.get('end','')}}"></div></div></form>
<div class="stats"><div class="stat"><div class="stat-label">Total de consultas</div><strong>{{'{:,}'.format(summary['count']).replace(',','.')}}</strong><small>Durante o período selecionado</small></div><div class="stat"><div class="stat-label">Computadores ativos</div><strong>{{summary['computers']}}</strong><small>IPs distintos consultando DNS</small></div><div class="stat"><div class="stat-label">Consultas bloqueadas</div><strong>{{'{:,}'.format(summary['blocked']).replace(',','.')}}</strong><small>Bloqueios pelo AdGuard</small></div><div class="stat"><div class="stat-label">Taxa de bloqueio</div><strong>{{'%.1f'|format(summary['blocked']*100/summary['count'] if summary['count'] else 0)}}%</strong><small>Proporção de consultas</small></div></div>
<div class="two-col" id="servicos"><section class="panel"><div class="panel-head"><div><h2>Serviços identificados</h2><p>Classificação aproximada por domínio</p></div></div>{% for s in services %}<div class="barrow"><div class="bar-top"><span title="{{s.name}}">{{s.name}}</span><b>{{s.hits}}</b></div><progress class="meter" value="{{s.hits}}" max="{{max_service}}"></progress></div>{% else %}<div class="empty">Ainda não há dados no período.</div>{% endfor %}</section>
<section class="panel"><div class="panel-head"><div><h2>Computadores com mais consultas</h2><p>Por hostname ou endereço IP</p></div></div>{% for c in clients %}<div class="barrow"><div class="bar-top"><span title="{{c.ip}}">{{c.name}}</span><b>{{c.hits}}</b></div><progress class="meter alt" value="{{c.hits}}" max="{{max_clients}}"></progress></div>{% else %}<div class="empty">Nenhum computador encontrado.</div>{% endfor %}</section></div>
<section class="panel top-domains"><div class="panel-head"><div><h2>Domínios mais consultados</h2><p>Ranking das solicitações DNS</p></div></div><div class="domains-grid">{% for domain in domains %}<div class="domain"><span class="ranking">{{loop.index}}</span><span class="domain-name">{{domain.name}}</span><strong>{{domain.hits}}</strong></div>{% else %}<div class="empty">Nenhum domínio registrado.</div>{% endfor %}</div></section>
<section class="panel" id="consultas"><div class="panel-head wrap"><div><h2>Registro de consultas DNS</h2><p>Mostrando as 150 consultas mais recentes dos filtros. As exportações incluem até 100.000 linhas (PDF: 1.000).</p></div><div class="actions"><a class="btn outline" href="{{ url_for('export',format_name='csv') }}?{{ request.query_string.decode() }}">↓ CSV</a><a class="btn outline" href="{{ url_for('export',format_name='xlsx') }}?{{ request.query_string.decode() }}">↓ Excel</a><a class="btn outline" href="{{ url_for('export',format_name='pdf') }}?{{ request.query_string.decode() }}">↓ PDF</a></div></div><div class="table-scroll"><table><thead><tr><th>Data e hora</th><th>Computador</th><th>IP</th><th>Domínio consultado</th><th>Serviço provável</th><th>Resultado</th></tr></thead><tbody>{% for r in rows %}<tr><td class="nowrap">{{dt(r.ts)}}</td><td class="hostname" title="{{r.hostname}}">{{r.hostname if r.hostname and r.hostname!=r.ip else 'Não identificado'}}</td><td class="mono">{{r.ip}}</td><td class="domaintext">{{r.domain}}</td><td>{{r.service}}</td><td>{% if r.blocked %}<span class="pill blocked">Bloqueada</span>{% else %}<span class="pill allowed">Permitida</span>{% endif %}</td></tr>{% else %}<tr><td colspan="6" class="empty">Nenhum registro com estes filtros. Verifique se o coletor está ativo.</td></tr>{% endfor %}</tbody></table></div></section>
<footer>AdGuard DNS Reports · Relatorios de Consultas DNS · Histórico de consultas DNS, não de navegação ou uso comprovado de aplicativos. Os nomes de serviços são estimativas. {% if meta.get('last_import_complete') == 'false' %}<br><strong>Aviso:</strong> uma coleta atingiu o limite de páginas; o histórico antigo pode estar incompleto.{% endif %}</footer>
</div></main></div>
</body></html>

ADGUARD_REPORTS_ASSET_DASH_END
cat >"$TMP/users.html" <<'ADGUARD_REPORTS_ASSET_USERS_END'
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Usuários · Relatorios de Consultas DNS | AdGuard DNS Reports</title><link rel="stylesheet" href="{{ url_for('static',filename='style.css') }}"></head><body>
<div class="shell"><aside class="sidebar"><div class="brand"><div class="brand-mark">◈</div><div><strong>AdGuard DNS Reports</strong><small>Relatorios de Consultas DNS</small></div></div><div class="aside-section">PAINEL</div><a class="nav" href="/">▦ <span>Visão geral</span></a><a class="nav active" href="/usuarios">♙ <span>Usuários do painel</span></a><div class="sidebar-bottom"><span class="status-dot"></span> Servidor interno<div class="aside-note">Somente o administrador pode criar, excluir e redefinir senhas de usuários.</div></div></aside>
<main class="main"><div class="topbar"><span>AdGuard DNS Reports <span class="crumb">/ Usuários</span></span><form action="/logout" method="post"><input type="hidden" name="csrf" value="{{csrf}}"><button class="btn subtle">Sair ↗</button></form></div>
<div class="content"><div class="pageheading"><div><div class="eyebrow">CONFIGURAÇÕES DE ACESSO</div><h1>Usuários do painel</h1><p class="muted">Apenas o administrador gerencia usuários. As demais contas podem consultar e exportar relatórios.</p></div><a class="btn outline" href="/">← Voltar aos relatórios</a></div>
{% if error %}<div class="error alert">{{error}}</div>{% endif %}{% if success %}<div class="success alert">{{success}}</div>{% endif %}
<div class="two-col"><section class="panel"><div class="panel-head"><h2>Adicionar usuário</h2></div><form method="post" class="user-form"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="action" value="add"><label for="new-user">Usuário</label><input id="new-user" name="username" autocomplete="off" minlength="3" maxlength="40" pattern="[A-Za-z0-9._-]{3,40}" placeholder="Ex.: analista.rede" required><label for="new-pass">Senha (mínimo de 10 caracteres)</label><input id="new-pass" name="password" type="password" autocomplete="new-password" minlength="10" maxlength="256" required><button class="btn primary">Criar usuário</button></form></section>
<section class="panel"><div class="panel-head"><h2>Usuários existentes</h2><p>Senha pode ser redefinida individualmente</p></div>
{% for user in users %}<div class="user-row"><div class="user-name"><strong>{{user.username}}</strong>{% if user.is_admin %} <span class="pill allowed">Administrador</span>{% endif %}<small>Desde {{user.created_at}}</small></div><form method="post" class="inline-user-form"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="action" value="reset"><input type="hidden" name="user_id" value="{{user.id}}"><input name="password" type="password" autocomplete="new-password" minlength="10" maxlength="256" placeholder="Nova senha" required aria-label="Nova senha para {{user.username}}"><button class="btn outline" title="Alterar senha">Alterar senha</button></form>{% if not user.is_admin %}<form method="post"><input type="hidden" name="csrf" value="{{csrf}}"><input type="hidden" name="action" value="delete"><input type="hidden" name="user_id" value="{{user.id}}"><button class="btn danger">Excluir</button></form>{% endif %}</div>{% else %}<div class="empty">Nenhum usuário cadastrado.</div>{% endfor %}
</section></div><footer>AdGuard DNS Reports · Relatorios de Consultas DNS | Acesso restrito à rede interna.</footer></div></main></div></body></html>
ADGUARD_REPORTS_ASSET_USERS_END
cat >"$TMP/style.css" <<'ADGUARD_REPORTS_ASSET_CSS_END'
:root{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1b2840;background:#f4f7fb;font-synthesis:none}*{box-sizing:border-box}body{margin:0}a{color:inherit;text-decoration:none}button,input,select{font:inherit}button{cursor:pointer}.shell{display:flex;min-height:100vh}.sidebar{width:244px;flex-shrink:0;background:#11223b;color:#dce6f4;padding:26px 18px;display:flex;flex-direction:column;gap:8px}.brand{display:flex;align-items:center;gap:12px}.brand-mark{display:grid;place-items:center;background:#2874ed;color:white;width:42px;height:42px;border-radius:12px;font-size:29px}.brand strong{display:block;font-size:17px;letter-spacing:-.4px}.brand small{font-size:11px;opacity:.65;display:block;margin-top:4px}.aside-section{font-size:10px;font-weight:800;letter-spacing:1.7px;color:#728baa;margin:46px 10px 12px}.nav{padding:12px 13px;border-radius:8px;font-size:13px;display:flex;gap:12px;align-items:center;color:#a9bed7}.nav.active{background:#25446b;color:white;font-weight:650}.nav:hover{background:#213b60}.sidebar-bottom{margin-top:auto;font-size:12px;color:#c1d2e5;padding:15px 8px;border-top:1px solid #344661}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#56d6aa;margin-right:7px}.aside-note{color:#849bb7;line-height:1.6;font-size:11px;margin-top:10px}.main{min-width:0;flex:1}.topbar{display:flex;align-items:center;justify-content:space-between;padding:17px 35px;background:white;border-bottom:1px solid #e6ebf2;font-size:12px;font-weight:650}.crumb{color:#96a4b7;font-weight:400}.content{max-width:1560px;padding:34px 35px 50px;margin:auto}.pageheading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:25px}.eyebrow{font-size:10px;letter-spacing:1.6px;color:#3375d6;font-weight:800}h1{font-size:28px;letter-spacing:-.9px;margin:8px 0}h2{font-size:15px;letter-spacing:-.3px;margin:0}.muted{color:#7989a0;font-size:13px;line-height:1.7;margin:5px 0}.updated{text-align:right;font-size:11px;line-height:1.8;color:#8c9ab0}.updated strong{color:#344860;font-size:12px}.filters{border:1px solid #e2e8f1;background:white;border-radius:12px;padding:19px;display:flex;flex-wrap:wrap;gap:12px 14px;align-items:end;box-shadow:0 4px 15px rgba(30,56,89,.025)}.filter-group{flex:1 1 170px;min-width:155px}.filter-group.search{flex:1.5 1 200px}.filter-group label,.date-options label,.login-card label{font-size:11px;font-weight:750;display:block;margin-bottom:8px;color:#5a6d85}input,select{width:100%;height:39px;padding:0 11px;border-radius:7px;border:1px solid #dce4ee;background:#fff;color:#33445b;font-size:12px;outline:0}input:focus,select:focus{border-color:#3b82f6;box-shadow:0 0 0 3px #e7f0ff}.filter-actions{display:flex;gap:7px;align-items:center}.btn{height:38px;padding:0 16px;border:1px solid transparent;border-radius:7px;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;white-space:nowrap}.btn.primary{background:#2269da;color:white}.btn.primary:hover{background:#175bbd}.btn.subtle{color:#556781;background:#f2f5f9}.btn.outline{background:#fff;color:#336ab4;border-color:#d9e4f3}.btn.outline:hover{background:#edf5ff}.date-options{flex:1 1 100%;display:flex;gap:12px;border-top:1px dashed #e4eaf1;padding-top:12px}.date-options>div{max-width:195px;flex:1}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:20px 0}.stat{padding:21px 22px;background:#fff;border:1px solid #e2e8f1;border-radius:12px}.stat-label{font-size:12px;font-weight:650;color:#74849a}.stat strong{display:block;font-size:31px;letter-spacing:-1.1px;margin:12px 0 7px}.stat small{font-size:11px;color:#9aa9ba}.two-col{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.panel{border:1px solid #e2e8f1;background:#fff;border-radius:12px;padding:23px;margin-bottom:18px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:22px}.panel-head p{font-size:12px;color:#8b9aae;margin:6px 0 0}.panel-head.wrap{flex-wrap:wrap}.barrow{margin:13px 0}.bar-top{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:#4b5e76;margin-bottom:8px}.bar-top span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-top b{font-size:11px;color:#72839b}.track{background:#edf2f8;height:7px;border-radius:9px;overflow:hidden}.bar{background:#397bed;height:100%;border-radius:9px}.bar.alt{background:#21a59a}.domains-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 25px}.domain{display:flex;align-items:center;gap:13px;border-bottom:1px solid #eef2f7;padding:12px 0;font-size:12px;min-width:0}.ranking{font-size:11px;background:#eaf1fc;color:#4b79b7;border-radius:6px;display:grid;place-items:center;width:23px;height:23px;flex-shrink:0}.domain-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;color:#4a607a}.domain strong{color:#263f5a}.actions{display:flex;gap:7px;flex-wrap:wrap}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px;min-width:890px}th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#8b9bb0;padding:13px 9px;border-bottom:1px solid #e5ecf3}td{padding:13px 9px;border-bottom:1px solid #f0f2f5;color:#586c83}tbody tr:hover{background:#f8fbfe}td.hostname{font-weight:700;color:#34506d}td.domaintext{color:#3168aa;word-break:break-word}.nowrap{white-space:nowrap}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.pill{padding:5px 8px;border-radius:5px;font-weight:700;font-size:10px;white-space:nowrap}.pill.allowed{background:#e6f7f0;color:#238360}.pill.blocked{background:#fff0e9;color:#ad592b}footer{font-size:11px;color:#8898ac;line-height:1.6;margin-top:20px}.empty{font-size:13px;color:#95a3b4;padding:26px 0}.login-body{min-height:100vh;display:grid;place-items:center;background:linear-gradient(150deg,#10263f,#17385e)}.login-card{background:#fff;width:min(430px,calc(100% - 36px));padding:36px;border-radius:15px;box-shadow:0 25px 70px #07132670}.login-card .brand{margin-bottom:35px}.login-card .brand strong{color:#1b2c46}.login-card .brand small{color:#78899c}.login-card h1{font-size:23px}.login-card .muted{margin-bottom:24px}.login-card input{margin-bottom:17px;height:45px}.full{width:100%;height:44px;margin-top:5px}.login-foot{font-size:10px;text-align:center;color:#92a0ae;margin:24px 0 0}.error{color:#ad362c;background:#fff0ef;padding:11px;border-radius:6px;font-size:12px}@media(max-width:1150px){.sidebar{width:190px}.stats{grid-template-columns:repeat(2,1fr)}.content{padding:25px}.topbar{padding:15px 25px}}@media(max-width:750px){.shell{display:block}.sidebar{width:auto;display:block;padding:15px}.sidebar .brand{display:inline-flex}.sidebar .aside-section,.sidebar .nav,.sidebar-bottom{display:none}.stats,.two-col,.domains-grid{grid-template-columns:1fr}.content{padding:20px 14px}.pageheading{display:block}.updated{text-align:left;margin-top:10px}.filter-group{flex:1 1 100%}.date-options>div{max-width:none}.topbar{padding:12px 16px}.panel{padding:16px}}

.meter{display:block;width:100%;height:7px;border:0;border-radius:9px;overflow:hidden;appearance:none}.meter::-webkit-progress-bar{background:#edf2f8;border-radius:9px}.meter::-webkit-progress-value{background:#397bed;border-radius:9px}.meter.alt::-webkit-progress-value{background:#21a59a}.meter::-moz-progress-bar{background:#397bed;border-radius:9px}.meter.alt::-moz-progress-bar{background:#21a59a}

/* AdGuard DNS Reports: destaque de exportacoes e gerenciamento de contas */
.brand-mark{background:linear-gradient(145deg,#0d88a8,#2355bc)}
.user-tag{margin-left:auto;margin-right:20px;color:#59718c;font-size:12px}
.export-top{justify-content:flex-end;margin-bottom:10px}
.export-top .btn{box-shadow:0 2px 6px #0a244011}
.user-form{display:flex;flex-direction:column;gap:9px;align-items:flex-start}
.user-form input{margin-bottom:9px}
.user-form input,.user-form label{width:100%}
.user-form label{font-size:12px;color:#5a6d85;font-weight:750}
.alert{padding:13px 16px;margin-bottom:18px;border-radius:10px}
.success{background:#e6faf5;color:#15694e;border:1px solid #c3e8dc}
.user-row{display:flex;align-items:center;flex-wrap:wrap;gap:8px;justify-content:space-between;padding:14px 0;border-bottom:1px solid #edf1f6}
.user-name{flex:1;min-width:150px}
.user-name strong,.user-name small{display:block}
.user-name strong{font-size:13px;color:#244260}
.user-name small{font-size:11px;color:#8596a8;margin-top:5px}
.inline-user-form{display:flex;align-items:center;gap:7px}
.inline-user-form input{width:160px}
.btn.danger{color:#ae3347;background:#fff3f5;border-color:#ffdce1}
@media(max-width:750px){.user-row,.inline-user-form{width:100%}.inline-user-form input{flex:1}.export-top{justify-content:flex-start}.user-tag{display:none}}

ADGUARD_REPORTS_ASSET_CSS_END

for name in app.py core.py; do pct push "$CTID" "$TMP/$name" "$APPDIR/$name"; done
for name in login.html dashboard.html users.html; do pct push "$CTID" "$TMP/$name" "$APPDIR/templates/$name"; done
pct push "$CTID" "$TMP/style.css" "$APPDIR/static/style.css"
if [[ "$MODE" == install ]]; then
  pct push "$CTID" "$TMP/config.json" "$APPDIR/config.json"
  pct exec "$CTID" -- bash -c 'id -u dnsreports >/dev/null 2>&1 || useradd --system --home-dir /opt/dnsreports --shell /usr/sbin/nologin dnsreports'
fi
# Both clean installs and upgrades: enforce correct traversable app directory.
# Keep config.json root-owned and only readable by the service group.
pct exec "$CTID" -- bash -c '
  chown root:dnsreports /opt/dnsreports
  chmod 750 /opt/dnsreports
  chown root:dnsreports /opt/dnsreports/app.py /opt/dnsreports/core.py
  chmod 640 /opt/dnsreports/app.py /opt/dnsreports/core.py
  chown -R root:dnsreports /opt/dnsreports/venv /opt/dnsreports/templates /opt/dnsreports/static
  chmod -R g+rX /opt/dnsreports/venv /opt/dnsreports/templates /opt/dnsreports/static
  chown root:dnsreports /opt/dnsreports/config.json
  chmod 640 /opt/dnsreports/config.json
  chown dnsreports:dnsreports /opt/dnsreports/data
  chmod 750 /opt/dnsreports/data
'
if [[ "$MODE" == install ]]; then
  cat >"$TMP/dnsreports.service" <<'ADGUARD_REPORTS_GUNICORN_UNIT'
[Unit]
Description=AdGuard DNS Reports Relatorios de Consultas DNS - Gunicorn
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
User=dnsreports
Group=dnsreports
WorkingDirectory=/opt/dnsreports
ExecStart=/opt/dnsreports/venv/bin/gunicorn --workers 2 --threads 2 --bind 127.0.0.1:8088 --timeout 120 app:app
Restart=always
RestartSec=4
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/dnsreports/data
PrivateTmp=true
[Install]
WantedBy=multi-user.target
ADGUARD_REPORTS_GUNICORN_UNIT
  cat >"$TMP/dnsreports-collector.service" <<'ADGUARD_REPORTS_COLLECTOR_UNIT'
[Unit]
Description=AdGuard DNS Reports Relatorios de Consultas DNS - coleta DNS
Wants=network-online.target
After=network-online.target
[Service]
Type=oneshot
User=dnsreports
Group=dnsreports
WorkingDirectory=/opt/dnsreports
ExecStart=/opt/dnsreports/venv/bin/python /opt/dnsreports/core.py
TimeoutStartSec=900
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/opt/dnsreports/data
PrivateTmp=true
ADGUARD_REPORTS_COLLECTOR_UNIT
  cat >"$TMP/dnsreports-collector.timer" <<'ADGUARD_REPORTS_TIMER_UNIT'
[Unit]
Description=AdGuard DNS Reports Relatorios de Consultas DNS - coleta a cada 2 minutos
[Timer]
OnBootSec=20s
OnUnitInactiveSec=120s
Persistent=true
Unit=dnsreports-collector.service
[Install]
WantedBy=timers.target
ADGUARD_REPORTS_TIMER_UNIT
  for unit in dnsreports.service dnsreports-collector.service dnsreports-collector.timer; do
    pct push "$CTID" "$TMP/$unit" "/etc/systemd/system/$unit"
  done
  # Resolve lease from inside the container; do not guess a DHCP address.
  IP=''
  info 'Aguardando endereco DHCP da LAN...'
  for _ in $(seq 1 25); do
    IP="$(pct exec "$CTID" -- ip -4 -o addr show dev eth0 scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
    [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && break
    IP=''; sleep 2
  done
  [[ -n "$IP" ]] || fail 'LXC criado, mas nao recebeu IP DHCP. Verifique Services > DHCP Server e a bridge vmbr0.'
  ok "Endereco recebido: $IP"
  cat >"$TMP/openssl.cnf" <<CERT
[req]
distinguished_name=dn
x509_extensions=v3_req
prompt=no
[dn]
CN=${IP}
[v3_req]
subjectAltName=IP:${IP}
CERT
  pct push "$CTID" "$TMP/openssl.cnf" /root/adguard-reports-openssl.cnf
  pct exec "$CTID" -- mkdir -p /etc/nginx/ssl
  pct exec "$CTID" -- openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout /etc/nginx/ssl/dnsreports.key -out /etc/nginx/ssl/dnsreports.crt \
    -config /root/adguard-reports-openssl.cnf -extensions v3_req >/dev/null 2>&1
  pct exec "$CTID" -- chmod 600 /etc/nginx/ssl/dnsreports.key
  cat >"$TMP/dnsreports.nginx" <<'ADGUARD_REPORTS_NGINX'
server {
    listen 80 default_server;
    server_name _;
    allow 127.0.0.1;
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    allow 192.168.0.0/16;
    deny all;
    return 301 https://$host$request_uri;
}
server {
    listen 443 ssl default_server;
    server_name _;
    ssl_certificate /etc/nginx/ssl/dnsreports.crt;
    ssl_certificate_key /etc/nginx/ssl/dnsreports.key;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_tickets off;
    allow 127.0.0.1;
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    allow 192.168.0.0/16;
    deny all;
    client_max_body_size 1m;
    location / {
        proxy_pass http://127.0.0.1:8088;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 130s;
    }
}
ADGUARD_REPORTS_NGINX
  pct push "$CTID" "$TMP/dnsreports.nginx" /etc/nginx/sites-available/dnsreports
  pct exec "$CTID" -- bash -c 'rm -f /etc/nginx/sites-enabled/default; ln -sfn /etc/nginx/sites-available/dnsreports /etc/nginx/sites-enabled/dnsreports'
else
  IP="$(pct exec "$CTID" -- sh -c 'ip -4 -o addr show eth0 scope global | head -n1' | awk '{print $4}' | cut -d/ -f1)"
fi
info 'Iniciando os servicos do painel...'
pct exec "$CTID" -- systemctl daemon-reload
pct exec "$CTID" -- systemctl enable dnsreports.service dnsreports-collector.timer nginx >/dev/null
pct exec "$CTID" -- systemctl reset-failed dnsreports.service || true
pct exec "$CTID" -- systemctl restart dnsreports.service
pct exec "$CTID" -- systemctl start dnsreports-collector.timer
# nginx enable --now alone does NOT reload updated site; explicit restart required.
pct exec "$CTID" -- nginx -t
pct exec "$CTID" -- systemctl restart nginx
sleep 3
pct exec "$CTID" -- systemctl is-active --quiet dnsreports.service || fail "Painel falhou: pct exec $CTID -- journalctl -u dnsreports -n 50"
pct exec "$CTID" -- systemctl is-active --quiet nginx.service || fail "Nginx falhou: pct exec $CTID -- journalctl -u nginx -n 50"
pct exec "$CTID" -- curl -kfsS -o /dev/null "https://${IP}/login" || fail 'HTTPS nao respondeu no container.'
ok 'Gunicorn, HTTPS e Nginx ativos.'
info 'Iniciando coletor automatico (a cada 2 minutos)...'
pct exec "$CTID" -- systemctl start --no-block dnsreports-collector.service || true
printf '\n%b============================================================%b\n' "$GREEN" "$RESET"
ok 'AdGuard DNS Reports | Relatorios de Consultas DNS pronto!'
printf '  🌐  Painel:     https://%s/\n' "$IP"
printf '  👤  Usuarios:   https://%s/usuarios (somente administrador)\n' "$IP"
printf '  📁  Exportar:   CSV | Excel | PDF (filtros no painel)\n'
printf '  🗓️   Retencao:   90 dias (espaco no disco deve ser monitorado)\n'
printf '  🔁  Coleta:     a cada 2 minutos (limites de historico do AdGuard se aplicam)\n'
printf '  🆔  Container:  %s\n' "$CTID"
printf '  🛡️   AdGuard:    %s\n' "$(pct exec "$CTID" -- python3 -c 'import json; print(json.load(open("/opt/dnsreports/config.json"))["adguard_url"])')"
printf '\n  Obs.: HTTPS autoassinado; pode mostrar aviso de certificado.\n'
printf '  Redes liberadas no Nginx (nova instalacao): RFC1918 e loopback\n'
printf '  Registros DNS nao comprovam navegacao ou uso de aplicativos.\n'
printf '  Coletor: pct exec %s -- journalctl -u dnsreports-collector -n 40\n' "$CTID"
printf '%b============================================================%b\n\n' "$GREEN" "$RESET"
