"""Observed access lifecycle, computer/service grouping and read-only roles."""
import copy
import csv
import io
import time

import pytest

from test_app import client, login
from test_traffic import configured, FakeApi


def api_at(first, last, total=1000, application="TLS.Netflix", identifier="primary"):
    api = FakeApi()
    api.host["first_seen"] = first
    flow = {"key": identifier, "hash_id": identifier, "first_seen": first, "last_seen": last,
            "bytes": total, "vlan": 0, "protocol": {"l4": "TCP", "l7": application},
            "client": {"ip": "10.77.0.10", "port": 50123},
            "server": {"ip": "93.184.216.34", "port": 443, "name": "cdn.example.test"}}
    api.flows = lambda: [copy.deepcopy(flow)]
    return api


def collect(api, now):
    from adguard_dns_reports.traffic.collector import collect as run
    return run(api, now)


def stored():
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM traffic_connections ORDER BY uid")]
    finally:
        conn.close()


def empty_api(first):
    api = api_at(first, first)
    api.flows = lambda: []
    return api


def test_first_observation_shows_equipment_service_bytes_and_duration(configured):
    c, password = configured
    now = int(time.time()) - 5
    collect(api_at(now - 7200, now, 5_000_000_000), now)
    login(c, "owner", password)
    response = c.get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for value in ("Quem está acessando o quê", "PC-TESTE", "Netflix", "5,00 GB",
                  "2h 00min 00s", "Ativa na última coleta"):
        assert value in html
    assert c.get("/dns").status_code == 200
    assert c.get("/trafego").status_code == 200


def test_complete_absence_requires_grace_multiple_polls_and_same_poll_not_twice(configured):
    start = int(time.time()) - 600
    collect(api_at(start, start + 20), start + 20)
    collect(empty_api(start), start + 50)
    row = stored()[0]
    assert row["flow_state"] == "pending" and row["missing_polls"] == 1
    collect(empty_api(start), start + 50)
    assert stored()[0]["missing_polls"] == 1
    collect(empty_api(start), start + 110)
    assert stored()[0]["flow_state"] == "pending"
    collect(empty_api(start), start + 170)
    row = stored()[0]
    assert row["flow_state"] == "ended"
    assert row["observed_at"] == start + 20
    assert row["missing_since"] == start + 50
    assert row["last_checked"] == start + 170


def test_failed_or_malformed_flow_snapshot_never_closes_connections(configured):
    from adguard_dns_reports.traffic.client import NtopError
    from adguard_dns_reports.traffic.access import presented_state
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.database import db_connect
    now = int(time.time()) - 500
    collect(api_at(now, now + 10), now + 10)
    api = empty_api(now)
    api.flows = lambda: (_ for _ in ()).throw(NtopError("Origem indisponível."))
    collect(api, now + 400)
    assert stored()[0]["flow_state"] == "active"
    conn = db_connect()
    meta = dict(conn.execute("SELECT key,value FROM metadata"))
    assert presented_state(stored()[0], load_settings(conn), meta, now + 400) == "unknown"
    conn.close()
    api = empty_api(now)
    api.flows = lambda: [{"client": {}}]
    collect(api, now + 430)
    assert stored()[0]["flow_state"] == "active"


def test_outage_does_not_count_as_confirmation_time_and_reappearance_reopens(configured):
    first = int(time.time()) - 1200
    collect(api_at(first, first + 10), first + 10)
    collect(empty_api(first), first + 40)
    collect(empty_api(first), first + 700)
    row = stored()[0]
    assert row["flow_state"] == "pending" and row["missing_since"] == first + 700
    collect(empty_api(first), first + 820)
    assert stored()[0]["flow_state"] == "ended"
    collect(api_at(first, first + 850, total=1500), first + 850)
    assert len(stored()) == 1
    assert stored()[0]["flow_state"] == "active"
    assert stored()[0]["missing_since"] is None
    assert stored()[0]["bytes_seen"] == 1500


