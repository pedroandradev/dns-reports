"""Bounded, read-only ntopng REST v2 transport. Never log response bodies or credentials."""
import requests


class NtopError(Exception):
    """Safe error text suitable for the collector status."""


class NtopClient:
    def __init__(self, settings, session=None):
        self.root = settings["url"].rstrip("/") + "/lua/rest/v2/get/"
        self.ifid = settings["ifid"]
        self.session = session or requests.Session()
        self.session.auth = (settings["username"], settings["password"])
        self.verify = settings["verify_tls"]

    def close(self):
        self.session.close()

    def get(self, endpoint, **params):
        try:
            response = self.session.get(
                self.root + endpoint, params={"ifid": self.ifid, **params},
                timeout=(5, 15), verify=self.verify, allow_redirects=False)
            if response.is_redirect or response.status_code in (401, 403):
                raise NtopError("Autenticação ou permissão recusada pelo ntopng.")
            response.raise_for_status()
            # Some ntopng builds return a login HTML page with status 200.
            payload = response.json()
            if not isinstance(payload, dict) or payload.get("rc") != 0 or "rsp" not in payload:
                raise NtopError("Resposta da API ntopng inválida ou sem permissão.")
            return payload["rsp"]
        except requests.exceptions.SSLError:
            raise NtopError("Certificado TLS do ntopng não validado.") from None
        except (requests.RequestException, ValueError):
            raise NtopError("Falha de conexão ou resposta inválida do ntopng.") from None

    def pages(self, endpoint, **params):
        rows, seen, page_size = [], set(), 100
        for page in range(1, 101):
            payload = self.get(endpoint, currentPage=page, perPage=page_size, **params)
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise NtopError("Lista da API ntopng inválida.")
            batch = payload["data"]
            if any(not isinstance(row, dict) for row in batch):
                raise NtopError("Registros da API ntopng inválidos.")
            fingerprint = tuple(str(row.get("key") or row.get("ip") or "") for row in batch)
            if batch and fingerprint in seen:
                raise NtopError("Paginação repetida: coleta interrompida sem avançar os contadores.")
            seen.add(fingerprint)
            rows.extend(batch)
            if len(rows) > 10000:
                raise NtopError("Limite de 10.000 registros por coleta atingido.")
            if len(batch) < page_size:
                return rows
        raise NtopError("Limite de páginas atingido; coleta incompleta.")

    def hosts(self):
        return self.pages("host/active.lua", mode="local", sortColumn="ip", sortOrder="asc")

    def applications(self, host):
        return self.get("host/l7/stats.lua", host=host["ip"], vlan=host.get("vlan", 0),
                        collapse_stats="false")

    def flows(self):
        return self.pages("flow/active.lua")
