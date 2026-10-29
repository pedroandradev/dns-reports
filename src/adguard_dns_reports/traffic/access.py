"""Destination labels, observed connection lifecycle and access grouping."""
from collections import defaultdict
import ipaddress
import re

from ..classification import classify

GENERIC = {"tls", "ssl", "quic", "http", "https", "tcp", "udp", "unknown", "unidentified"}
ALIASES = {
    "ms_onedrive": "Microsoft OneDrive", "windowsupdate": "Windows Update",
    "microsoft365": "Microsoft 365", "teams": "Microsoft Teams", "outlook": "Outlook",
    "googleservices": "Serviços Google", "googledrive": "Google Drive",
    "googledocs": "Google Docs", "youtube": "YouTube", "netflix": "Netflix",
}
FRESH_SECONDS = 180
STATE_LABELS = {
    "active": "Ativa na última coleta", "pending": "Aguardando confirmação",
    "ended": "Encerrada (estimativa)", "unknown": "Sem confirmação recente",
    "paused": "Acompanhamento pausado", "legacy": "Histórico sem acompanhamento",
}


def reported_domain(value):
    name = str(value or "").strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return ""
    except ValueError:
        pass
    if len(name) > 253 or "." not in name:
        return ""
    labels = name.split(".")
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        return ""
    return name


def destination(application, remote_name, remote_ip, port, protocol):
    """Prefer nDPI, then reported domain, then IP; preserve the evidence type."""
    raw = str(application or "Unknown")[:160]
    parts = raw.split(".")
    while len(parts) > 1 and parts[0].casefold() in GENERIC:
        parts.pop(0)
    candidate = ".".join(parts)
    if candidate.casefold() not in GENERIC:
        label = ALIASES.get(candidate.casefold(), candidate)
        return label, "service:" + label.casefold(), "nDPI"
    domain = reported_domain(remote_name)
    if domain:
        label, _ = classify(domain)
        if label != "Não identificado":
            return label, "service:" + label.casefold(), "Domínio reportado (inferência)"
        return domain, "domain:" + domain, "Nome de destino reportado"
    return "Não identificado (" + raw + ")", f"ip:{remote_ip}:{port}:{protocol}", "IP e porta"


def observe(conn, record, source, now):
    key, uid, device, scope, app, first, last, total, rip, name, port, protocol = record
    service, service_key, evidence = destination(app, name, rip, port, protocol)
    conn.execute("""INSERT INTO traffic_connections
        (uid,source,first_seen,last_seen,observed_at,ip,mac,hostname,vlan,location,
         remote_ip,remote_name,remote_port,protocol,application,bytes_seen,scope,
         access_service,access_key,access_evidence,flow_state,first_observed,last_checked)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?)
        ON CONFLICT(uid) DO UPDATE SET last_seen=MAX(last_seen,excluded.last_seen),
        observed_at=MAX(observed_at,excluded.observed_at),application=excluded.application,
        bytes_seen=MAX(bytes_seen,excluded.bytes_seen),remote_name=excluded.remote_name,
        mac=excluded.mac,hostname=excluded.hostname,location=excluded.location,
        access_service=excluded.access_service,access_key=excluded.access_key,
        access_evidence=excluded.access_evidence,flow_state='active',
        first_observed=COALESCE(first_observed,excluded.first_observed),
        last_checked=excluded.last_checked,missing_since=NULL,missing_polls=0""",
        (uid, source, first, last, now, device["ip"], device["mac"], device["hostname"],
         device["vlan"], device["location"], rip, name, port, protocol, app, total, scope,
         service, service_key, evidence, now, now))
    return uid


def complete_snapshot(conn, source, now, seen, idle_seconds):
    """Only a complete, valid flow snapshot can provide evidence of absence."""
    previous = conn.execute("SELECT value FROM metadata WHERE key='ntopng_flow_last_success'").fetchone()
    uninterrupted = previous and 0 <= now - int(previous[0]) <= FRESH_SECONDS
    rows = conn.execute("""SELECT uid,missing_since,missing_polls,last_checked
        FROM traffic_connections WHERE source=? AND flow_state IN ('active','pending')""",
        (source,)).fetchall()
    for row in rows:
        if row["uid"] in seen or (row["last_checked"] is not None and now <= row["last_checked"]):
            continue
        since = row["missing_since"] if uninterrupted and row["missing_since"] is not None else now
        polls = row["missing_polls"] + 1 if uninterrupted else 1
        ended = polls >= 2 and now - since >= idle_seconds
        conn.execute("""UPDATE traffic_connections SET flow_state=?,missing_since=?,
                     missing_polls=?,last_checked=? WHERE uid=?""",
                     ("ended" if ended else "pending", since, polls, now, row["uid"]))


