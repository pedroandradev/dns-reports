"""Compact history preserves totals, days, permissions and transactional safety."""
import csv
import io
import time

import pytest

from test_app import client, login
from test_traffic import configured
from test_access_report import api_at, collect


def seed_dns(conn, uid, epoch, domain="example.test", blocked=0):
    from adguard_dns_reports.history import iso
    conn.execute("INSERT INTO queries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 (uid, iso(epoch), "10.77.0.10", "PC-TESTE", domain, "Example",
                  "Teste", "A", "NOERROR", blocked, "FilteredBlackList" if blocked else ""))


def dns_total(conn):
    return conn.execute("SELECT COALESCE(SUM(hits),0) FROM dns_report").fetchone()[0]


def ended(conn, now):
    conn.execute("UPDATE traffic_connections SET flow_state='ended',last_checked=?,missing_since=?",
                 (now - 1800, now - 1900))


def test_dns_consolidates_repeated_events_once_and_preserves_blocked_counts(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_dns
    now = int(time.time())
    conn = db_connect()
    for i in range(10):
        seed_dns(conn, str(i), now - 5*86400 + i, blocked=i%2)
    seed_dns(conn, "recent", now - 10)
    conn.commit()
    archive_dns(conn, now)
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dns_daily").fetchone()[0] == 2
    assert dns_total(conn) == 11
    assert conn.execute("SELECT SUM(blocked*hits) FROM dns_report").fetchone()[0] == 5
    archive_dns(conn, now)
    assert dns_total(conn) == 11
    conn.commit()
    conn.close()
    c, password = configured
    login(c, "owner", password)
    html = c.get("/dns?period=90").get_data(as_text=True)
    assert "Resumo diário" in html and "Quantidade" in html
    exported = c.get("/export/csv?period=90").get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(exported.lstrip("\ufeff")), delimiter=";"))
    assert sum(int(row[rows[0].index("Quantidade")]) for row in rows[1:]) == 11


