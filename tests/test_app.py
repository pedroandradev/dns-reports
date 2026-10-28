"""Flask integration checks using a temporary SQLite database; no live AdGuard required."""
import hashlib
import importlib
import json
import secrets
import sys

import pytest


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Isolate configuration and database without touching /opt/dnsreports."""
    (tmp_path / "data").mkdir()
    salt = secrets.token_bytes(16)
    rounds = 1000
    password = "test-password-admin-only"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, rounds)
    config = {
        "panel_username": "owner",
        "panel_password_hash": f"pbkdf2_sha256${rounds}${salt.hex()}${digest.hex()}",
        "session_secret": "integration-test-secret",
        "timezone": "Etc/UTC",
        "adguard_url": "http://192.0.2.10",
        "adguard_username": "test",
        "adguard_password": "not-a-real-password",
        "retention_days": 90,
    }
    (tmp_path / "config.json").write_text(json.dumps(config))
    monkeypatch.setenv("DNS_REPORTS_HOME", str(tmp_path))
    monkeypatch.setenv("DNS_REPORTS_CONFIG", str(tmp_path / "config.json"))
    for name in list(sys.modules):
        if name.startswith("adguard_dns_reports"):
            del sys.modules[name]
    from adguard_dns_reports import create_app
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c, password


def login(client, user, password):
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    return client.post("/login", data={"username": user, "password": password, "csrf": csrf})


def test_admin_and_user_permissions(client):
    c, password = client
    assert login(c, "owner", password).status_code == 302
    assert c.get("/usuarios").status_code == 200
    with c.session_transaction() as session:
        token = session["csrf"]
    created = c.post("/usuarios", data={"csrf": token, "action": "add",
                      "username": "analyst", "password": "ordinary-password-123"})
    assert created.status_code == 200
    c.post("/logout", data={"csrf": token})
    assert login(c, "analyst", "ordinary-password-123").status_code == 302
    assert c.get("/").status_code == 200
    assert c.get("/usuarios").status_code == 403
    with c.session_transaction() as session:
        csrf = session["csrf"]
    assert c.post("/usuarios", data={"csrf": csrf, "action": "add",
              "username": "forbidden", "password": "another-password-123"}).status_code == 403
    assert c.get("/export/csv").status_code == 200


def test_login_requires_csrf(client):
    c, password = client
    assert c.post("/login", data={"username": "owner", "password": password}).status_code == 403
