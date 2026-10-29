"""Counter accounting, Community payloads, configuration and authenticated reports."""
import copy
import time

import pytest

from test_app import client, login


@pytest.fixture
def configured(client):
    from adguard_dns_reports.database import db_connect, create_schema
    from adguard_dns_reports.traffic.settings import load_settings, save_settings, validate
    conn = db_connect()
    create_schema(conn)
    settings = validate({"enabled": "on", "url": "https://ntopng.example.test",
                         "username": "reader", "password": "test-only-secret", "ifid": "0",
                         "verify_tls": "on", "local_networks": "10.77.0.0/24",
                         "locations": "Escritório = 10.77.0.0/24"}, load_settings(conn))
    save_settings(conn, settings)
    conn.close()
    return client


class FakeApi:
    def __init__(self, sent=1000, received=2000, app=2200, flow_bytes=500, generation=100):
        self.host = {"ip": "10.77.0.10", "mac": "02:00:00:00:00:10", "name": "PC-TESTE",
                     "vlan": 0, "first_seen": generation,
                     "bytes": {"sent": sent, "recvd": received}}
        self.app = app
        self.flow_bytes = flow_bytes
        self.closed = False

    def hosts(self):
        return [copy.deepcopy(self.host)]

    def applications(self, host):
        return [{"label": "MS_OneDrive", "value": self.app, "duration": 10}]

    def flows(self):
        # Public server flags reproduce the incorrect client flags in ntopng 6.6.
        common = {"key": "123", "hash_id": "456", "first_seen": 100, "last_seen": 200,
                  "bytes": self.flow_bytes, "vlan": 0, "protocol": {"l4": "TCP", "l7": "TLS.YouTube"},
                  "client": {"ip": "10.77.0.10", "port": 50123},
                  "server": {"ip": "93.184.216.34", "port": 443, "name": "example.test",
                             "is_dhcp": True, "is_broadcast": True}}
        internal = copy.deepcopy(common)
        internal["key"] = "internal"
        internal["server"]["ip"] = "10.77.0.20"
        return [common, internal]

    def close(self):
        self.closed = True


def rows(table):
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM " + table)]
    finally:
        conn.close()


def test_first_baseline_deltas_no_double_count_and_internal_filter(configured):
    from adguard_dns_reports.traffic.collector import collect
    now = int(time.time()) - 90
    api = FakeApi()
    collect(api, now)
    assert api.closed
    assert rows("traffic_usage") == []
    assert len(rows("traffic_connections")) == 2
    collect(FakeApi(1300, 2700, 3000, 650), now + 30)
    usage = rows("traffic_usage")
    lan = next(row for row in usage if row["scope"] == "lan")
    app = next(row for row in usage if row["scope"] == "lan_app")
    internet = next(row for row in usage if row["scope"] == "internet")
    assert (lan["sent"], lan["received"], lan["total"]) == (300, 700, 1000)
    assert app["total"] == 800 and app["sent"] is None
    assert internet["total"] == 150 and internet["sent"] is None
    assert internet["application"] == "TLS.YouTube"
    assert internet["location"] == "Escritório"
    collect(FakeApi(1300, 2700, 3000, 650), now + 60)
    assert len(rows("traffic_usage")) == 3


def test_reset_and_host_recreation_do_not_import_old_totals(configured):
    from adguard_dns_reports.traffic.collector import collect
    now = int(time.time()) - 200
    collect(FakeApi(), now)
    collect(FakeApi(5, 5, 5, 5), now + 30)
    assert not rows("traffic_usage")
    collect(FakeApi(8, 9, 10, 7), now + 60)
    assert sum(row["total"] for row in rows("traffic_usage") if row["scope"] == "lan") == 7
    collect(FakeApi(10000, 20000, 25000, 9, generation=999), now + 90)
    assert sum(row["total"] for row in rows("traffic_usage") if row["scope"] == "lan") == 7


def test_application_failure_keeps_previous_baseline(configured):
    from adguard_dns_reports.traffic.collector import collect
    from adguard_dns_reports.traffic.client import NtopError
    now = int(time.time()) - 100
    collect(FakeApi(), now)
    api = FakeApi(1200, 2500, 2600, 600)
    api.applications = lambda host: (_ for _ in ()).throw(NtopError("Falha de conexão."))
    collect(api, now + 30)
    collect(FakeApi(1300, 2600, 3000, 700), now + 60)
    apps = [row for row in rows("traffic_usage") if row["scope"] == "lan_app"]
    assert len(apps) == 1 and apps[0]["total"] == 800


def test_disabled_integration_does_not_connect_or_modify_dns(client):
    from adguard_dns_reports.traffic.collector import collect
    api = FakeApi()
    api.hosts = lambda: pytest.fail("disabled integration connected")
    assert collect(api) == {"enabled": False}
    assert not rows("traffic_usage")


