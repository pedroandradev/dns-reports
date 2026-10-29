"""Destination evidence: detailed Community API, DNS correlation and honest unknowns."""
import copy
import csv
import io
import time
from datetime import datetime, timezone

import pytest

from test_app import client, login
from test_traffic import configured
from test_access_report import api_at, collect, stored


def detailed_row(info="video.example.test", app="TLS", port=443, key="flow"):
    return {
        "key": key, "hash_id": "hash-" + key, "first_seen": 100, "last_seen": 130,
        "client": {"ip": "10.77.0.10", "port": 50123, "vlan": 0},
        "server": {"ip": "93.184.216.34", "port": port, "vlan": 0, "name": "93.184.216.34"},
        "application": {"name": app}, "l4_proto": {"name": "TCP", "id": 6},
        "bytes": {"total": 500, "cli_bytes": 100, "srv_bytes": 400}, "info": info,
    }


def bare_client():
    from adguard_dns_reports.traffic.client import NtopClient
    return object.__new__(NtopClient)


def test_detailed_community_api_preserves_keys_and_extracts_only_hostname():
    api = bare_client()
    calls = []
    def get(endpoint, **params):
        calls.append((endpoint, params))
        return [detailed_row("https://video.example.test/private/path?token=secret", "HTTP")]
    api.get = get
    row = api.flows()[0]
    assert row["flow_hostname"] == "video.example.test"
    assert row["key"] == "flow" and row["hash_id"] == "hash-flow"
    assert row["bytes"] == 500 and row["protocol"] == {"l7": "HTTP", "l4": "TCP"}
    assert "secret" not in repr(row) and "private/path" not in repr(row)
    assert calls == [("flow/active_list.lua", {"start": 0, "length": 100,
                                            "sort": "first_seen", "order": "asc"})]
    assert api.flow_api == "detailed"


def test_detailed_pagination_and_lua_empty_object():
    api = bare_client()
    api.get = lambda endpoint, **p: [detailed_row(key=str(i)) for i in range(100)] if not p["start"] else {}
    assert len(api.flows()) == 100
    api.get = lambda endpoint, **p: [detailed_row(key=str(i)) for i in range(100)]
    from adguard_dns_reports.traffic.client import NtopError
    with pytest.raises(NtopError, match="Paginação"):
        api.flows()


def test_optional_endpoint_fallback_and_auth_or_partial_errors_are_not_empty_snapshots():
    from adguard_dns_reports.traffic.client import NtopError, NtopUnsupported
    api = bare_client()
    calls = []
    def get(endpoint, **p):
        calls.append(endpoint)
        if endpoint.endswith("active_list.lua"):
            raise NtopUnsupported("Optional endpoint absent")
        return {"data": [{"key": "legacy"}]}
    api.get = get
    assert api.flows() == [{"key": "legacy"}]
    assert calls == ["flow/active_list.lua", "flow/active.lua"] and api.flow_api == "basic"
    api.get = lambda *a, **k: (_ for _ in ()).throw(NtopError("Authentication denied"))
    with pytest.raises(NtopError, match="Authentication"):
        api.flows()
    def partial(endpoint, **p):
        if p["start"]:
            raise NtopUnsupported("Unavailable")
        return [detailed_row(key=str(i)) for i in range(100)]
    api.get = partial
    with pytest.raises(NtopError, match="incompleta"):
        api.flows()
    api.get = lambda *a, **k: [{"key": "broken"}]
    with pytest.raises(NtopError, match="inválido"):
        api.flows()


@pytest.mark.parametrize("info,app,port,expected", [
    ("www.netflix.com", "TLS", 443, "www.netflix.com"),
    ("cdn.example.test/video?id=private", "HTTP", 80, "cdn.example.test"),
    ("https://cdn.example.test:8443/private", "QUIC", 8443, "cdn.example.test"),
    ("queried.example.test", "DNS", 53, ""),
    ("queried.example.test", "DNS", 443, ""),
    ("example.test", "STUN", 3478, ""),
    ("SSH-2.0-example.test", "SSH", 22, ""),
    ("https://user:secret@example.test/path", "TLS", 443, ""),
    ("<a href='https://example.test'>site</a>", "HTTP", 80, ""),
    ("93.184.216.34", "TLS", 443, ""),
    ("", "Unknown", 443, ""),
])
def test_info_is_not_a_dns_question_banner_or_arbitrary_html(info, app, port, expected):
    from adguard_dns_reports.traffic.names import flow_hostname
    assert flow_hostname(info, app, port) == expected


