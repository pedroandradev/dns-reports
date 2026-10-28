"""Dashboard and export routes (CSV, XLSX and PDF)."""
import csv
import io
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo
from flask import Blueprint, Response, abort, render_template, request, send_file, session
from openpyxl import Workbook
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from .config import config
from .database import db_connect, create_schema
from .security import token
bp = Blueprint("reports", __name__)
ZONE = ZoneInfo(config().get("timezone", "Etc/UTC"))
FIELDS = ['Data/Hora','Hostname','IP','Dominio','Servico provavel','Categoria','Tipo DNS','Resultado','Bloqueada','Motivo']
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


@bp.get('/')
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


@bp.get('/export/<format_name>')
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


@bp.get('/healthz')
def healthz():
    # Health check still requires login for all other URLs. nginx can check locally.
    return Response('ok', mimetype='text/plain')