def test_authenticated_reports_export_and_secret_redaction(configured):
    from adguard_dns_reports.traffic.collector import collect
    c, password = configured
    now = int(time.time()) - 100
    collect(FakeApi(), now)
    collect(FakeApi(1300, 2700, 3000, 650), now + 30)
    assert c.get("/trafego").status_code == 302
    assert login(c, "owner", password).status_code == 302
    page = c.get("/trafego")
    assert page.status_code == 200
    assert b"TLS.YouTube" in page.data and b"150,00 B" in page.data
    assert "parcial" in page.get_data(as_text=True)
    lan = c.get("/trafego?scope=lan")
    assert b"1,00 kB" in lan.data
    app = c.get("/trafego?scope=lan&application=OneDrive")
    assert b"800,00 B" in app.data
    settings = c.get("/integracoes/ntopng")
    assert settings.status_code == 200
    assert b"test-only-secret" not in settings.data
    assert c.post("/integracoes/ntopng", data={"enabled": "on"}).status_code == 403
    for fmt in ("csv", "xlsx", "pdf"):
        response = c.get("/trafego/export/" + fmt)
        assert response.status_code == 200
        assert "attachment;" in response.headers["Content-Disposition"]
        assert b"test-only-secret" not in response.data
    assert c.get("/trafego?start=invalid").status_code == 400
    assert c.get("/trafego?start=2026-02-02&end=2026-01-01").status_code == 400


def test_ordinary_user_cannot_change_integration(configured):
    c, password = configured
    login(c, "owner", password)
    with c.session_transaction() as session:
        csrf = session["csrf"]
    c.post("/usuarios", data={"csrf": csrf, "action": "add",
                             "username": "analyst", "password": "ordinary-password-123"})
    c.post("/logout", data={"csrf": csrf})
    login(c, "analyst", "ordinary-password-123")
    assert c.get("/trafego").status_code == 200
    assert c.get("/integracoes/ntopng").status_code == 403
    assert c.post("/integracoes/ntopng", data={}).status_code == 403


def test_configuration_validation_and_longest_prefix(configured):
    from adguard_dns_reports.traffic.settings import validate, load_settings, local_name
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    previous = load_settings(conn)
    conn.close()
    base = {"enabled": "on", "url": previous["url"], "username": previous["username"],
            "password": "", "ifid": "0", "verify_tls": "on", "local_networks": "10.77.0.0/24"}
    assert validate(base, previous)["password"] == "test-only-secret"
    assert validate(base, previous)["source"] == previous["source"]
    for changes in ({"url": "https://other.example.test"}, {"url": "file:///etc/passwd"},
                    {"url": "https://user:password@example.test"}, {"ifid": "-1"},
                    {"local_networks": "0.0.0.0/0"}, {"locations": "Local sem rede"}):
        with pytest.raises(ValueError):
            validate({**base, **changes}, previous)
    rules = [{"name": "Geral", "network": "10.0.0.0/8"},
             {"name": "Local", "network": "10.77.0.0/24"}]
    assert local_name("10.77.0.10", rules) == "Local"
    assert local_name("192.168.33.1", rules) == "Sem local"


def test_api_transport_rejects_html_redirect_and_rc_errors():
    from adguard_dns_reports.traffic.client import NtopClient, NtopError
    class Response:
        is_redirect = False
        status_code = 200
        def raise_for_status(self):
            pass
        def json(self):
            return {"rc": 0, "rsp": []}
    class Session:
        def get(self, url, **kwargs):
            self.kwargs = kwargs
            return response
        def close(self):
            pass
    response, session = Response(), Session()
    api = NtopClient({"url": "https://ntopng.example.test", "ifid": 0,
                      "username": "reader", "password": "secret", "verify_tls": True}, session)
    assert api.get("ntopng/interfaces.lua") == []
    assert session.kwargs["allow_redirects"] is False
    assert session.kwargs["verify"] is True
    response.is_redirect = True
    with pytest.raises(NtopError):
        api.get("host/active.lua")
    response.is_redirect = False
    response.json = lambda: {"rc": -3, "rsp": []}
    with pytest.raises(NtopError):
        api.get("host/active.lua")
    response.json = lambda: (_ for _ in ()).throw(ValueError("HTML contains secret"))
    with pytest.raises(NtopError, match="resposta inválida"):
        api.get("host/active.lua")


def test_pagination_works_without_totalrows_and_refuses_repeated_pages():
    from adguard_dns_reports.traffic.client import NtopClient, NtopError
    api = object.__new__(NtopClient)
    calls = []
    def get(endpoint, **params):
        calls.append(params)
        return {"data": [{"ip": str(i), "key": str(i)} for i in range(100)]} if params["currentPage"] == 1 else {"data": []}
    api.get = get
    assert len(api.hosts()) == 100
    assert len(calls) == 2
    api.get = lambda endpoint, **params: {"data": [{"key": str(i)} for i in range(100)]}
    with pytest.raises(NtopError, match="Paginação"):
        api.hosts()


def test_source_changes_when_reenabled(configured):
    from adguard_dns_reports.traffic.settings import load_settings, validate
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    previous = load_settings(conn)
    conn.close()
    previous["enabled"] = False
    updated = validate({"enabled": "on", "url": previous["url"], "username": previous["username"],
                        "ifid": "0", "verify_tls": "on", "local_networks": "10.77.0.0/24"}, previous)
    assert updated["source"] != previous["source"]