def test_provisional_and_technical_protocols_are_not_websites():
    from adguard_dns_reports.traffic.access import destination
    assert destination("TooEarly", "", "93.184.216.34", 443, "TCP")[0] == "Identificação pendente (TooEarly)"
    assert destination("ICMP", "router.example.test", "93.184.216.34", 0, "ICMP")[0] == "Protocolo técnico (ICMP)"
    assert destination("STUN", "", "93.184.216.34", 3478, "UDP")[0] == "Protocolo técnico (STUN)"
    assert destination("STUN.WhatsApp", "", "93.184.216.34", 3478, "UDP")[0] == "WhatsApp"


def dns_entry(when, domain="video.example.test", ip="10.77.0.10", answer_ip="93.184.216.34", ttl=300):
    return {
        "time": datetime.fromtimestamp(when, timezone.utc).isoformat(),
        "client": ip, "question": {"name": domain, "type": "A"}, "status": "NOERROR",
        "answer": [{"type": "A", "value": answer_ip, "ttl": ttl}],
    }


def remember(conn, entry, now):
    from adguard_dns_reports.dns_evidence import remember_answers
    # Synthetic row avoids PTR/network lookups in DNS-cache unit tests.
    query = ("query-" + repr(entry), entry["time"], entry["client"], "PC-TESTE",
             entry["question"]["name"], "", "", "A", entry["status"],
             int(entry.get("reason", "").startswith("Filtered")), "")
    remember_answers(conn, entry, query, now)


def cache(entry, now):
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    remember(conn, entry, now)
    conn.commit()
    conn.close()


def unnamed_api(first, last, app="TLS", total=2000):
    api = api_at(first, last, total, app)
    flows = api.flows()
    flows[0]["server"]["name"] = flows[0]["server"]["ip"]
    api.flows = lambda: copy.deepcopy(flows)
    return api


def test_same_client_answer_ip_and_flow_start_ttl_produce_explicit_inference(configured):
    now = int(time.time()) - 100
    cache(dns_entry(now - 20, "www.netflix.com"), now)
    collect(unnamed_api(now, now + 10), now + 10)
    row = stored()[0]
    assert row["access_service"] == "Netflix"
    assert row["access_evidence"] == "Correlação DNS (inferência)"
    c, password = configured
    login(c, "owner", password)
    html = c.get("/acessos?kind=identified").get_data(as_text=True)
    assert "Netflix" in html and "Correlação DNS (inferência)" in html
    assert "Netflix" in c.get("/acessos?application=www.netflix.com").get_data(as_text=True)
    exported = c.get("/acessos/export/csv?kind=identified").get_data(as_text=True)
    rows = list(csv.reader(io.StringIO(exported.lstrip("\ufeff")), delimiter=";"))
    assert rows[1][-1] == "www.netflix.com"
    assert c.get("/acessos?kind=unidentified").get_data(as_text=True).count("Nenhum acesso observado") == 1


@pytest.mark.parametrize("changes", [
    {"client": "10.77.0.20"}, {"answer_ip": "8.8.8.8"}, {"ttl": 5}, {"time_offset": 1},
    {"reason": "FilteredBlackList"}, {"status": "NXDOMAIN"}, {"answer": []},
    {"answer": [{"type": "CNAME", "value": "video.example.test", "ttl": 300}]},
    {"answer": [{"type": "A", "value": "0.0.0.0", "ttl": 300}]},
    {"answer": [{"type": "A", "value": "93.184.216.34", "ttl": 0}]},
])
def test_dns_mismatch_expired_future_blocked_or_question_only_never_identifies(configured, changes):
    now = int(time.time()) - 100
    entry = dns_entry(now - 20)
    changes = dict(changes)
    if "answer_ip" in changes:
        entry["answer"][0]["value"] = changes.pop("answer_ip")
    if "ttl" in changes:
        entry["answer"][0]["ttl"] = changes.pop("ttl")
    if "time_offset" in changes:
        entry["time"] = datetime.fromtimestamp(now + changes.pop("time_offset"), timezone.utc).isoformat()
    entry.update(changes)
    cache(entry, now + 10)
    collect(unnamed_api(now, now + 10), now + 10)
    assert stored()[0]["access_evidence"] == "IP e porta"


