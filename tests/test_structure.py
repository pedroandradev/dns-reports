"""Tests which require no AdGuard Home instance."""
from pathlib import Path
import ast

import pytest
from werkzeug.exceptions import Forbidden
from test_app import client

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "adguard_dns_reports"

def test_all_python_modules_parse():
    for module in PACKAGE.glob("*.py"):
        ast.parse(module.read_text(encoding="utf-8"), filename=str(module))

def test_admin_guard_is_on_backend(client):
    # Exercise endpoint guards directly, independently of the shared auth gate or UI.
    from adguard_dns_reports.auth import usuarios, settings
    c, _ = client
    with c.application.test_request_context():
        for view in (usuarios, settings):
            with pytest.raises(Forbidden):
                view()


def test_frontend_is_in_package():
    assert (PACKAGE / "templates" / "dashboard.html").is_file()
    assert (PACKAGE / "templates" / "users.html").is_file()
    assert (PACKAGE / "static" / "style.css").is_file()

def test_proxmox_services_use_package():
    units = ROOT / "scripts" / "proxmox" / "systemd"
    assert "adguard_dns_reports.wsgi:app" in (units / "dnsreports.service").read_text()
    assert "adguard-reports-collect" in (units / "dnsreports-collector.service").read_text()
