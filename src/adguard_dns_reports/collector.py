"""Collector of AdGuard Home DNS querylog."""
import hashlib
import ipaddress
import json
import socket
import time
from datetime import datetime, timezone, timedelta
import requests
from .classification import classify
from .config import config
from .database import db_connect, create_schema
from .dns_evidence import remember_answers, prune
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
            rows, answer_events = [], []
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
                    answer_events.append((entry, row))
            before = c.total_changes
            c.executemany('''INSERT OR IGNORE INTO queries
                  (uid,ts,ip,hostname,domain,service,category,qtype,status,blocked,reason)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?)''', rows)
            total_added += c.total_changes - before
            for entry, row in answer_events:
                remember_answers(c, entry, row, int(now.timestamp()))
            prune(c, int(now.timestamp()))
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
        prune(c, int(now.timestamp()))
        # Refresh hostname information for existing devices whenever a PTR first becomes available.
        c.execute('''UPDATE queries SET hostname=(SELECT hostname FROM client_names WHERE ip=queries.ip)
                     WHERE ip IN (SELECT ip FROM client_names WHERE hostname != ip)
                     AND hostname=ip''')
        c.commit()
        print(f'Consulta OK: {pages} paginas, {total_added} novos registros, completo={complete}', flush=True)
    finally:
        c.close()
    return total_added

def main():
    collect()

if __name__ == "__main__":
    main()
