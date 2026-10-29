"""Additive schema and counter differences; the first sample is always a baseline."""


def create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS traffic_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS traffic_state(
            source TEXT NOT NULL, counter_key TEXT NOT NULL, kind TEXT NOT NULL,
            application TEXT NOT NULL, sampled_at INTEGER NOT NULL, generation TEXT NOT NULL,
            sent INTEGER NOT NULL, received INTEGER NOT NULL, total INTEGER NOT NULL,
            PRIMARY KEY(source,counter_key,kind,application));
        CREATE TABLE IF NOT EXISTS traffic_usage(
            id INTEGER PRIMARY KEY, source TEXT NOT NULL, counter_key TEXT NOT NULL,
            sample_start INTEGER NOT NULL, sample_end INTEGER NOT NULL,
            ip TEXT NOT NULL, mac TEXT NOT NULL, hostname TEXT NOT NULL, vlan INTEGER NOT NULL,
            location TEXT NOT NULL, application TEXT NOT NULL, scope TEXT NOT NULL,
            sent INTEGER, received INTEGER, total INTEGER NOT NULL, quality TEXT NOT NULL,
            UNIQUE(source,counter_key,scope,application,sample_start));
        CREATE INDEX IF NOT EXISTS idx_traffic_usage_time ON traffic_usage(sample_end);
        CREATE INDEX IF NOT EXISTS idx_traffic_usage_scope ON traffic_usage(scope,sample_end,ip);
        CREATE TABLE IF NOT EXISTS traffic_connections(
            uid TEXT PRIMARY KEY, source TEXT NOT NULL, first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL, observed_at INTEGER NOT NULL,
            ip TEXT NOT NULL, mac TEXT NOT NULL, hostname TEXT NOT NULL, vlan INTEGER NOT NULL,
            location TEXT NOT NULL, remote_ip TEXT NOT NULL, remote_name TEXT NOT NULL,
            remote_port INTEGER NOT NULL, protocol TEXT NOT NULL, application TEXT NOT NULL,
            bytes_seen INTEGER NOT NULL, scope TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_traffic_connections_time ON traffic_connections(observed_at);
    """)

    # Existing observations remain historical; never mark them active during migration.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(traffic_connections)")}
    additions = {
        "access_service": "TEXT NOT NULL DEFAULT ''", "access_key": "TEXT NOT NULL DEFAULT ''",
        "access_evidence": "TEXT NOT NULL DEFAULT ''", "flow_state": "TEXT NOT NULL DEFAULT 'legacy'",
        "first_observed": "INTEGER", "last_checked": "INTEGER",
        "missing_since": "INTEGER", "missing_polls": "INTEGER NOT NULL DEFAULT 0",
        "name_source": "TEXT NOT NULL DEFAULT ''", "dns_candidates": "TEXT NOT NULL DEFAULT '[]'",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE traffic_connections ADD COLUMN {name} {definition}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_access_state ON traffic_connections(source,flow_state)")



def metadata(conn, key, value):
    conn.execute("INSERT INTO metadata(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def counter(conn, source, key, kind, app, now, generation, sent, received, total, device):
    previous = conn.execute("SELECT * FROM traffic_state WHERE source=? AND counter_key=? "
                            "AND kind=? AND application=?", (source, key, kind, app)).fetchone()
    if previous and now <= previous["sampled_at"]:
        return
    current = (sent, received, total)
    compatible = (previous and previous["generation"] == generation and
                  all(value >= previous[field] for value, field in
                      zip(current, ("sent", "received", "total"))))
    if compatible:
        delta = tuple(value - previous[field] for value, field in
                      zip(current, ("sent", "received", "total")))
        if delta[2] > 0:
            # Hourly aggregation bounds growth on small existing containers.
            # Each delta belongs to the UTC hour in which it was read, without
            # inventing a distribution across minutes or a boundary it crossed.
            bucket = now - now % 3600
            usage_key = f'{device["ip"]}@{device["vlan"]}@{device["mac"]}@{device["location"]}'
            quality = "parcial" if kind == "internet" else "medido"
            if now - previous["sampled_at"] > 120:
                quality += "_intervalo_longo"
            conn.execute("""INSERT INTO traffic_usage
                (source,counter_key,sample_start,sample_end,ip,mac,hostname,vlan,location,
                 application,scope,sent,received,total,quality)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,counter_key,scope,application,sample_start) DO UPDATE SET
                sample_end=MAX(sample_end,excluded.sample_end),hostname=excluded.hostname,
                sent=sent+excluded.sent,received=received+excluded.received,
                total=total+excluded.total,
                quality=CASE WHEN quality LIKE '%intervalo_longo' THEN quality ELSE excluded.quality END""",
                (source, usage_key, bucket, now, device["ip"], device["mac"],
                 device["hostname"], device["vlan"], device["location"], device.get("application", app), kind,
                 delta[0] if kind == "lan" else None,
                 delta[1] if kind == "lan" else None, delta[2], quality))
    conn.execute("""INSERT INTO traffic_state
        (source,counter_key,kind,application,sampled_at,generation,sent,received,total)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(source,counter_key,kind,application)
        DO UPDATE SET sampled_at=excluded.sampled_at,generation=excluded.generation,
        sent=excluded.sent,received=excluded.received,total=excluded.total""",
        (source, key, kind, app, now, generation, *current))
