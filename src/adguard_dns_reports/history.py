"""Compact daily history alongside a bounded recent event window."""
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import zlib

from .config import config

DAY = 86400
RAW_DNS_DAYS = 2
RAW_FLOW_DAYS = 1
RAW_FLOW_TARGET = 100000
SETTLE_SECONDS = 600


def days():
    return max(1, min(int(config().get("retention_days", 90)), 3660))


def iso(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def save_meta(conn, key, value):
    conn.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, str(value)))


def create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dns_daily(
            day TEXT NOT NULL,ip TEXT NOT NULL,hostname TEXT NOT NULL,domain TEXT NOT NULL,
            service TEXT NOT NULL,category TEXT NOT NULL,qtype TEXT NOT NULL,status TEXT NOT NULL,
            blocked INTEGER NOT NULL,reason TEXT NOT NULL,hits INTEGER NOT NULL,
            first_ts TEXT NOT NULL,last_ts TEXT NOT NULL,
            period_start TEXT NOT NULL,period_end TEXT NOT NULL,
            PRIMARY KEY(day,ip,hostname,domain,qtype,status,blocked,reason)) WITHOUT ROWID;
        CREATE VIEW IF NOT EXISTS dns_report AS
            SELECT uid,ts,ip,hostname,domain,service,category,qtype,status,blocked,reason,
                   1 AS hits,ts AS first_ts,ts AS last_ts,'event' AS record_type,
                   ts AS period_start,ts AS period_end FROM queries
            UNION ALL
            SELECT '',last_ts,ip,hostname,domain,service,category,qtype,status,blocked,reason,
                   hits,first_ts,last_ts,'daily',period_start,period_end FROM dns_daily;
        CREATE TABLE IF NOT EXISTS access_daily(
            day INTEGER NOT NULL,source TEXT NOT NULL,ip TEXT NOT NULL,mac TEXT NOT NULL,
            vlan INTEGER NOT NULL,hostname TEXT NOT NULL,location TEXT NOT NULL,
            service_key TEXT NOT NULL,service TEXT NOT NULL,evidence TEXT NOT NULL,
            scope TEXT NOT NULL,target_example TEXT NOT NULL,first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL,observed_at INTEGER NOT NULL,bytes_seen INTEGER NOT NULL,
            flow_count INTEGER NOT NULL,minutes BLOB NOT NULL,flow_state TEXT NOT NULL,
            missing_since INTEGER,last_checked INTEGER,
            PRIMARY KEY(day,source,ip,mac,vlan,hostname,service_key,scope)) WITHOUT ROWID;
    """)


def archive_dns(conn, now):
    """Archive closed UTC days atomically before removing their individual events."""
    zone = ZoneInfo(config().get("timezone", "Etc/UTC"))
    def local_day(value):
        return datetime.fromisoformat(value.replace("Z","+00:00")).astimezone(zone).date().isoformat()
    def day_start(value, offset=0):
        value = datetime.fromisoformat(value).replace(tzinfo=zone) + timedelta(days=offset)
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")
    conn.create_function("history_local_day",1,local_day,deterministic=True)
    conn.create_function("history_day_start",1,lambda value:day_start(value),deterministic=True)
    conn.create_function("history_day_end",1,lambda value:day_start(value,1),deterministic=True)
    local_boundary = (datetime.fromtimestamp(now,zone).date()-timedelta(days=RAW_DNS_DAYS)).isoformat()
    boundary = day_start(local_boundary)
    cutoff = iso(now - days() * DAY)
    conn.execute("""INSERT INTO dns_daily
        SELECT history_local_day(ts),ip,MAX(hostname),domain,MAX(service),MAX(category),
               qtype,status,blocked,reason,COUNT(*),MIN(ts),MAX(ts),
               history_day_start(history_local_day(ts)),history_day_end(history_local_day(ts))
        FROM queries WHERE ts < ? AND ts >= ?
        GROUP BY history_local_day(ts),ip,hostname,domain,qtype,status,blocked,reason
        ON CONFLICT(day,ip,hostname,domain,qtype,status,blocked,reason) DO UPDATE SET
        hits=hits+excluded.hits,first_ts=MIN(first_ts,excluded.first_ts),
        last_ts=MAX(last_ts,excluded.last_ts),hostname=excluded.hostname,
        period_start=MIN(period_start,excluded.period_start),period_end=MAX(period_end,excluded.period_end)""", (boundary, cutoff))
    conn.execute("DELETE FROM queries WHERE ts < ?", (boundary,))
    conn.execute("DELETE FROM dns_daily WHERE day<=? AND last_ts < ?", (local_day(cutoff),cutoff))
    old = conn.execute("SELECT value FROM metadata WHERE key='dns_archived_before'").fetchone()
    save_meta(conn, "dns_archived_before", max(boundary, old[0] if old else ""))
    save_meta(conn, "history_days", days())
    save_meta(conn, "dns_raw_days", RAW_DNS_DAYS)


def minute_mask(first, last, day):
    left, right = max(first, day), min(last, day + DAY)
    if right <= left:
        return 0
    start, end = int((left - day) // 60), int((right - day + 59) // 60)
    return ((1 << max(0, end - start)) - 1) << start


class MinuteUnion:
    def __init__(self):
        self.mask = 0

    def step(self, first, last, day):
        self.mask |= minute_mask(first, last, day)

    def finalize(self):
        return pack_minutes(self.mask)


def pack_minutes(mask):
    return b"Z" + zlib.compress(mask.to_bytes(180, "little"))


def unpack_minutes(value):
    if not value:
        return 0
    raw = zlib.decompress(value[1:]) if value[:1] == b"Z" else value
    return int.from_bytes(raw, "little")


def merge_minutes(left, right):
    return pack_minutes(unpack_minutes(left) | unpack_minutes(right))


def archive_accesses(conn, now):
    """Only settled ended/legacy flows are consolidated. Active flows stay detailed."""
    cutoff = now - days() * DAY
    conn.execute("DELETE FROM traffic_usage WHERE sample_end < ?", (cutoff,))
    conn.execute("DELETE FROM access_daily WHERE day < ?", ((cutoff//DAY)*DAY,))
    conn.execute("DELETE FROM traffic_connections WHERE observed_at < ?", (cutoff,))
    conn.create_aggregate("history_minutes", 3, MinuteUnion)
    conn.create_function("history_merge_minutes", 2, merge_minutes)
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS history_candidates(uid TEXT PRIMARY KEY)")
    conn.execute("DELETE FROM history_candidates")
    from .traffic.settings import load_settings
    settings = load_settings(conn)
    active_source = settings["source"] if settings["enabled"] else ""
    eligible = ("(flow_state='legacy' OR (flow_state='ended' AND last_checked<=?)"
                " OR (source<>? AND observed_at<=?))")
    eligible_values = (now-SETTLE_SECONDS, active_source, now-SETTLE_SECONDS)
    conn.execute("INSERT OR IGNORE INTO history_candidates SELECT uid FROM traffic_connections WHERE " +
                 eligible + " AND observed_at < ?", (*eligible_values, now - RAW_FLOW_DAYS * DAY))
    count = conn.execute("SELECT COUNT(*) FROM traffic_connections").fetchone()[0]
    excess = max(0, count - RAW_FLOW_TARGET)
    if excess:
        conn.execute("INSERT OR IGNORE INTO history_candidates SELECT uid FROM traffic_connections WHERE " +
                     eligible + " ORDER BY observed_at LIMIT ?", (*eligible_values, excess))
    # Resolve labels for older installations before grouping, without changing their state.
    from .traffic.access import destination, saved_candidates
    cursor = conn.execute("""SELECT c.uid,c.application,c.remote_name,c.remote_ip,c.remote_port,
                             c.protocol,c.name_source,c.dns_candidates FROM traffic_connections c
                             JOIN history_candidates h ON h.uid=c.uid""")
    for row in cursor:
        label, key, evidence = destination(row[1], row[2], row[3], row[4], row[5],
                                           row[6], saved_candidates(row[7]))
        conn.execute("UPDATE traffic_connections SET access_service=?,access_key=?,access_evidence=? WHERE uid=?",
                     (label, key, evidence, row[0]))
    conn.execute("""CREATE TEMP TABLE IF NOT EXISTS history_segments(
        uid TEXT NOT NULL,day INTEGER NOT NULL,first_seen INTEGER NOT NULL,
        last_seen INTEGER NOT NULL,bytes_seen INTEGER NOT NULL,PRIMARY KEY(uid,day))""")
    conn.execute("DELETE FROM history_segments")
    conn.execute("""INSERT INTO history_segments
        WITH RECURSIVE spans(uid,day,last_day) AS (
            SELECT c.uid,MAX((c.first_seen/86400)*86400,?),(c.last_seen/86400)*86400
            FROM traffic_connections c JOIN history_candidates h ON h.uid=c.uid
            WHERE c.last_seen>=?
            UNION ALL SELECT uid,day+86400,last_day FROM spans WHERE day<last_day)
        SELECT c.uid,s.day,MAX(c.first_seen,s.day),MIN(c.last_seen,s.day+86400),
               CASE WHEN s.day=s.last_day THEN c.bytes_seen ELSE 0 END
        FROM spans s JOIN traffic_connections c ON c.uid=s.uid""",
        ((cutoff//DAY)*DAY,cutoff))
    # Stale observations with no activity inside the retained window still keep
    # their destination/count, without inventing minutes of recent activity.
    conn.execute("""INSERT OR IGNORE INTO history_segments
        SELECT c.uid,(c.observed_at/86400)*86400,c.observed_at,c.observed_at,c.bytes_seen
        FROM traffic_connections c JOIN history_candidates h ON h.uid=c.uid
        WHERE c.last_seen<?""", (cutoff,))
    conn.execute("""INSERT INTO access_daily
        SELECT s.day,c.source,c.ip,c.mac,c.vlan,MAX(c.hostname),
               MAX(c.location),c.access_key,MAX(c.access_service),
               GROUP_CONCAT(DISTINCT c.access_evidence),c.scope,MIN(c.remote_name),
               MIN(s.first_seen),MAX(s.last_seen),MAX(c.observed_at),SUM(s.bytes_seen),COUNT(*),
               history_minutes(s.first_seen,s.last_seen,s.day),
               CASE WHEN MIN(c.flow_state)='ended' AND MAX(c.flow_state)='ended'
                    THEN 'ended' ELSE 'legacy' END,MAX(c.missing_since),MAX(c.last_checked)
        FROM traffic_connections c JOIN history_segments s ON s.uid=c.uid
        GROUP BY s.day,c.source,c.ip,c.mac,c.vlan,c.hostname,c.access_key,c.scope
        ON CONFLICT(day,source,ip,mac,vlan,hostname,service_key,scope) DO UPDATE SET
        hostname=excluded.hostname,location=excluded.location,
        first_seen=MIN(first_seen,excluded.first_seen),last_seen=MAX(last_seen,excluded.last_seen),
        observed_at=MAX(observed_at,excluded.observed_at),bytes_seen=bytes_seen+excluded.bytes_seen,
        flow_count=flow_count+excluded.flow_count,
        minutes=history_merge_minutes(minutes,excluded.minutes),
        evidence=CASE WHEN evidence=excluded.evidence THEN evidence ELSE 'Identificação combinada (pode conter inferências)' END,
        flow_state=CASE WHEN flow_state='ended' AND excluded.flow_state='ended' THEN 'ended' ELSE 'legacy' END,
        missing_since=MAX(missing_since,excluded.missing_since),
        last_checked=MAX(last_checked,excluded.last_checked)""")
    removed = conn.execute("SELECT COUNT(*) FROM history_candidates").fetchone()[0]
    conn.execute("DELETE FROM traffic_connections WHERE uid IN (SELECT uid FROM history_candidates)")
    save_meta(conn, "ntopng_connections_compacted_last", removed)
    save_meta(conn, "ntopng_connection_days", RAW_FLOW_DAYS)
    save_meta(conn, "ntopng_connection_limit", RAW_FLOW_TARGET)
    save_meta(conn, "history_days", days())


def daily_access(row):
    from .traffic.access import identification_kind, STATE_LABELS
    item = dict(row)
    state = item["flow_state"]
    return {
        "ip": item["ip"], "mac": item["mac"], "hostname": item["hostname"], "location": item["location"],
        "service": item["service"], "scope": item["scope"], "evidence": item["evidence"],
        "kind": identification_kind(item["service_key"], item["service"]),
        "started_at": item["first_seen"], "last_activity": item["last_seen"],
        "last_observed": item["observed_at"], "duration": unpack_minutes(item["minutes"]).bit_count()*60,
        "bytes": item["bytes_seen"], "flow_count": item["flow_count"], "state": state,
        "state_label": STATE_LABELS[state], "missing_since": item["missing_since"] or 0,
        "checked_at": item["last_checked"] or 0, "destinations": [item["target_example"]], "flows": [],
        "diagnostic": "Resumo diário; duração aproximada com resolução de um minuto, sem somar conexões paralelas.",
        "dns_names": [], "record_type": "daily",
        "day_label": datetime.fromtimestamp(item["day"], timezone.utc).strftime("%d/%m/%Y"),
    }


def compact(vacuum=False):
    """Run during maintenance with web/collectors stopped; keep the file reusable."""
    import time
    from .database import db_connect, create_schema
    conn = db_connect()
    original_mode = "wal"
    try:
        create_schema(conn)
        original_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        if vacuum:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        archive_dns(conn, now)
        archive_accesses(conn, now)
        conn.commit()
        if vacuum:
            conn.execute("VACUUM")
        print("Histórico compacto: retenção de " + str(days()) + " dias; consolidação concluída.", flush=True)
    finally:
        if conn.in_transaction:
            conn.rollback()
        if vacuum and original_mode == "wal":
            conn.execute("PRAGMA journal_mode=WAL")
        conn.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Consolidar histórico em manutenção, com os serviços pausados.")
    parser.add_argument("--vacuum", action="store_true", help="Reorganizar o banco e devolver espaço ao disco.")
    args = parser.parse_args()
    compact(vacuum=args.vacuum)