def test_hourly_aggregation_and_flow_reclassification_are_not_double_counted(configured):
    from adguard_dns_reports.traffic.collector import collect
    now = int(time.time()) // 3600 * 3600 + 10
    collect(FakeApi(), now)
    collect(FakeApi(1100, 2200, 2300, 550), now + 30)
    collect(FakeApi(1200, 2400, 2500, 650), now + 60)
    usage = rows("traffic_usage")
    assert len([row for row in usage if row["scope"] == "internet"]) == 1
    assert sum(row["total"] for row in usage if row["scope"] == "lan") == 600
    assert sum(row["total"] for row in usage if row["scope"] == "internet") == 150
    api = FakeApi(1300, 2500, 2600, 700)
    original = api.flows
    def reclassified():
        flows = original()
        flows[0]["protocol"]["l7"] = "TLS.Netflix"
        return flows
    api.flows = reclassified
    collect(api, now + 90)
    internet = [row for row in rows("traffic_usage") if row["scope"] == "internet"]
    assert sum(row["total"] for row in internet) == 200
    assert next(row for row in internet if row["application"] == "TLS.Netflix")["total"] == 50


def test_migration_preserves_existing_config_dns_users_and_sessions(client):
    from adguard_dns_reports.database import db_connect, create_schema
    from adguard_dns_reports.config import CONFIG
    conn = db_connect()
    conn.execute("""CREATE TABLE users(id INTEGER PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE,
                    password_hash TEXT, session_version INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("INSERT INTO users(username,password_hash,session_version) VALUES('analyst','unchanged-hash',17)")
    conn.commit()
    before = CONFIG.read_bytes()
    create_schema(conn)
    conn.execute("INSERT INTO queries VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                 ("legacy-query","2026-01-01T00:00:00.000000Z","10.77.0.10","PC-TESTE",
                  "example.test","Example","Teste","A","NOERROR",0,""))
    conn.commit()
    create_schema(conn)
    create_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM queries WHERE uid='legacy-query'").fetchone()[0] == 1
    user = conn.execute("SELECT * FROM users WHERE username='analyst'").fetchone()
    assert user["password_hash"] == "unchanged-hash" and user["session_version"] == 17
    assert user["is_admin"] == 0
    assert CONFIG.read_bytes() == before
    conn.close()


def test_api_failure_does_not_advance_baselines(configured):
    from adguard_dns_reports.traffic.collector import collect
    from adguard_dns_reports.traffic.client import NtopError
    now = int(time.time()) - 100
    collect(FakeApi(), now)
    before = rows("traffic_state")
    api = FakeApi()
    api.hosts = lambda: (_ for _ in ()).throw(NtopError("Falha de conexão."))
    with pytest.raises(NtopError):
        collect(api, now + 30)
    assert rows("traffic_state") == before
    assert api.closed


def test_form_save_keeps_adguard_configuration_and_password_private(configured):
    from adguard_dns_reports.config import CONFIG
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.database import db_connect
    c, password = configured
    login(c, "owner", password)
    with c.session_transaction() as session:
        csrf = session["csrf"]
    before = CONFIG.read_bytes()
    response = c.post("/integracoes/ntopng", data={
        "csrf": csrf, "enabled": "on", "url": "https://ntopng.example.test",
        "username": "reader", "password": "", "ifid": "0", "verify_tls": "on",
        "local_networks": "10.77.0.0/24", "locations": "Setor A = 10.77.0.0/25"})
    assert response.status_code == 200
    assert b"test-only-secret" not in response.data
    conn = db_connect()
    assert load_settings(conn)["password"] == "test-only-secret"
    conn.close()
    assert CONFIG.read_bytes() == before


def test_old_connections_are_pruned_but_hourly_measurements_remain(configured):
    from adguard_dns_reports.traffic.collector import collect
    from adguard_dns_reports.database import db_connect
    now = int(time.time())
    collect(FakeApi(), now - 8 * 86400)
    collect(FakeApi(1100, 2200, 2300, 550), now - 8 * 86400 + 30)
    conn = db_connect()
    old = conn.execute("SELECT uid FROM traffic_connections LIMIT 1").fetchone()[0]
    conn.execute("UPDATE traffic_connections SET uid='old-connection' WHERE uid=?", (old,))
    conn.commit()
    conn.close()
    collect(FakeApi(1200, 2300, 2400, 650), now)
    assert all(row["uid"] != "old-connection" for row in rows("traffic_connections"))
    assert any(row["sample_end"] < now - 7 * 86400 for row in rows("traffic_usage"))


@pytest.mark.parametrize("remote", ["224.0.0.251", "239.255.255.250", "255.255.255.255",
                                    "100.64.0.1", "10.88.0.1", "127.0.0.1"])
def test_multicast_private_and_special_destinations_are_not_internet(remote):
    import ipaddress
    from adguard_dns_reports.traffic.collector import flow_record
    flow = FakeApi().flows()[0]
    flow["server"]["ip"] = remote
    settings = {"source": "test", "locations": []}
    record = flow_record(flow, settings, {}, [ipaddress.ip_network("10.77.0.0/24")])
    assert record[3] == "interno"
