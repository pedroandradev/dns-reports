"""Deployment ACL tests: no Proxmox, credentials or running AdGuard required."""
import importlib.util
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_access", ROOT / "scripts/proxmox/configure_access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
TEMPLATE = (ROOT / "scripts/proxmox/nginx/dnsreports.conf").read_text()


@pytest.mark.parametrize("value, expected", [
    ("", []), ("  ", []),
    ("100.96.0.7/24, 10.250.0.0/24 100.96.0.0/24", ["100.96.0.0/24", "10.250.0.0/24"]),
    ("192.0.2.5/32", ["192.0.2.5/32"]),
])
def test_cidr_validation(value, expected):
    assert access.vpn_networks(value) == expected


@pytest.mark.parametrize("value", [
    "all", "0.0.0.0/0", "0.0.0.0/1,128.0.0.0/1", "100.96.0.1", "::/0", "fd00::/64",
    "10.0.0.0/33", "10.0.0.0/8; deny all;", "$(touch /tmp/injected)",
    "10.0.0.1/32\nallow all;", "https://example.com/24",
    ",".join(f"192.0.2.{i}/32" for i in range(65)),
])
def test_invalid_or_unrestricted_rules(value):
    with pytest.raises(ValueError):
        access.vpn_networks(value)


def test_legacy_site_preserves_lan_tls_proxy_and_customizations():
    legacy = TEMPLATE.replace("allow 10.0.0.0/8;", "allow 10.25.0.0/24;")
    legacy = legacy.replace("client_max_body_size 1m;", 'client_max_body_size 2m; # custom } comment\n')
    result = access.plan_site(legacy)
    assert result.count('include "/etc/nginx/snippets/dnsreports-vpn.conf";') == 2
    assert result.replace('include "/etc/nginx/snippets/dnsreports-vpn.conf";\n    ', "") == legacy
    assert access.plan_site(result) == result


@pytest.mark.parametrize("site", [
    TEMPLATE.replace("deny all;", "allow all;"),
    TEMPLATE.replace("deny all;", "deny 192.0.2.1; deny all;"),
    TEMPLATE.replace("location / {", "location / { allow 192.0.2.0/24; deny all;"),
    TEMPLATE.replace("location / {", "include /etc/nginx/custom.conf; location / {"),
    TEMPLATE.replace("location / {", "satisfy any; location / {"),
    TEMPLATE.replace("deny all;", ""),
    TEMPLATE + "server { listen 8080; }",
    TEMPLATE + "}",
])
def test_custom_or_ambiguous_acl_refused(site):
    with pytest.raises(ValueError):
        access.plan_site(site)


@pytest.fixture
def files(tmp_path):
    site = tmp_path / "site.conf"
    snippet = tmp_path / "vpn.conf"
    site.write_text(TEMPLATE)
    return site, snippet


def test_apply_replace_clear_and_preserve_previous_lan(files):
    site, snippet = files
    calls = []
    run = lambda command, **kwargs: calls.append(command)
    access.configure("100.96.0.0/24", site, snippet, run=run)
    applied_site = site.read_bytes()
    assert "allow 100.96.0.0/24;" in snippet.read_text()
    assert calls == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]
    access.configure("100.97.0.0/24", site, snippet, run=run)
    assert "100.96." not in snippet.read_text()
    assert "100.97." in snippet.read_text()
    access.configure("", site, snippet, run=run)
    assert snippet.read_text() == access.HEADER
    assert site.read_bytes() == applied_site
    assert "allow 192.168.0.0/16;" in site.read_text()
    assert len(list(site.parent.glob("site.conf.vpn-backup-*"))) == 3


@pytest.mark.parametrize("failure", ["test", "reload"])
@pytest.mark.parametrize("existing_snippet", [False, True])
def test_validation_and_reload_failure_restore_both_files(files, failure, existing_snippet):
    site, snippet = files
    original_site = site.read_bytes()
    previous = (access.HEADER + "allow 100.95.0.0/24;\n").encode() if existing_snippet else None
    if previous is not None:
        snippet.write_bytes(previous)
    calls = []

    def run(command, check):
        calls.append(command)
        fail_at = ["nginx", "-t"] if failure == "test" else ["systemctl", "reload", "nginx"]
        if check and command == fail_at:
            raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        access.configure("100.96.0.0/24", site, snippet, run=run)
    assert site.read_bytes() == original_site
    assert (snippet.read_bytes() if snippet.exists() else None) == previous
    assert calls[-2:] == [["nginx", "-t"], ["systemctl", "reload", "nginx"]]


def test_invalid_input_and_unmanaged_snippet_do_not_mutate(files):
    site, snippet = files
    original = site.read_bytes()
    snippet.write_text("# locally maintained\nallow 192.0.2.0/24;\n")
    previous = snippet.read_bytes()
    for value in ["0.0.0.0/0", "100.96.0.0/24"]:
        with pytest.raises(ValueError):
            access.configure(value, site, snippet, run=lambda *args, **kwargs: pytest.fail("nginx invoked"))
        assert site.read_bytes() == original
        assert snippet.read_bytes() == previous
    assert not list(site.parent.glob("*.vpn-backup-*"))


def test_new_install_validates_without_reload(files):
    site, snippet = files
    calls = []
    access.configure("", site, snippet, reload=False, run=lambda command, **kwargs: calls.append(command))
    assert calls == [["nginx", "-t"]]
