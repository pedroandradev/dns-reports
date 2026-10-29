"""Bounded DNS answer cache for explicit, per-client destination inference."""
import ipaddress
from datetime import datetime

from .traffic.names import reported_domain, web_flow

MAX_AGE = 86400
MAX_ROWS = 100000
MAX_CANDIDATES = 16


def create_table(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dns_resolutions(
            uid TEXT NOT NULL,client_ip TEXT NOT NULL,answer_ip TEXT NOT NULL,
            domain TEXT NOT NULL,queried_at INTEGER NOT NULL,expires_at INTEGER NOT NULL,
            PRIMARY KEY(uid,answer_ip));
        CREATE INDEX IF NOT EXISTS idx_dns_resolution_match
            ON dns_resolutions(client_ip,answer_ip,queried_at,expires_at);
        CREATE INDEX IF NOT EXISTS idx_dns_resolution_time ON dns_resolutions(queried_at);
    """)


def remember_answers(conn, entry, query, now):
    """Only actual, successful A/AAAA answers; never a question alone or blocked query."""
    uid, timestamp, client_ip, _, domain, _, _, _, status, blocked, _ = query
    domain = reported_domain(domain)
    if blocked or status != "NOERROR" or not domain:
        return
    try:
        client_ip = str(ipaddress.ip_address(client_ip))
        queried = int(datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp())
    except (ValueError, TypeError):
        return
    if not now - MAX_AGE <= queried <= now + 5:
        return
    answers = entry.get("answer")
    if not isinstance(answers, list):
        return
    for answer in answers[:64]:
        if not isinstance(answer, dict) or answer.get("type") not in ("A", "AAAA"):
            continue
        ttl = answer.get("ttl")
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl <= 0:
            continue
        try:
            address = ipaddress.ip_address(answer.get("value", ""))
        except (ValueError, TypeError):
            continue
        if address.version != (4 if answer["type"] == "A" else 6):
            continue
        if address.is_unspecified or address.is_loopback or address.is_multicast:
            continue
        conn.execute("""INSERT OR IGNORE INTO dns_resolutions
            (uid,client_ip,answer_ip,domain,queried_at,expires_at) VALUES(?,?,?,?,?,?)""",
            (uid, client_ip, str(address), domain, queried, queried + min(ttl, MAX_AGE)))


def prune(conn, now):
    conn.execute("DELETE FROM dns_resolutions WHERE queried_at < ?", (now - MAX_AGE,))
    conn.execute("""DELETE FROM dns_resolutions WHERE rowid IN
        (SELECT rowid FROM dns_resolutions ORDER BY queried_at DESC LIMIT -1 OFFSET ?)""",
        (MAX_ROWS,))


def candidates(conn, record):
    _, _, device, _, app, first, _, _, remote_ip, _, port, _ = record
    if not web_flow(app, port):
        return []
    rows = conn.execute("""SELECT DISTINCT domain FROM dns_resolutions
        WHERE client_ip=? AND answer_ip=? AND queried_at<=? AND expires_at>?
        ORDER BY domain LIMIT ?""",
        (device["ip"], remote_ip, first, first, MAX_CANDIDATES + 1)).fetchall()
    return [row[0] for row in rows]
