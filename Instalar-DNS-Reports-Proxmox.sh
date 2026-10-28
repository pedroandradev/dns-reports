#!/usr/bin/env bash
# DNS Reports installer - Proxmox VE 8/9; Debian 13 LXC
# Creates a NEW unprivileged container. It never changes AdGuard/pfSense settings.
set -Eeuo pipefail
umask 077
BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; RESET='\033[0m'
log(){ printf "${BLUE}[*]${RESET} %s\n" "$*"; }
ok(){ printf "${GREEN}[OK]${RESET} %s\n" "$*"; }
error(){ printf "${RED}[ERRO]${RESET} %s\n" "$*" >&2; exit 1; }
if (( EUID != 0 )); then error 'Execute como root no shell do Proxmox.'; fi
for cmd in pct pvesh pveam pvesm python3 openssl; do command -v "$cmd" >/dev/null || error "Comando ausente: $cmd"; done
printf '\n===========================================================\n'
printf ' DNS Reports - instalacao completa para Proxmox VE\n'
printf ' LXC Debian 13 | Panel HTTPS | API AdGuard | XLSX/PDF/CSV\n'
printf '===========================================================\n\n'

DEFAULT_CTID="$(pvesh get /cluster/nextid 2>/dev/null | tr -dc '0-9')"
[[ "$DEFAULT_CTID" =~ ^[0-9]+$ ]] || error 'Nao foi possivel obter o proximo ID de container.'
read -rp "ID do novo container [${DEFAULT_CTID}]: " CTID
CTID="${CTID:-$DEFAULT_CTID}"
[[ "$CTID" =~ ^[0-9]+$ ]] || error 'CTID invalido.'
[[ ! -e "/etc/pve/lxc/${CTID}.conf" ]] || error "O CT ${CTID} ja existe. Nao alterei nada."
read -rp 'IP estatico do painel [172.20.0.252]: ' IP
IP="${IP:-172.20.0.252}"
read -rp 'Prefixo CIDR [16]: ' CIDR
CIDR="${CIDR:-16}"
read -rp 'Gateway [172.20.0.254]: ' GW
GW="${GW:-172.20.0.254}"
read -rp 'Bridge [vmbr0]: ' BRIDGE
BRIDGE="${BRIDGE:-vmbr0}"
read -rp 'Armazenamento dos discos [local-lvm]: ' STORAGE
STORAGE="${STORAGE:-local-lvm}"
read -rp 'Armazenamento dos templates [local]: ' TEMPLATE_STORAGE
TEMPLATE_STORAGE="${TEMPLATE_STORAGE:-local}"
read -rp 'AdGuard Home URL [http://172.20.0.253]: ' ADGUARD_URL
ADGUARD_URL="${ADGUARD_URL:-http://172.20.0.253}"
read -rp 'Usuario administrador do AdGuard [admin]: ' ADGUARD_USER
ADGUARD_USER="${ADGUARD_USER:-admin}"
read -rsp 'Senha do painel AdGuard (nao aparece): ' ADGUARD_PASS; echo
[[ -n "$ADGUARD_PASS" ]] || error 'Senha do AdGuard obrigatoria.'
read -rp 'Usuario de login do novo painel [admin]: ' PANEL_USER
PANEL_USER="${PANEL_USER:-admin}"
read -rsp 'CRIE uma senha para o novo painel (minimo 10 caracteres): ' PANEL_PASS; echo
(( ${#PANEL_PASS} >= 10 )) || error 'Senha do painel deve ter 10 caracteres ou mais.'
read -rsp 'Repita a senha do novo painel: ' PANEL_PASS_2; echo
[[ "$PANEL_PASS" == "$PANEL_PASS_2" ]] || error 'Senhas do novo painel nao conferem.'
unset PANEL_PASS_2
read -rp 'Dias de retencao no novo painel [90]: ' RETENTION
RETENTION="${RETENTION:-90}"
[[ "$RETENTION" =~ ^[0-9]+$ ]] && (( RETENTION>=1 && RETENTION<=3650 )) || error 'Retencao invalida (1-3650).'

python3 - "$IP" "$CIDR" "$GW" "$ADGUARD_URL" <<'PYCHECK'
import ipaddress,sys
from urllib.parse import urlsplit
ip,prefix,gw,api=sys.argv[1:]
ipaddress.ip_interface(f'{ip}/{prefix}'); ipaddress.ip_address(gw)
p=urlsplit(api)
if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.path not in ('','/'):
 raise ValueError('URL do AdGuard deve ter apenas origem, ex. http://172.20.0.253 ou http://172.20.0.253:3000')
PYCHECK
ip -o link show "$BRIDGE" >/dev/null 2>&1 || error "Bridge ${BRIDGE} nao encontrada."
pvesm status | awk 'NR>1{print $1}' | grep -Fx "$STORAGE" >/dev/null || error "Storage ${STORAGE} nao encontrado."
pvesm status | awk 'NR>1{print $1}' | grep -Fx "$TEMPLATE_STORAGE" >/dev/null || error "Template storage ${TEMPLATE_STORAGE} nao encontrado."
if ping -c 1 -W 1 "$IP" >/dev/null 2>&1; then error "O IP ${IP} respondeu a ping. Verifique se esta ocupado e escolha outro."; fi

log 'Localizando template Debian 13...'
TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
if [[ -z "$TEMPLATE" ]]; then
  TEMPLATE="$(pveam available --section system | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
  [[ -n "$TEMPLATE" ]] || error 'Template Debian 13 nao encontrado nos repositorios Proxmox.'
  pveam update
  pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
fi
ok "Template: ${TEMPLATE}"

TMP="$(mktemp -d /root/dnsreports-install.XXXXXXXX)"
trap 'if [[ -n "${TMP:-}" ]]; then rm -rf "$TMP"; fi' EXIT
log 'Gerando arquivos internos e credenciais...'
export ADGUARD_URL ADGUARD_USER ADGUARD_PASS PANEL_USER PANEL_PASS RETENTION
python3 - "$TMP/config.json" <<'PYCONF'
import os,sys,json,secrets,hashlib
salt=secrets.token_bytes(16)
rounds=380000
key=hashlib.pbkdf2_hmac('sha256',os.environ['PANEL_PASS'].encode(),salt,rounds)
data={
 'adguard_url':os.environ['ADGUARD_URL'].rstrip('/'),
 'adguard_username':os.environ['ADGUARD_USER'],
 'adguard_password':os.environ['ADGUARD_PASS'],
 'panel_username':os.environ['PANEL_USER'],
 'panel_password_hash':f'pbkdf2_sha256${rounds}${salt.hex()}${key.hex()}',
 'session_secret':secrets.token_hex(48),
 'retention_days':int(os.environ['RETENTION']),
 'initial_history_days':7,
}
with open(sys.argv[1],'w') as f: json.dump(data,f,ensure_ascii=False)
os.chmod(sys.argv[1],0o600)
PYCONF
unset ADGUARD_PASS PANEL_PASS

log "Criando container ${CTID} em ${IP}/${CIDR}..."
pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
  --hostname dns-reports --ostype debian --unprivileged 1 \
  --rootfs "${STORAGE}:8" --cores 2 --memory 1024 --swap 512 \
  --net0 "name=eth0,bridge=${BRIDGE},ip=${IP}/${CIDR},gw=${GW}" \
  --nameserver "$GW" --timezone America/Sao_Paulo --onboot 1 \
  --start 1
ok 'Container criado.'
log 'Instalando pacotes Debian e ambiente Python (pode levar alguns minutos)...'
pct exec "$CTID" -- bash -c 'export DEBIAN_FRONTEND=noninteractive; apt-get update && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates nginx openssl && apt-get clean'
pct exec "$CTID" -- mkdir -p /opt/dnsreports/templates /opt/dnsreports/static /opt/dnsreports/data
pct exec "$CTID" -- python3 -m venv /opt/dnsreports/venv
pct exec "$CTID" -- /opt/dnsreports/venv/bin/pip install --no-cache-dir 'Flask>=3.1,<4' 'requests>=2.31,<3' 'openpyxl>=3.1,<4' 'reportlab>=4,<5' 'gunicorn>=23,<25'
log 'Transferindo backend, frontend, coletor e configuracao...'
cat >"$TMP/app.py" <<'DNSREPORTS_ASSET_APP_PY'
"""Read-only, internal DNS reporting panel."""
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
from html import escape

from flask import Flask, Response, abort, redirect, render_template, request, send_file, session, url_for
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
ZONE = ZoneInfo('America/Sao_Paulo')
ATTEMPTS = {}
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
    if not session.get('logged'):
        return redirect(url_for('login', next=request.full_path if request.path != '/' else '/'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('logged'):
        return redirect('/')
    error = ''
    if request.method == 'POST':
        require_csrf()
        ip = request.remote_addr or 'unknown'
        history = [ts for ts in ATTEMPTS.get(ip, []) if ts > time.time() - 600]
        if len(history) >= 8:
            error = 'Muitas tentativas. Aguarde 10 minutos.'
        elif request.form.get('username', '') == settings['panel_username'] and verify_password(request.form.get('password', ''), settings['panel_password_hash']):
            ATTEMPTS.pop(ip, None)
            session.clear()
            session['logged'] = True
            session.permanent = True
            token()
            return redirect('/')
        else:
            history.append(time.time())
            ATTEMPTS[ip] = history
            error = 'Usuário ou senha incorretos.'
    return render_template('login.html', error=error, csrf=token())


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
    return render_template('dashboard.html', summary=summary, services=services, clients=clients,
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
    filename = f'relatorio-dns-{stamp}.{format_name}'
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
    story = [Paragraph('DNS Reports — Consultas DNS', styles['Title']),
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

DNSREPORTS_ASSET_APP_PY
cat >"$TMP/core.py" <<'DNSREPORTS_ASSET_CORE_PY'
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
      CREATE TABLE IF NOT EXISTS client_names (
        ip TEXT PRIMARY KEY,
        hostname TEXT NOT NULL,
        last_checked INTEGER NOT NULL
      );
    ''')
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

DNSREPORTS_ASSET_CORE_PY
cat >"$TMP/login.html" <<'DNSREPORTS_ASSET_LOGIN_HTML'
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Entrar · DNS Reports</title><link rel="stylesheet" href="{{ url_for('static',filename='style.css') }}"></head>
<body class="login-body"><main class="login-card"><div class="brand"><div class="brand-mark">◈</div><div><strong>DNS Reports</strong><small>Monitoramento e relatórios DNS</small></div></div><h1>Acesso ao painel</h1><p class="muted">Entre com suas credenciais para consultar o histórico de sua rede.</p>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post"><input type="hidden" name="csrf" value="{{csrf}}"><label for="username">Usuário</label><input autofocus id="username" name="username" autocomplete="username" required><label for="password">Senha</label><input id="password" type="password" name="password" autocomplete="current-password" required><button class="btn primary full">Entrar</button></form><p class="login-foot">Painel somente de leitura · acesso restrito à rede interna</p></main></body></html>

DNSREPORTS_ASSET_LOGIN_HTML
cat >"$TMP/dashboard.html" <<'DNSREPORTS_ASSET_DASHBOARD_HTML'
<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DNS Reports · Painel</title><link rel="stylesheet" href="{{ url_for('static',filename='style.css') }}"></head><body>
<div class="shell"><aside class="sidebar"><div class="brand"><div class="brand-mark">◈</div><div><strong>DNS Reports</strong><small>Inteligência de rede</small></div></div><div class="aside-section">PAINEL</div><a class="nav active" href="/">▦ <span>Visão geral</span></a><a class="nav" href="#consultas">⌕ <span>Consultas DNS</span></a><a class="nav" href="#servicos">◫ <span>Serviços e domínios</span></a><div class="sidebar-bottom"><span class="status-dot"></span> Servidor interno <div class="aside-note">Relatórios de consultas DNS<br>Não equivale a histórico de navegação.</div></div></aside>
<main class="main"><div class="topbar"><span>Visão geral <span class="crumb">/ Relatórios</span></span><form method="post" action="/logout"><input type="hidden" name="csrf" value="{{csrf}}"><button class="btn subtle" type="submit">Sair ↗</button></form></div><div class="content"><div class="pageheading"><div><div class="eyebrow">MONITORAMENTO DNS</div><h1>Relatórios de acesso</h1><p class="muted">Consultas realizadas pelos dispositivos monitorados pelo AdGuard Home.</p></div><div class="updated">Última coleta<br><strong>{{ dt(meta.get('last_success','')) if meta.get('last_success') else 'Aguardando primeira coleta' }}</strong></div></div>
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
<footer>DNS Reports · Histórico de consultas DNS, não de navegação ou uso comprovado de aplicativos. Os nomes de serviços são estimativas. {% if meta.get('last_import_complete') == 'false' %}<br><strong>Aviso:</strong> uma coleta atingiu o limite de páginas; o histórico antigo pode estar incompleto.{% endif %}</footer>
</div></main></div>
</body></html>

DNSREPORTS_ASSET_DASHBOARD_HTML
cat >"$TMP/style.css" <<'DNSREPORTS_ASSET_STYLE_CSS'
:root{font-family:Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#1b2840;background:#f4f7fb;font-synthesis:none}*{box-sizing:border-box}body{margin:0}a{color:inherit;text-decoration:none}button,input,select{font:inherit}button{cursor:pointer}.shell{display:flex;min-height:100vh}.sidebar{width:244px;flex-shrink:0;background:#11223b;color:#dce6f4;padding:26px 18px;display:flex;flex-direction:column;gap:8px}.brand{display:flex;align-items:center;gap:12px}.brand-mark{display:grid;place-items:center;background:#2874ed;color:white;width:42px;height:42px;border-radius:12px;font-size:29px}.brand strong{display:block;font-size:17px;letter-spacing:-.4px}.brand small{font-size:11px;opacity:.65;display:block;margin-top:4px}.aside-section{font-size:10px;font-weight:800;letter-spacing:1.7px;color:#728baa;margin:46px 10px 12px}.nav{padding:12px 13px;border-radius:8px;font-size:13px;display:flex;gap:12px;align-items:center;color:#a9bed7}.nav.active{background:#25446b;color:white;font-weight:650}.nav:hover{background:#213b60}.sidebar-bottom{margin-top:auto;font-size:12px;color:#c1d2e5;padding:15px 8px;border-top:1px solid #344661}.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#56d6aa;margin-right:7px}.aside-note{color:#849bb7;line-height:1.6;font-size:11px;margin-top:10px}.main{min-width:0;flex:1}.topbar{display:flex;align-items:center;justify-content:space-between;padding:17px 35px;background:white;border-bottom:1px solid #e6ebf2;font-size:12px;font-weight:650}.crumb{color:#96a4b7;font-weight:400}.content{max-width:1560px;padding:34px 35px 50px;margin:auto}.pageheading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:25px}.eyebrow{font-size:10px;letter-spacing:1.6px;color:#3375d6;font-weight:800}h1{font-size:28px;letter-spacing:-.9px;margin:8px 0}h2{font-size:15px;letter-spacing:-.3px;margin:0}.muted{color:#7989a0;font-size:13px;line-height:1.7;margin:5px 0}.updated{text-align:right;font-size:11px;line-height:1.8;color:#8c9ab0}.updated strong{color:#344860;font-size:12px}.filters{border:1px solid #e2e8f1;background:white;border-radius:12px;padding:19px;display:flex;flex-wrap:wrap;gap:12px 14px;align-items:end;box-shadow:0 4px 15px rgba(30,56,89,.025)}.filter-group{flex:1 1 170px;min-width:155px}.filter-group.search{flex:1.5 1 200px}.filter-group label,.date-options label,.login-card label{font-size:11px;font-weight:750;display:block;margin-bottom:8px;color:#5a6d85}input,select{width:100%;height:39px;padding:0 11px;border-radius:7px;border:1px solid #dce4ee;background:#fff;color:#33445b;font-size:12px;outline:0}input:focus,select:focus{border-color:#3b82f6;box-shadow:0 0 0 3px #e7f0ff}.filter-actions{display:flex;gap:7px;align-items:center}.btn{height:38px;padding:0 16px;border:1px solid transparent;border-radius:7px;font-size:12px;font-weight:700;display:inline-flex;align-items:center;justify-content:center;white-space:nowrap}.btn.primary{background:#2269da;color:white}.btn.primary:hover{background:#175bbd}.btn.subtle{color:#556781;background:#f2f5f9}.btn.outline{background:#fff;color:#336ab4;border-color:#d9e4f3}.btn.outline:hover{background:#edf5ff}.date-options{flex:1 1 100%;display:flex;gap:12px;border-top:1px dashed #e4eaf1;padding-top:12px}.date-options>div{max-width:195px;flex:1}.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:15px;margin:20px 0}.stat{padding:21px 22px;background:#fff;border:1px solid #e2e8f1;border-radius:12px}.stat-label{font-size:12px;font-weight:650;color:#74849a}.stat strong{display:block;font-size:31px;letter-spacing:-1.1px;margin:12px 0 7px}.stat small{font-size:11px;color:#9aa9ba}.two-col{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}.panel{border:1px solid #e2e8f1;background:#fff;border-radius:12px;padding:23px;margin-bottom:18px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:22px}.panel-head p{font-size:12px;color:#8b9aae;margin:6px 0 0}.panel-head.wrap{flex-wrap:wrap}.barrow{margin:13px 0}.bar-top{display:flex;align-items:center;justify-content:space-between;gap:8px;font-size:12px;color:#4b5e76;margin-bottom:8px}.bar-top span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.bar-top b{font-size:11px;color:#72839b}.track{background:#edf2f8;height:7px;border-radius:9px;overflow:hidden}.bar{background:#397bed;height:100%;border-radius:9px}.bar.alt{background:#21a59a}.domains-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:3px 25px}.domain{display:flex;align-items:center;gap:13px;border-bottom:1px solid #eef2f7;padding:12px 0;font-size:12px;min-width:0}.ranking{font-size:11px;background:#eaf1fc;color:#4b79b7;border-radius:6px;display:grid;place-items:center;width:23px;height:23px;flex-shrink:0}.domain-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;color:#4a607a}.domain strong{color:#263f5a}.actions{display:flex;gap:7px;flex-wrap:wrap}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:12px;min-width:890px}th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.5px;color:#8b9bb0;padding:13px 9px;border-bottom:1px solid #e5ecf3}td{padding:13px 9px;border-bottom:1px solid #f0f2f5;color:#586c83}tbody tr:hover{background:#f8fbfe}td.hostname{font-weight:700;color:#34506d}td.domaintext{color:#3168aa;word-break:break-word}.nowrap{white-space:nowrap}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}.pill{padding:5px 8px;border-radius:5px;font-weight:700;font-size:10px;white-space:nowrap}.pill.allowed{background:#e6f7f0;color:#238360}.pill.blocked{background:#fff0e9;color:#ad592b}footer{font-size:11px;color:#8898ac;line-height:1.6;margin-top:20px}.empty{font-size:13px;color:#95a3b4;padding:26px 0}.login-body{min-height:100vh;display:grid;place-items:center;background:linear-gradient(150deg,#10263f,#17385e)}.login-card{background:#fff;width:min(430px,calc(100% - 36px));padding:36px;border-radius:15px;box-shadow:0 25px 70px #07132670}.login-card .brand{margin-bottom:35px}.login-card .brand strong{color:#1b2c46}.login-card .brand small{color:#78899c}.login-card h1{font-size:23px}.login-card .muted{margin-bottom:24px}.login-card input{margin-bottom:17px;height:45px}.full{width:100%;height:44px;margin-top:5px}.login-foot{font-size:10px;text-align:center;color:#92a0ae;margin:24px 0 0}.error{color:#ad362c;background:#fff0ef;padding:11px;border-radius:6px;font-size:12px}@media(max-width:1150px){.sidebar{width:190px}.stats{grid-template-columns:repeat(2,1fr)}.content{padding:25px}.topbar{padding:15px 25px}}@media(max-width:750px){.shell{display:block}.sidebar{width:auto;display:block;padding:15px}.sidebar .brand{display:inline-flex}.sidebar .aside-section,.sidebar .nav,.sidebar-bottom{display:none}.stats,.two-col,.domains-grid{grid-template-columns:1fr}.content{padding:20px 14px}.pageheading{display:block}.updated{text-align:left;margin-top:10px}.filter-group{flex:1 1 100%}.date-options>div{max-width:none}.topbar{padding:12px 16px}.panel{padding:16px}}

.meter{display:block;width:100%;height:7px;border:0;border-radius:9px;overflow:hidden;appearance:none}.meter::-webkit-progress-bar{background:#edf2f8;border-radius:9px}.meter::-webkit-progress-value{background:#397bed;border-radius:9px}.meter.alt::-webkit-progress-value{background:#21a59a}.meter::-moz-progress-bar{background:#397bed;border-radius:9px}.meter.alt::-moz-progress-bar{background:#21a59a}

DNSREPORTS_ASSET_STYLE_CSS

pct push "$CTID" "$TMP/app.py" /opt/dnsreports/app.py
pct push "$CTID" "$TMP/core.py" /opt/dnsreports/core.py
pct push "$CTID" "$TMP/login.html" /opt/dnsreports/templates/login.html
pct push "$CTID" "$TMP/dashboard.html" /opt/dnsreports/templates/dashboard.html
pct push "$CTID" "$TMP/style.css" /opt/dnsreports/static/style.css
pct push "$CTID" "$TMP/config.json" /opt/dnsreports/config.json

pct exec "$CTID" -- bash -c 'id -u dnsreports >/dev/null 2>&1 || useradd --system --home-dir /opt/dnsreports --shell /usr/sbin/nologin dnsreports'
pct exec "$CTID" -- chown -R root:root /opt/dnsreports
pct exec "$CTID" -- chown dnsreports:dnsreports /opt/dnsreports/data
pct exec "$CTID" -- chown root:dnsreports /opt/dnsreports/config.json
pct exec "$CTID" -- chmod 640 /opt/dnsreports/config.json
pct exec "$CTID" -- chmod 750 /opt/dnsreports/data

cat >"$TMP/dnsreports.service" <<'UNITS'
[Unit]
Description=DNS Reports Panel (Gunicorn)
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
UNITS
cat >"$TMP/dnsreports-collector.service" <<'UNITS'
[Unit]
Description=DNS Reports - archive AdGuard Home DNS querylog
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
UNITS
cat >"$TMP/dnsreports-collector.timer" <<'UNITS'
[Unit]
Description=DNS Reports - collect querylogs every 2 minutes
[Timer]
OnBootSec=20s
OnUnitInactiveSec=120s
Persistent=true
Unit=dnsreports-collector.service
[Install]
WantedBy=timers.target
UNITS
for unit in dnsreports.service dnsreports-collector.service dnsreports-collector.timer; do
 pct push "$CTID" "$TMP/$unit" "/etc/systemd/system/$unit"
done

cat >"$TMP/openssl.cnf" <<CERT
[req]
distinguished_name=dn
x509_extensions=v3_req
prompt=no
[dn]
CN=dns-reports.local
[v3_req]
subjectAltName=IP:${IP},DNS:dns-reports.local
CERT
pct push "$CTID" "$TMP/openssl.cnf" /root/dnsreports-openssl.cnf
pct exec "$CTID" -- mkdir -p /etc/nginx/ssl
pct exec "$CTID" -- openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
  -keyout /etc/nginx/ssl/dnsreports.key -out /etc/nginx/ssl/dnsreports.crt \
  -config /root/dnsreports-openssl.cnf -extensions v3_req >/dev/null 2>&1
pct exec "$CTID" -- chmod 600 /etc/nginx/ssl/dnsreports.key
cat >"$TMP/dnsreports.nginx" <<'NGINX'
server {
    listen 80 default_server;
    server_name _;
    allow 172.20.0.0/16;
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
    allow 172.20.0.0/16;
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
NGINX
pct push "$CTID" "$TMP/dnsreports.nginx" /etc/nginx/sites-available/dnsreports
pct exec "$CTID" -- bash -c 'rm -f /etc/nginx/sites-enabled/default; ln -sfn /etc/nginx/sites-available/dnsreports /etc/nginx/sites-enabled/dnsreports; nginx -t && systemctl daemon-reload && systemctl enable --now dnsreports.service dnsreports-collector.timer && systemctl enable --now nginx'

log 'Validando servicos...'
pct exec "$CTID" -- systemctl is-active dnsreports.service >/dev/null || error 'Painel nao inicializou: pct exec CTID -- journalctl -u dnsreports -n 50'
pct exec "$CTID" -- systemctl is-active nginx >/dev/null || error 'Nginx nao iniciou.'
log 'Testando leitura autenticada da API AdGuard...'
if pct exec "$CTID" -- /opt/dnsreports/venv/bin/python -c 'import requests,json; s=json.load(open("/opt/dnsreports/config.json")); r=requests.get(s["adguard_url"]+"/control/querylog",auth=(s["adguard_username"],s["adguard_password"]),params={"limit":1},timeout=15); r.raise_for_status(); print("AdGuard API OK: ",r.status_code)' ; then
 ok 'API AdGuard respondendo com as credenciais fornecidas.'
 log 'Iniciando primeira coleta de dados (restante ocorre a cada 2 minutos)...'
 pct exec "$CTID" -- systemctl start --no-block dnsreports-collector.service || true
else
 printf '%b\n' "${RED}[ATENCAO] API do AdGuard nao respondeu. Confira login, URL e conectividade.${RESET}"
 printf 'Para corrigir: pct enter %s ; nano /opt/dnsreports/config.json ; systemctl restart dnsreports-collector.timer\n' "$CTID"
fi
printf '\n===========================================================\n'
ok "DNS Reports instalado no CT ${CTID}"
printf ' Painel:  https://%s/\n' "$IP"
printf ' Login:   %s\n' "$PANEL_USER"
printf ' Disco:   8 GB (amplie conforme volume da rede)\n'
printf ' Retencao no arquivo local: %s dias\n' "$RETENTION"
printf ' AdGuard: %s\n' "$ADGUARD_URL"
printf '\n AVISO: Certificado HTTPS autoassinado; o navegador mostrara um alerta.\n'
printf ' Somente redes 172.20.0.0/16 sao permitidas no Nginx.\n'
printf ' Nao altera DNS, DHCP, firewall nem o CT 102.\n'
printf '\n Logs da coleta: pct exec %s -- journalctl -u dnsreports-collector -n 60\n' "$CTID"
printf ' Status: pct exec %s -- systemctl status dnsreports\n' "$CTID"
printf '===========================================================\n'
