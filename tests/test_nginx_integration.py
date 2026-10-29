"""Real Nginx TLS/proxy/ACL integration. Requires nginx and openssl on PATH."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest

from test_access import access, TEMPLATE

NGINX = os.environ.get("REPORTS_TEST_NGINX") or shutil.which("nginx")
OPENSSL = os.environ.get("REPORTS_TEST_OPENSSL") or shutil.which("openssl")
pytestmark = pytest.mark.skipif(not (NGINX and OPENSSL), reason="nginx/openssl unavailable")


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"test dashboard")

    def log_message(self, *args):
        pass


def test_real_nginx_lan_vpn_denial_spoofing_reload_and_removal(tmp_path):
    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    http_port, tls_port = unused_port(), unused_port()
    cert, key = tmp_path / "test.crt", tmp_path / "test.key"
    subprocess.run([OPENSSL, "req", "-x509", "-nodes", "-newkey", "rsa:2048",
                    "-days", "1", "-subj", "/CN=localhost", "-keyout", str(key),
                    "-out", str(cert)], check=True, capture_output=True)
    site, snippet = tmp_path / "site.conf", tmp_path / "vpn.conf"
    text = TEMPLATE.replace("listen 80 default_server;", f"listen 127.0.0.1:{http_port};")
    text = text.replace("listen 443 ssl default_server;", f"listen 127.0.0.1:{tls_port} ssl;")
    text = text.replace("/etc/nginx/ssl/dnsreports.crt", f'"{cert.as_posix()}"')
    text = text.replace("/etc/nginx/ssl/dnsreports.key", f'"{key.as_posix()}"')
    text = text.replace("http://127.0.0.1:8088", f"http://127.0.0.1:{backend.server_port}")
    site.write_text(text)
    (tmp_path / "logs").mkdir()
    (tmp_path / "temp").mkdir()
    config = tmp_path / "nginx.conf"
    # Test only: emulate source networks through a loopback-only listener.
    # Production templates never trust a caller-supplied IP header.
    config.write_text(
        f'pid "{(tmp_path / "nginx.pid").as_posix()}";\n'
        f'error_log "{(tmp_path / "error.log").as_posix()}";\n'
        'events {}\nhttp {\n'
        + "".join(f'{name}_temp_path "{(tmp_path / "temp" / name).as_posix()}";\n'
                  for name in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
        +
        'set_real_ip_from 127.0.0.1; real_ip_header X-Test-Source;\n'
        f'access_log "{(tmp_path / "access.log").as_posix()}";\n'
        f'include "{site.as_posix()}";\n}}\n'
    )
    base = [NGINX, "-e", "stderr", "-p", tmp_path.as_posix() + "/", "-c", config.as_posix()]
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

    def run(command, check):
        actual = base + (["-t"] if command == ["nginx", "-t"] else ["-s", "reload"])
        return subprocess.run(actual, check=check, capture_output=True, timeout=10, **flags)

    process = None
    try:
        access.configure("", site, snippet, reload=False, run=run)
        process = subprocess.Popen(base + ["-g", "daemon off;"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, **flags)

        def status(source, extra=None):
            conn = http.client.HTTPSConnection("127.0.0.1", tls_port, timeout=2,
                                              context=ssl._create_unverified_context())
            try:
                conn.request("GET", "/login", headers={"X-Test-Source": source, **(extra or {})})
                response = conn.getresponse()
                response.read()
                return response.status
            finally:
                conn.close()

        def eventually(source, expected):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if status(source) == expected:
                        return
                except OSError:
                    pass
                time.sleep(0.05)
            pytest.fail(f"Nginx: source {source} did not return {expected}; " +
                        (tmp_path / "error.log").read_text())

        eventually("192.168.50.5", 200)
        assert status("10.25.0.5") == 200
        assert status("172.20.50.5") == 200
        assert status("100.96.0.5") == 403
        access.configure("100.96.0.0/24", site, snippet, run=run)
        eventually("100.96.0.5", 200)
        assert status("192.168.50.5") == 200
        assert status("203.0.113.5") == 403
        assert status("100.97.0.5") == 403
        assert status("203.0.113.5", {"X-Forwarded-For": "100.96.0.5",
                                   "X-Real-IP": "100.96.0.5"}) == 403
        access.configure("", site, snippet, run=run)
        eventually("100.96.0.5", 403)
        assert status("192.168.50.5") == 200
    finally:
        if process is not None:
            subprocess.run(base + ["-s", "quit"], capture_output=True, **flags)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        backend.shutdown()
        backend.server_close()
        thread.join(timeout=5)