def test_shared_cdn_stays_ambiguous_even_when_cache_is_pruned_then_flow_name_wins(configured):
    from adguard_dns_reports.database import db_connect
    now = int(time.time()) - 100
    cache(dns_entry(now - 30, "first.example.test"), now)
    cache(dns_entry(now - 20, "second.example.test"), now)
    collect(unnamed_api(now, now + 10), now + 10)
    assert stored()[0]["access_evidence"] == "DNS ambíguo (vários domínios)"
    conn = db_connect()
    conn.execute("DELETE FROM dns_resolutions")
    conn.commit()
    conn.close()
    collect(unnamed_api(now, now + 40), now + 40)
    assert stored()[0]["access_evidence"] == "DNS ambíguo (vários domínios)"
    api = unnamed_api(now, now + 70)
    original = api.flows()
    original[0]["flow_hostname"] = "actual.example.test"
    api.flows = lambda: copy.deepcopy(original)
    collect(api, now + 70)
    row = stored()[0]
    assert row["access_service"] == "actual.example.test" and row["name_source"] == "flow"
    assert row["access_evidence"] == "Nome no fluxo ntopng"


def test_flow_hostname_and_specific_ndpi_survive_samples_omitting_metadata(configured):
    now = int(time.time()) - 100
    api = unnamed_api(now, now + 10)
    flows = api.flows()
    flows[0]["flow_hostname"] = "www.netflix.com"
    api.flows = lambda: copy.deepcopy(flows)
    collect(api, now + 10)
    collect(unnamed_api(now, now + 40), now + 40)
    assert stored()[0]["remote_name"] == "www.netflix.com"
    assert stored()[0]["access_service"] == "Netflix"
    collect(unnamed_api(now, now + 70, "TLS.YouTube"), now + 70)
    collect(unnamed_api(now, now + 80, "TooEarly"), now + 80)
    assert len(stored()) == 1
    assert stored()[0]["access_service"] == "YouTube" and stored()[0]["access_evidence"] == "nDPI"


def test_inbound_flow_info_does_not_label_external_client_as_local_server(configured):
    now = int(time.time()) - 100
    api = unnamed_api(now, now + 10)
    flows = api.flows()
    flow = flows[0]
    flow["client"], flow["server"] = flow["server"], flow["client"]
    flow["flow_hostname"] = "our-server.example.test"
    api.flows = lambda: flows
    collect(api, now + 10)
    assert stored()[0]["access_evidence"] == "IP e porta"
    assert stored()[0]["remote_name"] != "our-server.example.test"


def test_dns_collector_ingests_answers_from_existing_query_overlap_without_duplicate_counts(configured, monkeypatch):
    from adguard_dns_reports import collector
    from adguard_dns_reports.database import db_connect
    now = int(time.time()) - 10
    event = dns_entry(now)
    monkeypatch.setattr(collector, "lookup_hostname", lambda *args: "PC-TESTE")
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"data": [event]}
    class Session:
        def get(self, *args, **kwargs):
            return Response()
    monkeypatch.setattr(collector.requests, "Session", Session)
    assert collector.collect() == 1
    assert collector.collect() == 0
    conn = db_connect()
    assert conn.execute("SELECT COUNT(*) FROM dns_resolutions").fetchone()[0] == 1
    conn.close()
    collect(unnamed_api(now + 1, now + 5), now + 5)
    assert stored()[0]["access_service"] == "video.example.test"


