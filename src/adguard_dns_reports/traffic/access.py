"""Destination labels, observed connection lifecycle and access grouping."""
from collections import defaultdict
import json

from ..classification import classify

from .names import GENERIC, TECHNICAL, application_name, reported_domain
from ..dns_evidence import MAX_CANDIDATES
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


def destination(application, remote_name, remote_ip, port, protocol, name_source="", dns_names=None):
    """Specific nDPI > flow hostname > client DNS inference > reported host > IP."""
    raw = str(application or "Unknown")[:160]
    candidate = application_name(raw)
    if candidate.casefold() not in GENERIC | TECHNICAL:
        label = ALIASES.get(candidate.casefold(), candidate)
        return label, "service:" + label.casefold(), "nDPI"
    technical = candidate.casefold() in TECHNICAL or str(protocol).casefold() in TECHNICAL
    if technical:
        label = candidate if candidate.casefold() in TECHNICAL else str(protocol)
        return "Protocolo técnico (" + label + ")", "technical:" + label.casefold(), "Protocolo; não identifica site"
    domain = reported_domain(remote_name)
    dns_names = dns_names or []
    if name_source != "flow" and dns_names:
        if len(dns_names) > 1:
            return "Não identificado (" + raw + ")", f"ip:{remote_ip}:{port}:{protocol}", "DNS ambíguo (vários domínios)"
        domain = dns_names[0]
        name_source = "dns"
    if domain:
        label, _ = classify(domain)
        recognized = label != "Não identificado"
        label = label if recognized else domain
        evidence = ("Correlação DNS (inferência)" if name_source == "dns" else
                    "Nome no fluxo ntopng (inferência do serviço)" if name_source == "flow" and recognized else
                    "Nome no fluxo ntopng" if name_source == "flow" else
                    "Domínio reportado (inferência)" if recognized else "Nome de destino reportado")
        return label, ("service:" + label.casefold() if recognized else "domain:" + domain), evidence
    if candidate.casefold() in {"tooearly", "too_early", "pending"}:
        return "Identificação pendente (" + raw + ")", f"ip:{remote_ip}:{port}:{protocol}", "Classificação provisória do ntopng"
    return "Não identificado (" + raw + ")", f"ip:{remote_ip}:{port}:{protocol}", "IP e porta"


def saved_candidates(value):
    try:
        values = json.loads(value or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return sorted({reported_domain(item) for item in values if reported_domain(item)})[:MAX_CANDIDATES + 1]


def identification_kind(service_key, service):
    if service_key.startswith(("service:", "domain:")):
        return "identified"
    if service_key.startswith("technical:"):
        return "technical"
    if service.startswith("Identificação pendente"):
        return "pending"
    return "unidentified"


def observe(conn, record, source, now, dns_names=None):
    key, uid, device, scope, app, first, last, total, rip, name, port, protocol = record
    name_source = device.get("name_source", "reported")
    old = conn.execute("SELECT remote_name,name_source,dns_candidates,application FROM traffic_connections WHERE uid=?",
                       (uid,)).fetchone()
    if (old and application_name(app).casefold() in GENERIC
            and application_name(old["application"]).casefold() not in GENERIC | TECHNICAL):
        app = old["application"]
    # Keep a per-flow hostname when later samples omit it. Never copy names between IPs/flows.
    if old and old["name_source"] == "flow" and name_source != "flow":
        name, name_source = old["remote_name"], "flow"
    elif old and not reported_domain(name) and reported_domain(old["remote_name"]):
        name, name_source = old["remote_name"], old["name_source"]
    names = sorted(set(dns_names or []) | set(saved_candidates(old["dns_candidates"]) if old else []))
    names = names[:MAX_CANDIDATES + 1]
    service, service_key, evidence = destination(app, name, rip, port, protocol, name_source, names)
    conn.execute("""INSERT INTO traffic_connections
        (uid,source,first_seen,last_seen,observed_at,ip,mac,hostname,vlan,location,
         remote_ip,remote_name,remote_port,protocol,application,bytes_seen,scope,
         access_service,access_key,access_evidence,flow_state,first_observed,last_checked,
         name_source,dns_candidates)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'active',?,?,?,?)
        ON CONFLICT(uid) DO UPDATE SET last_seen=MAX(last_seen,excluded.last_seen),
        observed_at=MAX(observed_at,excluded.observed_at),application=excluded.application,
        bytes_seen=MAX(bytes_seen,excluded.bytes_seen),remote_name=excluded.remote_name,
        name_source=excluded.name_source,dns_candidates=excluded.dns_candidates,
        mac=excluded.mac,hostname=excluded.hostname,location=excluded.location,
        access_service=excluded.access_service,access_key=excluded.access_key,
        access_evidence=excluded.access_evidence,flow_state='active',
        first_observed=COALESCE(first_observed,excluded.first_observed),
        last_checked=excluded.last_checked,missing_since=NULL,missing_polls=0""",
        (uid, source, first, last, now, device["ip"], device["mac"], device["hostname"],
         device["vlan"], device["location"], rip, name, port, protocol, app, total, scope,
         service, service_key, evidence, now, now, name_source, json.dumps(names)))
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
            row["application"], row["remote_name"], row["remote_ip"], row["remote_port"], row["protocol"],
            row.get("name_source", ""), saved_candidates(row.get("dns_candidates")))
        row["dns_names"] = saved_candidates(row.get("dns_candidates"))
        row["kind"] = identification_kind(service_key, service)
        row.update(service=service, service_key=service_key, evidence=evidence)
        row["state"] = presented_state(row, settings, meta, now)
        identity = (row["source"], row["ip"], row["mac"], row["vlan"], service_key, row["kind"], row["scope"])
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
            dns_names = sorted({name for item in members for name in item["dns_names"]})[:MAX_CANDIDATES + 1]
            if latest["kind"] == "technical":
                diagnostic = "Este protocolo não identifica um site ou aplicativo específico."
            elif latest["kind"] == "pending":
                diagnostic = "O ntopng ainda não classificou este fluxo."
            elif latest["kind"] == "unidentified" and dns_names:
                diagnostic = "Vários domínios DNS possíveis; não há evidência para escolher um."
            elif latest["kind"] == "unidentified" and all(
                    item["bytes_seen"] <= 200 and item["first_seen"] == item["last_seen"] for item in members):
                diagnostic = "Poucos bytes e duração zero: pode ser apenas sinalização, sem nome de destino capturado."
            elif latest["kind"] == "unidentified":
                diagnostic = "Sem nome de destino disponível nesta coleta; abra os detalhes para conferir IP e porta."
            else:
                diagnostic = ""
            result.append({
                "ip": latest["ip"], "mac": latest["mac"], "hostname": latest["hostname"],
                "location": latest["location"], "service": latest["service"], "scope": latest["scope"],
                "kind": latest["kind"], "diagnostic": diagnostic, "dns_names": dns_names,
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
    kind_priority = {"identified": 0, "unidentified": 1, "pending": 2, "technical": 3}
    return sorted(result, key=lambda row: (priority[row["state"]], kind_priority[row["kind"]],
                                          -row["bytes"], -row["last_observed"]))
