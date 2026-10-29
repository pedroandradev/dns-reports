"""Navigation regressions: one shell, distinct reports and admin-only settings."""
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest

from test_app import client, login
from test_traffic import configured


class Navigation(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.nav = None
        self.sections = {}
        self.current = None
        self.settings_link = None
        self.theme_buttons = 0
        self.export_links = []
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "nav":
            self.nav = attrs.get("aria-label")
            self.sections.setdefault(self.nav, [])
        if tag == "a":
            if self.nav:
                self.current = {**attrs, "label": ""}
                self.sections[self.nav].append(self.current)
            if attrs.get("aria-label") == "Configurações":
                self.settings_link = attrs
            if attrs.get("href", "").startswith("/export/"):
                self.export_links.append(attrs["href"])
        if tag == "button" and "data-theme-toggle" in attrs:
            self.theme_buttons += 1

    def handle_data(self, data):
        if self.current:
            self.current["label"] += data

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None
        elif tag == "nav":
            self.nav = None


REPORTS = [
    ("/acessos", "Visualizador de Acesso"),
    ("/dns", "Consultas de DNS"),
    ("/trafego", "Consumo de rede"),
]


@pytest.mark.parametrize("path", ["/", "/acessos", "/dns", "/trafego", "/configuracoes", "/usuarios", "/integracoes/ntopng"])
def test_all_pages_share_three_distinct_reports_and_active_location(configured, path):
    c, password = configured
    login(c, "owner", password)
    response = c.get(path)
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    nav = Navigation(html)
    reports = nav.sections["Relatórios"]
    assert [(link["href"], link["label"].strip()) for link in reports] == REPORTS
    active = [link["href"] for link in reports if link.get("aria-current") == "page"]
    assert active == (["/acessos"] if path in ("/", "/acessos") else [path] if path in ("/dns", "/trafego") else [])
    assert nav.settings_link["href"] == "/configuracoes"
    assert nav.theme_buttons >= 1
    assert html.count('class="shell"') == 1
    assert html.count('class="sidebar"') == 1
    assert "AdGuard DNS Reports" not in html
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]


def test_settings_groups_integration_users_and_appearance_without_exposing_secret(configured):
    c, password = configured
    login(c, "owner", password)
    html = c.get("/configuracoes").get_data(as_text=True)
    nav = Navigation(html)
    assert [link["href"] for link in nav.sections["Configurações"]] == [
        "/configuracoes", "/integracoes/ntopng", "/usuarios"]
    assert "Gerenciar usuários" in html and "Configurar integração" in html and "Aparência" in html
    assert "test-only-secret" not in html
    assert nav.theme_buttons == 2
    for path in ("/configuracoes", "/usuarios", "/integracoes/ntopng"):
        nav = Navigation(c.get(path).get_data(as_text=True))
        assert [link["href"] for link in nav.sections["Configurações"]
                if link.get("aria-current") == "page"] == [path]


def test_viewer_has_reports_and_theme_but_no_admin_entry_or_settings_access(configured):
    from adguard_dns_reports.database import db_connect
    from adguard_dns_reports.security import password_hash
    from adguard_dns_reports.traffic.settings import load_settings
    c, _ = configured
    conn = db_connect()
    before = load_settings(conn)
    conn.execute("INSERT INTO users(username,password_hash,is_admin) VALUES(?,?,0)",
                 ("viewer", password_hash("viewer-test-password")))
    conn.commit()
    conn.close()
    login(c, "viewer", "viewer-test-password")
    with c.session_transaction() as session:
        session["is_admin"] = True
        csrf = session["csrf"]
    for path, _ in REPORTS:
        nav = Navigation(c.get(path).get_data(as_text=True))
        assert nav.settings_link is None and nav.theme_buttons == 1
        assert len(nav.sections["Relatórios"]) == 3
        assert "Configurações" not in nav.sections
    for path in ("/configuracoes", "/usuarios", "/integracoes/ntopng"):
        assert c.get(path).status_code == 403
        assert c.post(path, data={"csrf": csrf, "action": "save"}).status_code == 403
    conn = db_connect()
    assert load_settings(conn) == before
    conn.close()


def test_dns_exports_keep_filters_once_and_no_duplicate_section_links(configured):
    c, password = configured
    login(c, "owner", password)
    html = c.get("/dns?period=30&client=10.77.0.10&search=example.test").get_data(as_text=True)
    nav = Navigation(html)
    assert len(nav.export_links) == 3
    assert {urlsplit(link).path for link in nav.export_links} == {"/export/csv", "/export/xlsx", "/export/pdf"}
    assert all(parse_qs(urlsplit(link).query) == {
        "period": ["30"], "client": ["10.77.0.10"], "search": ["example.test"]}
        for link in nav.export_links)
    assert 'href="#consultas"' not in html and 'href="#servicos"' not in html


def test_theme_and_favicon_are_available_before_login_with_csp_compatible_scripts(client):
    c, _ = client
    html = c.get("/login").get_data(as_text=True)
    assert Navigation(html).theme_buttons == 1
    assert html.index("theme.js") < html.index("style.css")
    assert c.get("/static/theme.js").status_code == 200
    assert c.get("/static/favicon.svg").status_code == 200
    assert c.get("/configuracoes").status_code == 302