def test_dns_archive_rollback_keeps_individual_events(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_dns
    now = int(time.time())
    conn = db_connect()
    seed_dns(conn, "before", now-5*86400)
    conn.commit()
    conn.execute("BEGIN")
    archive_dns(conn, now)
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM dns_daily").fetchone()[0] == 0
    assert not conn.execute("SELECT 1 FROM metadata WHERE key='dns_archived_before'").fetchone()
    conn.close()


def test_dns_archive_expires_oldest_after_90_days(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_dns
    now = int(time.time())
    conn = db_connect()
    seed_dns(conn, "keep", now - 89*86400)
    seed_dns(conn, "expire", now - 91*86400)
    archive_dns(conn, now)
    assert dns_total(conn) == 1
    archive_dns(conn, now + 2*86400)
    assert dns_total(conn) == 0
    conn.close()


def test_access_compaction_preserves_bytes_and_parallel_minutes_across_days(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_accesses, daily_access
    now = int(time.time())
    day = now//86400*86400
    first, last = day - 3*86400 + 23*3600+1800, day - 2*86400+3600
    api = api_at(first, last, 2000, identifier="one")
    flows = api.flows()
    flows += api_at(first+900, last-1800, 1000, identifier="two").flows()
    api.flows = lambda: flows
    collect(api, now)
    conn = db_connect()
    ended(conn, now)
    conn.execute("UPDATE traffic_connections SET observed_at=last_seen")
    conn.commit()
    archive_accesses(conn, now)
    rows = conn.execute("SELECT * FROM access_daily ORDER BY day").fetchall()
    assert len(rows) == 2
    assert sum(row["bytes_seen"] for row in rows) == 3000
    assert [row["flow_count"] for row in rows] == [2,2]
    assert sum(daily_access(row)["duration"] for row in rows) == 90*60
    assert sum(len(row["minutes"]) for row in rows) < 100
    assert conn.execute("SELECT COUNT(*) FROM traffic_connections").fetchone()[0] == 0
    archive_accesses(conn, now)
    assert conn.execute("SELECT SUM(bytes_seen) FROM access_daily").fetchone()[0] == 3000
    conn.commit()
    conn.close()
    c, password = configured
    login(c, "owner", password)
    html = c.get("/acessos?period=90").get_data(as_text=True)
    assert "Resumo de" in html and "Netflix" in html and "resolução de um minuto" in html
    for fmt in ("csv","xlsx","pdf"):
        assert c.get("/acessos/export/"+fmt+"?period=90").status_code == 200


def test_detail_target_consolidates_instead_of_discarding_history(configured, monkeypatch):
    from adguard_dns_reports import history
    from adguard_dns_reports.database import db_connect
    now = int(time.time())
    monkeypatch.setattr(history, "RAW_FLOW_TARGET", 2)
    for i in range(5):
        collect(api_at(now-7200,now-3600,1000,identifier=str(i)), now-3600)
    conn = db_connect()
    ended(conn, now)
    conn.commit()
    history.archive_accesses(conn, now)
    assert conn.execute("SELECT COUNT(*) FROM traffic_connections").fetchone()[0] == 2
    assert conn.execute("SELECT SUM(flow_count) FROM access_daily").fetchone()[0] == 3
    assert conn.execute("SELECT SUM(bytes_seen) FROM access_daily").fetchone()[0] == 3000
    conn.close()


def test_active_and_unsettled_flows_are_never_removed_to_satisfy_detail_target(configured, monkeypatch):
    from adguard_dns_reports import history
    from adguard_dns_reports.database import db_connect
    now = int(time.time())
    monkeypatch.setattr(history, "RAW_FLOW_TARGET", 1)
    for i in range(3):
        collect(api_at(now-7200,now-10,1000,identifier=str(i)), now-10)
    conn = db_connect()
    conn.execute("UPDATE traffic_connections SET flow_state='ended',last_checked=? WHERE uid=(SELECT uid FROM traffic_connections LIMIT 1)",
                 (now-60,))
    conn.commit()
    history.archive_accesses(conn, now)
    assert conn.execute("SELECT COUNT(*) FROM traffic_connections").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM access_daily").fetchone()[0] == 0
    conn.close()


def test_minutes_union_across_separate_compaction_batches(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_accesses, unpack_minutes
    now = int(time.time())
    first = (now//86400 - 4)*86400+3600
    collect(api_at(first,first+1800,1000,identifier="one"),now-2*86400)
    conn = db_connect()
    ended(conn,now)
    conn.commit()
    archive_accesses(conn,now)
    conn.commit()
    conn.close()
    collect(api_at(first+900,first+2700,2000,identifier="two"),now-2*86400)
    conn = db_connect()
    ended(conn,now)
    conn.commit()
    archive_accesses(conn,now)
    row = conn.execute("SELECT * FROM access_daily").fetchone()
    assert unpack_minutes(row["minutes"]).bit_count()*60 == 45*60
    assert row["bytes_seen"] == 3000 and row["flow_count"] == 2
    conn.close()


def test_zero_duration_is_not_invented_as_one_minute(configured):
    from adguard_dns_reports.history import minute_mask, pack_minutes, unpack_minutes
    assert minute_mask(100,100,0) == 0
    assert unpack_minutes(pack_minutes(0)) == 0


def test_archived_dns_watermark_prevents_reimport_of_sealed_events(configured, monkeypatch):
    from adguard_dns_reports import collector
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_dns, iso
    now = int(time.time())
    conn = db_connect()
    seed_dns(conn, "old", now-5*86400)
    archive_dns(conn,now)
    conn.commit()
    conn.close()
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data":[{"client":"10.77.0.10","time":iso(now-5*86400),
                             "question":{"name":"example.test","type":"A"},"status":"NOERROR"}]}
    class Session:
        def get(self,*args,**kwargs):
            return Response()
    monkeypatch.setattr(collector.requests,"Session",Session)
    monkeypatch.setattr(collector,"lookup_hostname",lambda *args: pytest.fail("Sealed event was imported"))
    collector.collect()
    conn = db_connect()
    assert dns_total(conn) == 1
    conn.close()


def test_offline_compaction_reclaims_space_and_can_run_twice(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import compact
    now=int(time.time())
    conn=db_connect()
    for i in range(100):
        seed_dns(conn,str(i),now-5*86400+i)
    conn.commit()
    conn.close()
    compact(vacuum=True)
    compact(vacuum=True)
    conn=db_connect()
    assert dns_total(conn)==100
    assert conn.execute("SELECT COUNT(*) FROM queries").fetchone()[0]==0
    assert conn.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
    conn.close()


def test_dns_daily_summary_matches_a_single_calendar_day_in_panel_timezone(configured):
    import json
    from datetime import datetime,timezone
    from adguard_dns_reports.config import CONFIG
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.history import archive_dns
    settings=json.loads(CONFIG.read_text())
    settings["timezone"]="America/Sao_Paulo"
    CONFIG.write_text(json.dumps(settings))
    from adguard_dns_reports import reports
    from zoneinfo import ZoneInfo
    reports.ZONE=ZoneInfo("America/Sao_Paulo")
    now=int(time.time())
    old_day=(datetime.fromtimestamp(now,timezone.utc).date()).toordinal()-5
    date=datetime.fromordinal(old_day).strftime("%Y-%m-%d")
    epoch=int(datetime.fromisoformat(date+"T12:00:00-03:00").timestamp())
    conn=db_connect()
    seed_dns(conn,"local-day",epoch)
    archive_dns(conn,now)
    conn.commit()
    conn.close()
    c,password=configured
    login(c,"owner",password)
    exported=c.get("/export/csv?start="+date+"&end="+date).get_data(as_text=True)
    rows=list(csv.reader(io.StringIO(exported.lstrip("﻿")),delimiter=";"))
    assert len(rows)==2
    assert rows[1][rows[0].index("Quantidade")]=="1"
