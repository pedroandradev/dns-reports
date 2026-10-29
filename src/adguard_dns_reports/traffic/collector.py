"""Collect prospective measurements and observed flows from ntopng Community REST v2."""
import hashlib
import ipaddress
import json
import time
from datetime import datetime, timezone

from ..config import config
from ..database import create_schema, db_connect
from .client import NtopClient, NtopError
from .settings import load_settings, local_name
from .storage import counter, metadata
from .access import observe, complete_snapshot
from .names import reported_domain
from ..dns_evidence import candidates, prune


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError("Contador inválido.")
    result = int(value)
    if not 0 <= result < 2**63:
        raise ValueError("Contador fora do limite.")
    return result


def text(value, maximum=160):
    return str(value or "")[:maximum]


def identity(host, settings):
    ip = str(ipaddress.ip_address(host["ip"]))
    vlan = number(host.get("vlan", 0))
    mac = text(host.get("mac"), 40).lower()
    generation = str(number(host["first_seen"]))
    key = f"{ip}@{vlan}@{mac}"
    return key, generation, {
        "ip": ip, "vlan": vlan, "mac": mac, "hostname": text(host.get("name")) or ip,
        "location": local_name(ip, settings["locations"])
    }


def in_networks(ip, networks):
    address = ipaddress.ip_address(ip)
    return any(address in network for network in networks)


def flow_record(flow, settings, hosts, networks):
    client, server = flow["client"], flow["server"]
    cip, sip = str(ipaddress.ip_address(client["ip"])), str(ipaddress.ip_address(server["ip"]))
    c_local, s_local = in_networks(cip, networks), in_networks(sip, networks)
    if not (c_local or s_local):
        return None
    local, remote = (client, server) if c_local else (server, client)
    lip, rip = (cip, sip) if c_local else (sip, cip)
    vlan = number(flow.get("vlan", 0))
    device = hosts.get((lip, vlan), {
        "ip": lip, "mac": "", "hostname": text(local.get("name")) or lip,
        "vlan": vlan, "location": local_name(lip, settings["locations"])
    })
    # Do not trust server.is_dhcp/is_broadcast from active.lua: ntopng 6.6 can copy
    # those flags from the client. Use explicit network policy and actual addresses.
    remote_address = ipaddress.ip_address(rip)
    public_unicast = (remote_address.is_global and not remote_address.is_multicast
                      and not remote_address.is_loopback and not remote_address.is_link_local
                      and not remote_address.is_unspecified and not remote_address.is_reserved)
    scope = "internet" if not (c_local and s_local) and public_unicast else "interno"
    first, last, total = number(flow["first_seen"]), number(flow["last_seen"]), number(flow["bytes"])
    if first > last:
        raise ValueError("Intervalo de fluxo inválido.")
    protocol = flow.get("protocol") or {}
    app, l4 = text(protocol.get("l7")) or "Unknown", text(protocol.get("l4"), 20)
    raw_key = [flow.get("key"), flow.get("hash_id"), cip, number(client["port"]),
               sip, number(server["port"]), vlan, l4, first]
    key = hashlib.sha256(json.dumps(raw_key).encode()).hexdigest()
    uid = hashlib.sha256((settings["source"] + key).encode()).hexdigest()
    hostname = reported_domain(flow.get("flow_hostname")) if c_local else ""
    device = {**device, "name_source": "flow" if hostname else "reported"}
    return (key, uid, device, scope, app, first, last, total, rip,
            hostname or text(remote.get("name")) or rip, number(remote["port"]), l4)