def test_admin_toggle_preserves_existing_credentials_and_disables_new_dns_inference(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.traffic.settings import load_settings
    from adguard_dns_reports.config import CONFIG
    c, password = configured
    before = CONFIG.read_bytes()
    login(c, "owner", password)
    with c.session_transaction() as session:
        csrf = session["csrf"]
    response = c.post("/integracoes/ntopng", data={
        "csrf": csrf, "action": "save", "enabled": "on", "url": "https://ntopng.example.test",
        "username": "reader", "password": "", "ifid": "0", "verify_tls": "on",
        "local_networks": "10.77.0.0/24", "dns_enrichment_present": "1"})
    assert response.status_code == 200 and CONFIG.read_bytes() == before
    conn = db_connect()
    settings = load_settings(conn)
    assert settings["password"] == "test-only-secret" and settings["dns_enrichment"] is False
    conn.close()
    now = int(time.time()) - 100
    cache(dns_entry(now - 20), now)
    collect(unnamed_api(now, now + 10), now + 10)
    assert stored()[0]["access_evidence"] == "IP e porta"


def test_small_zero_duration_and_technical_details_are_visible_and_filterable(configured):
    c, password = configured
    now = int(time.time()) - 10
    api = unnamed_api(now, now, total=54)
    collect(api, now)
    login(c, "owner", password)
    html = c.get("/acessos?kind=unidentified").get_data(as_text=True)
    assert "54,00 B" in html and "pode ser apenas sinalização" in html
    assert "Poucos bytes" in c.get("/acessos/export/csv?kind=unidentified").get_data(as_text=True)
    collect(unnamed_api(now, now, app="ICMP", total=54), now + 1)
    html = c.get("/acessos?kind=technical").get_data(as_text=True)
    assert "Protocolo técnico (ICMP)" in html
    assert "Nenhum acesso observado" in c.get("/acessos?kind=identified").get_data(as_text=True)


def test_dns_cache_retention_and_limit_and_additive_migration(configured, monkeypatch):
    from adguard_dns_reports.database import db_connect, create_schema
    from adguard_dns_reports import dns_evidence
    now = int(time.time())
    conn = db_connect()
    for i, age in enumerate((90000, 300, 200, 100)):
        conn.execute("INSERT INTO dns_resolutions VALUES(?,?,?,?,?,?)",
                     (str(i), "10.77.0.10", "93.184.216.34", "a.example.test", now - age, now + 60))
    monkeypatch.setattr(dns_evidence, "MAX_ROWS", 2)
    dns_evidence.prune(conn, now)
    create_schema(conn)
    create_schema(conn)
    assert [r[0] for r in conn.execute("SELECT uid FROM dns_resolutions ORDER BY uid")] == ["2", "3"]
    conn.close()


def test_detailed_api_keeps_existing_flow_identity_and_does_not_import_old_bytes_twice(configured):
    now = int(time.time()) - 100
    api = unnamed_api(now, now + 10)
    collect(api, now + 10)
    uid = stored()[0]["uid"]
    rich = detailed_row("new.example.test", key="primary")
    rich.update(hash_id="primary", first_seen=now, last_seen=now + 40)
    rich["bytes"]["total"] = 2500
    transport = bare_client()
    transport.get = lambda *a, **k: [rich]
    api.flows = transport.flows
    collect(api, now + 40)
    assert len(stored()) == 1 and stored()[0]["uid"] == uid
    assert stored()[0]["access_service"] == "new.example.test"
    from adguard_dns_reports.database import db_connect
    conn = db_connect()
    assert conn.execute("SELECT SUM(total) FROM traffic_usage WHERE scope='internet'").fetchone()[0] == 500
    conn.close()


def test_ipv6_answer_supports_same_address_family_and_ignores_malformed_answers(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.dns_evidence import candidates
    now = int(time.time()) - 100
    entry = dns_entry(now - 20)
    entry["answer"] = [
        {"type": "AAAA", "value": "2606:4700:4700::1111", "ttl": 300},
        {"type": "A", "value": "2606:4700:4700::1111", "ttl": 300},
        {"type": "AAAA", "value": "<script>", "ttl": 300},
        {"type": "A", "value": "8.8.8.8", "ttl": True},
    ]
    cache(entry, now)
    conn = db_connect()
    assert conn.execute("SELECT COUNT(*) FROM dns_resolutions").fetchone()[0] == 1
    record = ("key", "uid", {"ip": "10.77.0.10"}, "internet", "TLS",
              now, now + 5, 100, "2606:4700:4700::1111", "", 443, "TCP")
    assert candidates(conn, record) == ["video.example.test"]
    conn.close()
