"""The primary, read-only computer -> destination report."""
import time

from flask import Blueprint, abort, render_template, request, session

from ..database import create_schema, db_connect
from ..security import token
from .access import group_accesses, duration_label
from .settings import load_settings
from .views import filters, human_bytes, human_time

bp = Blueprint("accesses", __name__)
MAX_FLOWS = 10000
PAGE_SIZE = 25


def report_data():
    start, end, selected = filters()
    selected.update(
        state=request.args.get("state", "all"),
        kind=request.args.get("kind", "all"),
        computer=request.args.get("computer", "").strip()[:160],
        refresh=request.args.get("refresh", "1") == "1",
    )
    clauses = ["first_seen <= ?", "(last_seen >= ? OR observed_at >= ?)"]
    values = [end, start, start]
    if selected["scope"] == "internet":
        clauses.append("scope='internet'")
    if selected["client"]:
        clauses.append("ip=?")
        values.append(selected["client"])
    if selected["computer"]:
        clauses.append("(ip LIKE ? OR hostname LIKE ? OR mac LIKE ?)")
        values.extend(["%" + selected["computer"] + "%"] * 3)
    if selected["location"]:
        clauses.append("location=?")
        values.append(selected["location"])
    conn = db_connect()
    create_schema(conn)
    try:
        rows = conn.execute("SELECT * FROM traffic_connections WHERE " + " AND ".join(clauses) +
                            " ORDER BY observed_at DESC LIMIT ?", [*values, MAX_FLOWS + 1]).fetchall()
        settings = load_settings(conn)
        meta = dict(conn.execute("SELECT key,value FROM metadata WHERE key LIKE 'ntopng_%'"))
        locations = [row[0] for row in conn.execute("SELECT DISTINCT location FROM traffic_connections ORDER BY location")]
    finally:
        conn.close()
    truncated = len(rows) > MAX_FLOWS
    grouped = group_accesses(rows[:MAX_FLOWS], settings, meta, int(time.time()))
    if selected["application"]:
        search = selected["application"].casefold()
        grouped = [row for row in grouped if search in row["service"].casefold()
                   or any(search in target.casefold() for target in row["destinations"] + row["dns_names"])
                   or any(search in flow["application"].casefold() or search in flow["remote_ip"]
                          for flow in row["flows"])]
    if selected["kind"] in ("identified", "unidentified", "technical", "pending"):
        grouped = [row for row in grouped if row["kind"] == selected["kind"]]
    if selected["state"] == "unknown":
        grouped = [row for row in grouped if row["state"] in ("unknown", "paused", "legacy")]
    elif selected["state"] in ("active", "pending", "ended"):
        grouped = [row for row in grouped if row["state"] == selected["state"]]
    return grouped, selected, meta, settings["enabled"], locations, truncated


@bp.get("/")
@bp.get("/acessos")
def dashboard():
    grouped, selected, meta, enabled, locations, truncated = report_data()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    pages = max(1, (len(grouped) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    summary = {
        "identified": sum(row["kind"] == "identified" for row in grouped),
        "unidentified": sum(row["kind"] == "unidentified" for row in grouped),
        "pending": sum(row["kind"] == "pending" for row in grouped),
        "technical": sum(row["kind"] == "technical" for row in grouped),
        "accesses": len(grouped), "active": sum(row["state"] == "active" for row in grouped),
        "ended": sum(row["state"] == "ended" for row in grouped),
        "devices": len({(row["ip"], row["mac"]) for row in grouped}),
    }
    def page_url(number):
        params = request.args.to_dict()
        params["page"] = number
        from urllib.parse import urlencode
        return "/acessos?" + urlencode(params)
    return render_template(
        "accesses.html", accesses=grouped[(page-1)*PAGE_SIZE:page*PAGE_SIZE], selected=selected,
        meta=meta, enabled=enabled, locations=locations, truncated=truncated, summary=summary,
        page=page, pages=pages, page_url=page_url, fmt=human_bytes, dt=human_time,
        duration=duration_label, args=request.args, csrf=token(), username=session.get("username", ""))


@bp.get("/acessos/export/<format_name>")
def export(format_name):
    if format_name not in ("csv", "xlsx", "pdf"):
        abort(404)
    from .access_exports import export_accesses
    grouped, selected, meta, enabled, locations, truncated = report_data()
    if truncated:
        abort(400, "O filtro excede 10.000 fluxos. Refine computador ou período antes de exportar.")
    return export_accesses(grouped[:1000] if format_name == "pdf" else grouped, format_name)
