"""Strict hostname extraction from ntopng flow metadata, without retaining URL paths."""
import ipaddress
import re
from urllib.parse import urlsplit

GENERIC = {"tls", "ssl", "quic", "http", "https", "tcp", "udp", "unknown", "unidentified",
           "tooearly", "too_early", "pending"}
TECHNICAL = {"icmp", "icmpv6", "stun", "dns", "mdns", "llmnr", "ssdp", "arp", "dhcp",
             "dhcpv6", "ntp", "igmp", "icmp6"}


def reported_domain(value):
    name = str(value or "").strip().lower().rstrip(".")
    try:
        ipaddress.ip_address(name)
        return ""
    except ValueError:
        pass
    if len(name) > 253 or "." not in name:
        return ""
    if not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
               for label in name.split(".")):
        return ""
    return name


def application_name(application):
    parts = str(application or "Unknown").split(".")
    while len(parts) > 1 and parts[0].casefold() in GENERIC | TECHNICAL:
        parts.pop(0)
    return ".".join(parts)


def web_flow(application, port):
    raw = str(application or "").casefold().split(".")[0]
    candidate = application_name(application).casefold()
    return (raw in {"tls", "ssl", "quic", "http", "https"}
            or candidate in GENERIC and port in (80, 443, 8080, 8443))


def flow_hostname(info, application, port):
    """info can also be a DNS question, ICMP description or SSH banner: reject those."""
    if not isinstance(info, str) or len(info) > 8192 or not web_flow(application, port):
        return ""
    value = info.strip()
    direct = reported_domain(value)
    if direct:
        return direct
    if any(ch.isspace() for ch in value) or any(ch in value for ch in "<>\\"):
        return ""
    try:
        parts = urlsplit(value if "://" in value else "//" + value)
        if parts.scheme and parts.scheme not in ("http", "https"):
            return ""
        if parts.username is not None or parts.password is not None:
            return ""
        _ = parts.port
        return reported_domain(parts.hostname)
    except ValueError:
        return ""