def collect(client=None, now=None):
    use_clock = now is None
    now = int(time.time()) if now is None else now
    conn = db_connect()
    create_schema(conn)
    settings = load_settings(conn)
    if not settings["enabled"]:
        conn.close()
        return {"enabled": False}
    networks = [ipaddress.ip_network(cidr) for cidr in settings["local_networks"]]
    api = client or NtopClient(settings)
    deadline = time.monotonic() + 45
    failures, host_data, flow_data = 0, [], None
    try:
        hosts = api.hosts()
        cursor_row = conn.execute("SELECT value FROM metadata WHERE key='ntopng_app_cursor'").fetchone()
        cursor = (int(cursor_row[0]) if cursor_row else 0) % max(1, len(hosts))
        apps_attempted = 0
        for host in hosts[cursor:] + hosts[:cursor]:
            try:
                key, generation, device = identity(host, settings)
                if not in_networks(device["ip"], networks):
                    continue
                sent = number(host["bytes"]["sent"])
                received = number(host["bytes"]["recvd"])
                total = number(sent + received)
                apps = None
                if time.monotonic() < deadline:
                    apps_attempted += 1
                    try:
                        raw_apps = api.applications(host)
                        if not isinstance(raw_apps, list):
                            raise ValueError("Aplicações inválidas.")
                        apps = [(text(row["label"]), number(row["value"])) for row in raw_apps]
                    except (NtopError, ValueError, KeyError, TypeError, OverflowError):
                        failures += 1
                else:
                    failures += 1
                host_data.append((key, generation, device, sent, received, total, apps))
            except (ValueError, KeyError, TypeError, OverflowError):
                failures += 1
        try:
            flow_data = api.flows()
        except NtopError:
            failures += 1
        if use_clock:
            now = int(time.time())
        # Configuration may have changed while network requests were in progress.
        conn.execute("BEGIN IMMEDIATE")
        current = load_settings(conn)
        if not current["enabled"] or current["revision"] != settings["revision"]:
            conn.rollback()
            return {"discarded": True}
        devices = {}
        for key, generation, device, sent, received, total, apps in host_data:
            devices[(device["ip"], device["vlan"])] = device
            counter(conn, settings["source"], key, "lan", "", now, generation,
                    sent, received, total, device)
            if apps is not None:
                for app, value in apps:
                    counter(conn, settings["source"], key, "lan_app", app, now, generation,
                            0, 0, value, device)
        observed_uids = set()
        flow_complete = flow_data is not None
        for flow in flow_data or []:
            try:
                record = flow_record(flow, settings, devices, networks)
                if not record:
                    continue
                key, uid, device, scope, app, first, last, total, rip, name, port, l4 = record
            except (ValueError, KeyError, TypeError, OverflowError):
                failures += 1
                flow_complete = False
                continue
            dns_names = candidates(conn, record) if settings.get("dns_enrichment", True) else []
            observed_uids.add(observe(conn, record, settings["source"], now, dns_names))
            if scope == "internet":
                # A single counter across application reclassification avoids a reset
                # when nDPI changes e.g. TLS into TLS.Netflix.
                counter(conn, settings["source"], key, "internet", "", now, str(first),
                        0, 0, total, {**device, "application": app})
        prune(conn, now)
        metadata(conn, "ntopng_flow_api", getattr(api, "flow_api", "custom"))
        if flow_complete:
            complete_snapshot(conn, settings["source"], now, observed_uids,
                              int(settings.get("session_idle_seconds", 120)))
            metadata(conn, "ntopng_flow_last_success", now)
            metadata(conn, "ntopng_flow_error", "")
        else:
            metadata(conn, "ntopng_flow_error", "Coleta de fluxos incompleta ou indisponível.")
        ttl = max(1, min(int(config().get("retention_days", 90)), 3660))
        cutoff = now - ttl * 86400
        conn.execute("DELETE FROM traffic_usage WHERE sample_end < ?", (cutoff,))
        connection_days = max(1, min(int(config().get("ntopng_connection_retention_days", 7)), ttl))
        connection_limit = max(1000, min(int(config().get("ntopng_connection_limit", 100000)), 1000000))
        before = conn.total_changes
        conn.execute("DELETE FROM traffic_connections WHERE observed_at < ?", (now - connection_days * 86400,))
        conn.execute("DELETE FROM traffic_connections WHERE uid IN "
                     "(SELECT uid FROM traffic_connections ORDER BY observed_at DESC LIMIT -1 OFFSET ?)",
                     (connection_limit,))
        metadata(conn, "ntopng_connections_removed_last", conn.total_changes - before)
        metadata(conn, "ntopng_connection_days", connection_days)
        metadata(conn, "ntopng_connection_limit", connection_limit)
        metadata(conn, "ntopng_app_cursor", (cursor + max(1, apps_attempted)) % max(1, len(hosts)))
        conn.execute("DELETE FROM traffic_state WHERE sampled_at < ? AND kind='internet'", (now - 600,))
        conn.execute("DELETE FROM traffic_state WHERE sampled_at < ?", (now - 86400,))
        metadata(conn, "ntopng_last_success", datetime.fromtimestamp(now, timezone.utc).isoformat())
        metadata(conn, "ntopng_status", "parcial" if failures else "ok")
        metadata(conn, "ntopng_failures", failures)
        metadata(conn, "ntopng_error", "")
        conn.commit()
        return {"hosts": len(host_data), "flows": len(flow_data or []), "failures": failures}
    except NtopError as error:
        conn.rollback()
        metadata(conn, "ntopng_flow_error", "Coleta indisponível; estado das conexões sem confirmação.")
        metadata(conn, "ntopng_status", "erro")
        metadata(conn, "ntopng_error", str(error))
        conn.commit()
        raise
    finally:
        api.close()
        conn.close()


def main():
    # systemd also serializes the service; a file lock covers manual invocations.
    import fcntl
    from ..config import BASE
    with (BASE / "data" / ".ntopng-collector.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        try:
            result = collect()
            if result.get("enabled") is not False:
                print("ntopng: coleta concluída " + json.dumps(result), flush=True)
        except NtopError as error:
            print("ntopng: " + str(error), flush=True)
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
