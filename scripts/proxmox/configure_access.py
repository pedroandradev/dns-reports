#!/usr/bin/env python3
"""Manage additive IPv4 VPN ACLs without importing the application or its secrets."""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

SITE = Path("/etc/nginx/sites-available/dnsreports")
SNIPPET = Path("/etc/nginx/snippets/dnsreports-vpn.conf")
HEADER = "# Managed by DNS Reports configure-access; do not edit.\n"
TOKEN = re.compile(r"""\s+|\#[^\n]*|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[{};]|[^\s{};\#'"]+""")


def vpn_networks(value):
    """Require explicit CIDRs, reject an unrestricted rule and directive injection."""
    items = [item for item in re.split(r"[\s,]+", value.strip()) if item]
    if len(items) > 64:
        raise ValueError("Informe no maximo 64 sub-redes VPN.")
    networks = []
    for item in items:
        if "/" not in item:
            raise ValueError(f"Use CIDR explicito: {item}")
        network = ipaddress.ip_network(item, strict=False)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError("VPN: somente CIDRs IPv4 especificos; IPv6 e /0 nao suportados.")
        if str(network) not in networks:
            networks.append(str(network))
    if any(network.prefixlen == 0 for network in ipaddress.collapse_addresses(
            ipaddress.ip_network(item) for item in networks)):
        raise ValueError("A combinacao dos CIDRs nao pode liberar toda a internet IPv4.")
    return networks


def directives(text):
    """Read statement positions and block scopes; refuse unsupported/ambiguous syntax."""
    stack, words, result, servers = [], [], [], set()
    start = 0
    for match in TOKEN.finditer(text):
        token = match.group()
        if token.isspace() or token.startswith("#"):
            continue
        if token == "{":
            if not words:
                raise ValueError("Bloco Nginx sem nome.")
            context = (match.start(), words[0])
            if words[0] == "server":
                if stack:
                    raise ValueError("O site deve conter blocos server no nivel principal.")
                servers.add(context[0])
            stack.append(context)
            words = []
        elif token == "}":
            if words or not stack:
                raise ValueError("Estrutura Nginx nao suportada.")
            stack.pop()
        elif token == ";":
            if not words:
                raise ValueError("Diretiva Nginx vazia.")
            result.append((tuple(stack), words, start))
            words = []
        else:
            if not words:
                start = match.start()
            words.append(token[1:-1] if token.startswith(('"', "'")) else token)
    if stack or words or not servers:
        raise ValueError("Site Nginx incompleto.")
    return result, servers


def plan_site(text, snippet=SNIPPET):
    """Keep all existing allows, TLS and proxy settings; insert before final deny."""
    statements, servers = directives(text)
    rules = {server: [] for server in servers}
    https = False
    include = ["include", snippet.as_posix()]
    for context, words, position in statements:
        name = words[0]
        server = context[0][0] if context and context[0][1] == "server" else None
        direct = len(context) == 1 and server in servers
        if name == "listen" and direct and "ssl" in words:
            https = True
        if name == "satisfy" and words != ["satisfy", "all"]:
            raise ValueError("satisfy personalizado: revise a ACL manualmente.")
        if name == "include":
            if not direct or words != include:
                raise ValueError("Include personalizado: revise a ACL manualmente.")
            rules[server].append((words, position))
        if name in ("allow", "deny"):
            if not direct:
                raise ValueError("ACL em location/escopo personalizado: revise manualmente.")
            rules[server].append((words, position))
    if not https:
        raise ValueError("Site HTTPS nao identificado; revise manualmente.")
    inserts = []
    for acl in rules.values():
        if not acl or acl[-1][0] != ["deny", "all"]:
            raise ValueError("Cada server deve terminar sua ACL com deny all.")
        seen_include = False
        allows = 0
        for words, _ in acl[:-1]:
            if words == include and not seen_include:
                seen_include = True
            elif len(words) == 2 and words[0] == "allow" and not seen_include:
                ipaddress.ip_network(words[1], strict=False)
                allows += 1
            else:
                raise ValueError("ACL personalizada/ordem nao suportada; revise manualmente.")
        if not allows:
            raise ValueError("ACL LAN ausente; revise manualmente.")
        if not seen_include:
            inserts.append(acl[-1][1])
    for position in sorted(inserts, reverse=True):
        text = text[:position] + f'include "{snippet.as_posix()}";\n    ' + text[position:]
    return text


def atomic_write(path, content, metadata=None):
    """Replace a file atomically, keeping owner and permissions when it exists."""
    fd, temporary = tempfile.mkstemp(prefix=".dnsreports-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777 if metadata else 0o644)
        if metadata and hasattr(os, "chown"):
            os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(value, site=SITE, snippet=SNIPPET, reload=True, run=subprocess.run):
    networks = vpn_networks(value)
    site = site.resolve(strict=True)
    if snippet.is_symlink():
        raise ValueError("O snippet gerenciado nao pode ser um link simbolico.")
    original = site.read_bytes()
    proposed = plan_site(original.decode("utf-8"), snippet).encode("utf-8")
    snippet.parent.mkdir(parents=True, exist_ok=True)
    old_snippet = snippet.read_bytes() if snippet.exists() else None
    if old_snippet is not None and not old_snippet.startswith(HEADER.encode()):
        raise ValueError("Snippet existente nao gerenciado; revise manualmente.")
    desired = (HEADER + "".join(f"allow {network};\n" for network in networks)).encode()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    snapshots = [(site, original, site.stat()),
                 (snippet, old_snippet, snippet.stat() if old_snippet is not None else None)]
    for path, content, _ in snapshots:
        if content is not None:
            shutil.copy2(path, path.with_name(path.name + ".vpn-backup-" + stamp))
    try:
        atomic_write(snippet, desired, snapshots[1][2])
        atomic_write(site, proposed, snapshots[0][2])
        run(["nginx", "-t"], check=True)
        if reload:
            run(["systemctl", "reload", "nginx"], check=True)
    except Exception:
        for path, content, metadata in snapshots:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content, metadata)
        # If reload failed after activation, restore the previous effective policy too.
        if reload:
            run(["nginx", "-t"], check=False)
            run(["systemctl", "reload", "nginx"], check=False)
        raise
    return networks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vpn-cidrs", required=True)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--no-reload", action="store_true")
    args = parser.parse_args()
    try:
        if args.validate:
            print(" ".join(vpn_networks(args.vpn_cidrs)))
        else:
            networks = configure(args.vpn_cidrs, reload=not args.no_reload)
            print("ACL VPN aplicada: " + (", ".join(networks) or "(nenhuma adicional)"))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"[ERRO] {error}\nConsulte docs/VPN.md; configuracao anterior preservada.\n")


if __name__ == "__main__":
    main()
