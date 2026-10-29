"""Validate and persist administrator-supplied ntopng configuration."""
import ipaddress
import json
import uuid
from urllib.parse import urlsplit

DEFAULTS = {"enabled": False, "url": "", "username": "", "password": "",
            "ifid": 0, "verify_tls": True, "local_networks": [], "locations": [],
            "source": "", "revision": "", "session_idle_seconds": 120, "dns_enrichment": True}


def load_settings(conn):
    row = conn.execute("SELECT value FROM traffic_settings WHERE key='ntopng'").fetchone()
    return {**DEFAULTS, **(json.loads(row[0]) if row else {})}


def networks(text):
    items = text.replace(",", " ").split()
    if len(items) > 64:
        raise ValueError("Use no máximo 64 redes.")
    result = []
    for item in items:
        network = ipaddress.ip_network(item, strict=False)
        if network.prefixlen == 0:
            raise ValueError("Informe as redes internas específicas, sem /0.")
        if str(network) not in result:
            result.append(str(network))
    return result


def location_rules(text):
    result, used = [], set()
    for line in text.splitlines():
        if not line.strip():
            continue
        name, separator, cidrs = line.partition("=")
        name = name.strip()
        if not separator or not name or len(name) > 80:
            raise ValueError("Locais: use Nome do local = CIDR, CIDR.")
        parsed = networks(cidrs)
        if not parsed:
            raise ValueError("Cada local precisa de uma rede.")
        for cidr in parsed:
            if cidr in used:
                raise ValueError("Uma rede não pode pertencer a dois locais.")
            used.add(cidr)
            result.append({"name": name, "network": cidr})
    if len(result) > 128:
        raise ValueError("Use no máximo 128 regras de local.")
    return result


def validate(form, previous):
    enabled = form.get("enabled") == "on"
    url = form.get("url", "").strip().rstrip("/")
    username = form.get("username", "").strip()
    supplied_password = form.get("password", "")
    if (previous["password"] and not supplied_password and
            (url != previous["url"] or username != previous["username"])):
        raise ValueError("Informe novamente a senha ao alterar URL ou usuário.")
    password = supplied_password or previous["password"]
    try:
        ifid = int(form.get("ifid", "0"))
        idle = int(form.get("session_idle_seconds", previous.get("session_idle_seconds", 120)))
        parts = urlsplit(url)
        _ = parts.port
    except ValueError:
        raise ValueError("URL ou identificador de interface inválido.") from None
    if not 60 <= idle <= 3600:
        raise ValueError("A confirmação de ausência deve ser entre 60 e 3.600 segundos.")
    if not 0 <= ifid <= 1000000:
        raise ValueError("Interface inválida.")
    if url and (parts.scheme not in ("http", "https") or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or any(ch.isspace() for ch in url)):
        raise ValueError("Use uma URL HTTP/HTTPS sem senha, query ou fragmento.")
    if len(url) > 500 or len(username) > 120 or len(password) > 1024:
        raise ValueError("Configuração excede os limites permitidos.")
    local = networks(form.get("local_networks", ""))
    locations = location_rules(form.get("locations", ""))
    if enabled and not (url and username and password and local):
        raise ValueError("Para habilitar, informe URL, usuário, senha e redes internas.")
    settings = {**previous, "enabled": enabled, "url": url, "username": username,
                "password": password, "ifid": ifid, "session_idle_seconds": idle,
                "dns_enrichment": (form.get("dns_enrichment") == "on"
                                   if form.get("dns_enrichment_present") == "1"
                                   else previous.get("dns_enrichment", True)),
                "verify_tls": form.get("verify_tls") == "on",
                "local_networks": local, "locations": locations, "revision": uuid.uuid4().hex}
    # A new source prevents comparisons across appliances, interfaces or network policies.
    identity = ("url", "ifid", "local_networks")
    if (not settings["source"] or (enabled and not previous["enabled"]) or
            any(settings[key] != previous[key] for key in identity)):
        settings["source"] = uuid.uuid4().hex
    return settings


def save_settings(conn, settings):
    conn.execute("INSERT INTO traffic_settings(key,value) VALUES('ntopng',?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (json.dumps(settings, ensure_ascii=False),))
    conn.commit()


def local_name(ip, rules):
    address = ipaddress.ip_address(ip)
    matching = [(ipaddress.ip_network(rule["network"]).prefixlen, rule["name"])
                for rule in rules if address in ipaddress.ip_network(rule["network"])]
    return max(matching, default=(-1, "Sem local"))[1]