def test_group_parallel_connections_without_multiplying_duration(configured):
    from adguard_dns_reports.traffic.access import group_accesses
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.database import db_connect
    now = int(time.time()) - 5
    api = api_at(now - 7200, now - 1800, 3_000_000_000, "TLS.Netflix", "one")
    flows = api.flows()
    second = api_at(now - 3600, now, 2_000_000_000, "QUIC.Netflix", "two").flows()[0]
    api.flows = lambda: copy.deepcopy(flows + [second])
    collect(api, now)
    conn = db_connect()
    groups = group_accesses(stored(), load_settings(conn),
                            dict(conn.execute("SELECT key,value FROM metadata")), now)
    conn.close()
    assert len(groups) == 1
    assert groups[0]["service"] == "Netflix"
    assert groups[0]["bytes"] == 5_000_000_000
    assert groups[0]["duration"] == 7200  # union, not 5400+3600
    assert groups[0]["flow_count"] == 2


def test_reclassification_updates_label_without_duplicate_flow(configured):
    first = int(time.time()) - 100
    collect(api_at(first, first + 10, application="TLS"), first + 10)
    collect(api_at(first, first + 40, total=1500, application="QUIC.YouTube"), first + 40)
    row = stored()[0]
    assert len(stored()) == 1
    assert row["access_service"] == "YouTube"
    assert row["access_evidence"] == "nDPI"
    assert row["bytes_seen"] == 1500


def test_destination_specificity_keeps_domain_inference_distinct_from_ndpi():
    from adguard_dns_reports.traffic.access import destination
    assert destination("TLS.Netflix", "cdn.example.test", "93.184.216.34", 443, "TCP") == (
        "Netflix", "service:netflix", "nDPI")
    assert destination("TLS", "www.netflix.com", "93.184.216.34", 443, "TCP")[2] == "Domínio reportado (inferência)"
    assert destination("TLS", "portal.example.test", "93.184.216.34", 443, "TCP")[0] == "portal.example.test"
    assert destination("TLS", "93.184.216.34", "93.184.216.34", 443, "TCP")[2] == "IP e porta"
    assert destination("TLS", "<script>alert(1)</script>", "93.184.216.34", 443, "TCP")[2] == "IP e porta"


def test_users_only_view_and_export_even_with_forged_role_and_valid_csrf(configured, monkeypatch):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.traffic import views
    c, password = configured
    now = int(time.time()) - 10
    collect(api_at(now - 30, now), now)
    login(c, "owner", password)
    with c.session_transaction() as session:
        csrf = session["csrf"]
    c.post("/usuarios", data={"csrf": csrf, "action": "add", "username": "viewer",
                             "password": "viewer-password-123"})
    c.post("/logout", data={"csrf": csrf})
    login(c, "viewer", "viewer-password-123")
    with c.session_transaction() as session:
        session["is_admin"] = True
        csrf = session["csrf"]
    conn = db_connect()
    before = load_settings(conn)
    conn.close()
    monkeypatch.setattr(views, "NtopClient", lambda *args: pytest.fail("Viewer reached network configuration"))
    for action in ("save", "test"):
        assert c.post("/integracoes/ntopng", data={
            "csrf": csrf, "action": action, "enabled": "on", "password": "overwrite"
        }).status_code == 403
    assert c.get("/integracoes/ntopng").status_code == 403
    for path in ("/", "/acessos", "/trafego", "/dns"):
        response = c.get(path)
        assert response.status_code == 200
        assert b'href="/integracoes/ntopng"' not in response.data
    assert c.post("/acessos", data={"csrf": csrf}).status_code == 405
    for fmt in ("csv", "xlsx", "pdf"):
        response = c.get("/acessos/export/" + fmt)
        assert response.status_code == 200
        assert b"test-only-secret" not in response.data
    conn = db_connect()
    assert load_settings(conn) == before
    conn.close()


def test_filters_export_and_freshness_expose_estimates_not_exact_close(configured):
    c, password = configured
    now = int(time.time()) - 500
    collect(api_at(now, now + 20), now + 20)
    collect(empty_api(now), now + 50)
    collect(empty_api(now), now + 170)
    login(c, "owner", password)
    html = c.get("/acessos?computer=PC-TESTE&application=Netflix&state=ended&refresh=0").get_data(as_text=True)
    assert "Encerrada (estimativa)" in html and "Deixou de aparecer entre" in html
    assert "Netflix" in html
    assert "Nenhum acesso observado" in c.get("/acessos?application=OtherService").get_data(as_text=True)
    exported = c.get("/acessos/export/csv?state=ended").get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(exported.lstrip("\ufeff")), delimiter=";"))
    assert rows[1][4] == "Netflix"
    assert rows[1][13] == "Encerrada (estimativa)"
    assert c.get("/acessos?start=invalid").status_code == 400