def presented_state(row, settings, meta, now):
    saved = row.get("flow_state", "legacy")
    if saved in ("legacy", "ended"):
        return saved
    if not settings["enabled"] or row["source"] != settings["source"]:
        return "paused"
    try:
        fresh = 0 <= now - int(meta.get("ntopng_flow_last_success", 0)) <= FRESH_SECONDS
    except (ValueError, TypeError):
        fresh = False
    if not fresh or meta.get("ntopng_flow_error"):
        return "unknown"
    return saved


def union_seconds(intervals):
    """Parallel connections do not multiply the duration."""
    total = 0
    start = end = None
    for left, right in sorted(intervals):
        if start is None:
            start, end = left, right
        elif left <= end:
            end = max(end, right)
        else:
            total += max(0, end - start)
            start, end = left, right
    return total + (max(0, end - start) if start is not None else 0)


def duration_label(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}h {minutes:02d}min {seconds:02d}s" if hours else f"{minutes}min {seconds:02d}s"


def group_accesses(rows, settings, meta, now):
    """Group close/overlapping flows per equipment and service into observed episodes."""
    buckets = defaultdict(list)
    gap = int(settings.get("session_idle_seconds", 120))
    for raw in rows:
        row = dict(raw)
        service, service_key, evidence = destination(
            row["application"], row["remote_name"], row["remote_ip"], row["remote_port"], row["protocol"])
        row.update(service=service, service_key=service_key, evidence=evidence)
        row["state"] = presented_state(row, settings, meta, now)
        identity = (row["source"], row["ip"], row["mac"], row["vlan"], service_key, row["scope"])
        buckets[identity].append(row)
    result = []
    for flows in buckets.values():
        episodes, current, boundary = [], [], 0
        for row in sorted(flows, key=lambda item: (item["first_seen"], item["uid"])):
            if current and row["first_seen"] > boundary + gap:
                episodes.append(current)
                current = []
                boundary = 0
            current.append(row)
            boundary = max(boundary, row["last_seen"], row["observed_at"])
        if current:
            episodes.append(current)
        for members in episodes:
            latest = max(members, key=lambda item: item["observed_at"])
            states = {item["state"] for item in members}
            state = next((value for value in ("active", "unknown", "paused", "pending", "legacy", "ended")
                          if value in states), "legacy")
            domains = sorted({item["remote_name"] if reported_domain(item["remote_name"])
                              else item["remote_ip"] for item in members})
            result.append({
                "ip": latest["ip"], "mac": latest["mac"], "hostname": latest["hostname"],
                "location": latest["location"], "service": latest["service"], "scope": latest["scope"],
                "evidence": " / ".join(sorted({item["evidence"] for item in members})),
                "started_at": min(item["first_seen"] for item in members),
                "last_activity": max(item["last_seen"] for item in members),
                "last_observed": max(item["observed_at"] for item in members),
                "duration": union_seconds([(item["first_seen"], item["last_seen"]) for item in members]),
                "bytes": sum(item["bytes_seen"] for item in members),
                "flow_count": len(members), "state": state, "state_label": STATE_LABELS[state],
                "missing_since": max((item["missing_since"] or 0 for item in members), default=0)
                                 if state == "ended" else 0,
                "checked_at": max((item["last_checked"] or 0 for item in members), default=0),
                "destinations": domains, "flows": sorted(members, key=lambda item: item["last_seen"], reverse=True),
            })
    priority = {"active": 0, "pending": 1, "unknown": 2, "paused": 3, "legacy": 4, "ended": 5}
    return sorted(result, key=lambda row: (priority[row["state"]], -row["last_observed"]))
