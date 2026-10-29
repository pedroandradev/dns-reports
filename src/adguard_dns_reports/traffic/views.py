"""Authenticated traffic reports and administrator-only integration settings."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, g, render_template, request, session

from ..config import config
from ..database import create_schema, db_connect
from ..security import require_csrf, token
from .client import NtopClient, NtopError
from .settings import load_settings, save_settings, validate

bp = Blueprint("traffic", __name__)


def human_bytes(value):
    if value is None:
        return "—"
    number = float(value)
    for unit in ("B", "kB", "MB", "GB", "TB", "PB"):
        if abs(number) < 1000 or unit == "PB":
            return f"{number:.2f} {unit}".replace(".", ",")
        number /= 1000


def human_time(value):
    if not value:
        return "—"
    zone = ZoneInfo(config().get("timezone", "Etc/UTC"))
    dt = datetime.fromtimestamp(value, timezone.utc) if isinstance(value, (int, float)) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.astimezone(zone).strftime("%d/%m/%Y %H:%M:%S")


def filters():
    period = request.args.get("period", "7")
    days = int(period) if period in ("1", "7", "30", "90") else 7
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    zone = ZoneInfo(config().get("timezone", "Etc/UTC"))
    try:
        if request.args.get("start"):
            start = datetime.strptime(request.args["start"], "%Y-%m-%d").replace(tzinfo=zone).astimezone(timezone.utc)
        if request.args.get("end"):
            end = (datetime.strptime(request.args["end"], "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=zone).astimezone(timezone.utc)
        if start >= end or end - start > timedelta(days=366):
            raise ValueError()
    except ValueError:
        abort(400, "Intervalo de datas inválido.")
    selected = {
        "client": request.args.get("client", "").strip()[:160],
        "application": request.args.get("application", "").strip()[:160],
        "location": request.args.get("location", "").strip()[:80],
        "scope": "lan" if request.args.get("scope") == "lan" else "internet",
        "period": days,
    }
    return int(start.timestamp()), int(end.timestamp()), selected


def usage_where(start, end, selected, scope=None):
    scope = scope or ("lan_app" if selected["scope"] == "lan" and selected["application"] else selected["scope"])
    clauses = ["sample_start >= ?", "sample_end <= ?", "scope=?"]
    values = [start, end, scope]
    for key, field in (("client", "ip"), ("location", "location")):
        if selected[key]:
            clauses.append(field + "=?")
            values.append(selected[key])
    if selected["application"]:
        clauses.append("application LIKE ?")
        values.append("%" + selected["application"] + "%")
    return " AND ".join(clauses), values


@bp.get("/trafego")
def dashboard():
    start, end, selected = filters()
    conn = db_connect()
    create_schema(conn)
    try:
        settings = load_settings(conn)
        where, values = usage_where(start, end, selected)
        summary = conn.execute("SELECT COALESCE(SUM(total),0) total, SUM(sent) sent,"
                               "SUM(received) received,COUNT(DISTINCT ip || '@' || mac) devices,"
                               "COUNT(*) samples FROM traffic_usage WHERE " + where, values).fetchone()
        clients = conn.execute("SELECT ip,mac,MAX(hostname) hostname,MAX(location) location,"
                               "SUM(total) total FROM traffic_usage WHERE " + where +
                               " GROUP BY ip,mac ORDER BY total DESC LIMIT 20", values).fetchall()
        locations = conn.execute("SELECT location,SUM(total) total FROM traffic_usage WHERE " + where +
                                 " GROUP BY location ORDER BY total DESC", values).fetchall()
        app_where, app_values = usage_where(start, end, selected,
                                           "lan_app" if selected["scope"] == "lan" else "internet")
        applications = conn.execute("SELECT application,SUM(total) total FROM traffic_usage WHERE " + app_where +
                                    " GROUP BY application ORDER BY total DESC LIMIT 20", app_values).fetchall()
        connection_clauses = ["observed_at>=?", "observed_at<=?"]
        connection_values = [start, end]
        if selected["scope"] == "internet":
            connection_clauses.append("scope='internet'")
        for key, field in (("client", "ip"), ("location", "location")):
            if selected[key]:
                connection_clauses.append(field + "=?")
                connection_values.append(selected[key])
        if selected["application"]:
            connection_clauses.append("application LIKE ?")
            connection_values.append("%" + selected["application"] + "%")
        connections = conn.execute("SELECT * FROM traffic_connections WHERE " +
                                   " AND ".join(connection_clauses) +
                                   " ORDER BY observed_at DESC LIMIT 100", connection_values).fetchall()
        meta = dict(conn.execute("SELECT key,value FROM metadata WHERE key LIKE 'ntopng_%'"))
        choices = conn.execute("SELECT DISTINCT ip FROM traffic_usage ORDER BY ip").fetchall()
        location_choices = conn.execute("SELECT DISTINCT location FROM traffic_usage ORDER BY location").fetchall()
    finally:
        conn.close()
    return render_template("traffic.html", selected=selected, summary=summary, clients=clients,
                           applications=applications, locations=locations, connections=connections,
                           meta=meta, enabled=settings["enabled"], choices=choices,
                           location_choices=location_choices, fmt=human_bytes, dt=human_time,
                           args=request.args, csrf=token(), username=session.get("username", ""))


@bp.route("/integracoes/ntopng", methods=["GET", "POST"])
def integration():
    if not getattr(g, "is_admin", False):
        abort(403)
    conn = db_connect()
    create_schema(conn)
    error, success = "", ""
    try:
        settings = load_settings(conn)
        if request.method == "POST":
            require_csrf()
            try:
                candidate = validate(request.form, settings)
                if request.form.get("action") == "test":
                    if not candidate["url"] or not candidate["username"] or not candidate["password"]:
                        raise ValueError("Informe URL e credenciais para testar.")
                    client = NtopClient(candidate)
                    try:
                        interfaces = client.get("ntopng/interfaces.lua")
                        if not isinstance(interfaces, list) or not any(
                                isinstance(item, dict) and str(item.get("ifid")) == str(candidate["ifid"]) for item in interfaces):
                            raise ValueError("Interface não encontrada na resposta do ntopng.")
                    finally:
                        client.close()
                    save_settings(conn, candidate)
                    settings = candidate
                    success = "Conexão e interface verificadas; configuração salva."
                else:
                    save_settings(conn, candidate)
                    settings = candidate
                    success = "Configuração salva. A coleta usa a nova configuração no próximo ciclo."
            except (ValueError, NtopError) as exc:
                error = str(exc)
        # Password is deliberately absent from the template context.
        view = {key: value for key, value in settings.items() if key != "password"}
        view["local_networks"] = "\n".join(settings["local_networks"])
        view["locations"] = "\n".join(f'{item["name"]} = {item["network"]}' for item in settings["locations"])
        has_password = bool(settings["password"])
    finally:
        conn.close()
    return render_template("ntopng_settings.html", settings=view, has_password=has_password,
                           error=error, success=success, csrf=token(), username=session.get("username", ""))


@bp.get("/trafego/export/<format_name>")
def export(format_name):
    if format_name not in ("csv", "xlsx", "pdf"):
        abort(404)
    from .exports import export_usage
    start, end, selected = filters()
    where, values = usage_where(start, end, selected)
    conn = db_connect()
    create_schema(conn)
    try:
        limit = 1000 if format_name == "pdf" else 100000
        rows = conn.execute("SELECT * FROM traffic_usage WHERE " + where +
                            " ORDER BY sample_end DESC LIMIT ?", [*values, limit]).fetchall()
    finally:
        conn.close()
    return export_usage(rows, format_name, selected["scope"])