def test_existing_connection_rows_are_not_marked_active_by_migration(client):
    from adguard_dns_reports.database import db_connect, create_schema
    conn = db_connect()
    conn.execute("""CREATE TABLE traffic_connections(
        uid TEXT PRIMARY KEY,source TEXT NOT NULL,first_seen INTEGER NOT NULL,last_seen INTEGER NOT NULL,
        observed_at INTEGER NOT NULL,ip TEXT NOT NULL,mac TEXT NOT NULL,hostname TEXT NOT NULL,
        vlan INTEGER NOT NULL,location TEXT NOT NULL,remote_ip TEXT NOT NULL,remote_name TEXT NOT NULL,
        remote_port INTEGER NOT NULL,protocol TEXT NOT NULL,application TEXT NOT NULL,
        bytes_seen INTEGER NOT NULL,scope TEXT NOT NULL)""")
    conn.execute("INSERT INTO traffic_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("old","source",10,20,30,"10.77.0.10","02:00:00:00:00:10","PC-TESTE",0,
                  "Sem local","93.184.216.34","example.test",443,"TCP","TLS.Netflix",5000,"internet"))
    conn.commit()
    create_schema(conn)
    create_schema(conn)
    row = conn.execute("SELECT * FROM traffic_connections WHERE uid='old'").fetchone()
    assert row["bytes_seen"] == 5000 and row["flow_state"] == "legacy"
    assert row["first_observed"] is None and row["missing_since"] is None
    conn.close()


def test_separate_visits_remain_separate_and_stale_data_is_not_active(configured):
    from adguard_dns_reports.traffic.access import group_accesses
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.database import db_connect
    now = int(time.time()) - 2000
    collect(api_at(now, now + 20, identifier="first"), now + 20)
    collect(empty_api(now), now + 50)
    collect(empty_api(now), now + 170)
    collect(api_at(now + 600, now + 620, identifier="second"), now + 620)
    conn = db_connect()
    settings = load_settings(conn)
    meta = dict(conn.execute("SELECT key,value FROM metadata"))
    groups = group_accesses(stored(), settings, meta, now + 620)
    assert len(groups) == 2 and {row["state"] for row in groups} == {"active", "ended"}
    assert "active" not in {row["state"] for row in group_accesses(stored(), settings, meta, now + 900)}
    settings["enabled"] = False
    assert "paused" in {row["state"] for row in group_accesses(stored(), settings, meta, now + 620)}
    conn.close()


def test_admin_can_change_absence_threshold_without_reentering_credentials(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.traffic.settings import load_settings
    c, password = configured
    login(c, "owner", password)
    with c.session_transaction() as session:
        csrf = session["csrf"]
    body = {"csrf": csrf, "action": "save", "enabled": "on", "url": "https://ntopng.example.test",
            "username": "reader", "password": "", "ifid": "0", "verify_tls": "on",
            "local_networks": "10.77.0.0/24", "session_idle_seconds": "180"}
    assert c.post("/integracoes/ntopng", data=body).status_code == 200
    conn = db_connect()
    settings = load_settings(conn)
    assert settings["session_idle_seconds"] == 180 and settings["password"] == "test-only-secret"
    conn.close()
    body["session_idle_seconds"] = "1"
    page = c.post("/integracoes/ntopng", data=body)
    assert "entre 60 e 3.600" in page.get_data(as_text=True)
    conn = db_connect()
    assert load_settings(conn)["session_idle_seconds"] == 180
    conn.close()


def test_report_escapes_hostnames_and_formula_safe_export(configured):
    c, password = configured
    now = int(time.time()) - 10
    api = api_at(now - 20, now)
    api.host["name"] = '<script>alert("x")</script>'
    collect(api, now)
    login(c, "owner", password)
    html = c.get("/acessos").get_data(as_text=True)
    assert '<script>alert("x")</script>' not in html
    assert "&lt;script&gt;" in html
    api.host["name"] = "=SUM(1,2)"
    collect(api, now + 1)
    csv_text = c.get("/acessos/export/csv").get_data(as_text=True)
    assert "'=SUM(1,2)" in csv_text
