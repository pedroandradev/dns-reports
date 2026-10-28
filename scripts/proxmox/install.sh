#!/usr/bin/env bash
# Proxmox deployment. Entrypoint: Instalar-DNS-Reports-Proxmox.sh.
set -Eeuo pipefail
umask 077
ROOT="${1:?Diretorio do codigo ausente}"
shift
[[ -f "$ROOT/pyproject.toml" ]] || { echo "[ERRO] Codigo fonte incompleto." >&2; exit 1; }
msg(){ printf '\n[*] %s\n' "$*"; }
die(){ echo "[ERRO] $*" >&2; exit 1; }
for cmd in pct pvesh pveam pvesm python3 openssl curl; do command -v "$cmd" >/dev/null || die "$cmd nao encontrado no Proxmox"; done
MODE=install
if [[ "${1:-}" == "--update" || "${1:-}" == "--upgrade" ]]; then
  MODE=update
  CTID="${2:-}"
  [[ "$CTID" =~ ^[0-9]+$ ]] || die "Uso: --update ID_DO_CT"
elif [[ $# -ne 0 ]]; then die "Uso: sem argumentos (instalar) ou --update CTID"; fi
STORAGE="${REPORTS_STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${REPORTS_TEMPLATE_STORAGE:-local}"
BRIDGE="${REPORTS_BRIDGE:-vmbr0}"
TZ_NAME="${REPORTS_TIMEZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"
TZ_NAME="${TZ_NAME:-Etc/UTC}"
[[ -f "/usr/share/zoneinfo/$TZ_NAME" ]] || die "Fuso horario invalido: $TZ_NAME"
APPDIR=/opt/dnsreports
if [[ "$MODE" == install ]]; then
  CTID="$(pvesh get /cluster/nextid | tr -dc '0-9')"
  [[ "$CTID" =~ ^[0-9]+$ ]] || die "Falha ao obter CTID"
  ip -o link show "$BRIDGE" >/dev/null || die "Bridge $BRIDGE ausente"
  pvesm status | awk 'NR>1{print $1}' | grep -Fxq "$STORAGE" || die "Storage $STORAGE ausente"
  pvesm status | awk 'NR>1{print $1}' | grep -Fxq "$TEMPLATE_STORAGE" || die "Storage de templates $TEMPLATE_STORAGE ausente"
  printf '\nAdGuard DNS Reports | Debian 13 | DHCP | 2 vCPU | 1 GB RAM | 8 GB | 90 dias\n'
  read -r -p 'URL do AdGuard Home (http(s)://IP:PORTA): ' ADGUARD_URL
  ADGUARD_URL="${ADGUARD_URL%/}"
  [[ "$ADGUARD_URL" =~ ^https?://[^[:space:]@]+$ ]] || die "URL invalida"
  read -r -p 'Usuario do AdGuard [admin]: ' ADGUARD_USER
  ADGUARD_USER="${ADGUARD_USER:-admin}"
  read -r -s -p 'Senha do AdGuard: ' ADGUARD_PASS; printf '\n'
  [[ -n "$ADGUARD_PASS" ]] || die "Senha do AdGuard vazia"
  read -r -p 'Administrador do painel [admin]: ' PANEL_USER
  PANEL_USER="${PANEL_USER:-admin}"
  [[ "$PANEL_USER" =~ ^[a-zA-Z0-9._-]{3,40}$ ]] || die "Usuario invalido"
  read -r -s -p 'Senha do administrador (10+ caracteres): ' PANEL_PASS; printf '\n'
  [[ ${#PANEL_PASS} -ge 10 ]] || die "Senha muito curta"
else
  pct config "$CTID" >/dev/null || die "CT $CTID ausente"
  if [[ "$(pct status "$CTID")" != *running* ]]; then pct start "$CTID"; fi
  pct exec "$CTID" -- test -f "$APPDIR/config.json" || die "Este CT nao possui configuracao do painel"
fi
msg "Preparando container $CTID"
if [[ "$MODE" == install ]]; then
  TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -1 || true)"
  if [[ -z "$TEMPLATE" ]]; then
    pveam update
    TEMPLATE="$(pveam available --section system | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -1 || true)"
    [[ -n "$TEMPLATE" ]] || die "Template Debian 13 indisponivel"
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi
  umask 022
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/$TEMPLATE" \
    --hostname adguard-dns-reports --ostype debian --unprivileged 1 \
    --rootfs "${STORAGE}:8" --cores 2 --memory 1024 --swap 512 \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" \
    --timezone "$TZ_NAME" --onboot 1 --start 1
  umask 077
  pct exec "$CTID" -- bash -c 'DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv python3-pip nginx openssl ca-certificates curl'
  pct exec "$CTID" -- mkdir -p "$APPDIR/data"
  pct exec "$CTID" -- python3 -m venv "$APPDIR/venv"
  export ADGUARD_URL ADGUARD_USER ADGUARD_PASS PANEL_USER PANEL_PASS TZ_NAME
  python3 - "$ROOT" <<'PYCONFIG'
import json, os, secrets, hashlib, sys
from pathlib import Path
salt = secrets.token_bytes(16)
rounds = 380000
hashed = hashlib.pbkdf2_hmac("sha256",os.environ["PANEL_PASS"].encode(),salt,rounds)
configuration = {
    "adguard_url": os.environ["ADGUARD_URL"],
    "adguard_username": os.environ["ADGUARD_USER"],
    "adguard_password": os.environ["ADGUARD_PASS"],
    "panel_username": os.environ["PANEL_USER"],
    "panel_password_hash": f"pbkdf2_sha256${rounds}${salt.hex()}${hashed.hex()}",
    "session_secret": secrets.token_hex(48),
    "retention_days": 90,
    "initial_history_days": 7,
    "timezone": os.environ["TZ_NAME"]
}
target = Path(sys.argv[1]) / ".adguard-config-tmp.json"
target.write_text(json.dumps(configuration,ensure_ascii=False),encoding="utf-8")
target.chmod(0o600)
PYCONFIG
  unset ADGUARD_PASS PANEL_PASS
else
  msg "Backup de configuracao e banco SQLite existente"
  pct exec "$CTID" -- bash -c 'cp -a /opt/dnsreports/config.json /opt/dnsreports/config.json.pre-refactor && if [ -f /opt/dnsreports/data/queries.sqlite3 ]; then cp -a /opt/dnsreports/data/queries.sqlite3 /opt/dnsreports/data/queries.sqlite3.pre-refactor; fi'
fi
# The repository is deployed as a Python package. No source generation here.
pct exec "$CTID" -- mkdir -p "$APPDIR/source"
# Copy one versioned archive with pct push; do not rely on LXC stdin forwarding.
tar -C "$ROOT" -czf "$ROOT/.adguard-source-tmp.tar.gz" pyproject.toml README.md src
pct push "$CTID" "$ROOT/.adguard-source-tmp.tar.gz" /tmp/adguard-source.tar.gz
rm -f "$ROOT/.adguard-source-tmp.tar.gz"
pct exec "$CTID" -- tar -xzf /tmp/adguard-source.tar.gz -C "$APPDIR/source"
pct exec "$CTID" -- rm -f /tmp/adguard-source.tar.gz
pct exec "$CTID" -- "$APPDIR/venv/bin/pip" install --no-cache-dir --upgrade "$APPDIR/source"
if [[ "$MODE" == install ]]; then
  pct push "$CTID" "$ROOT/.adguard-config-tmp.json" "$APPDIR/config.json"
  rm -f "$ROOT/.adguard-config-tmp.json"
  pct exec "$CTID" -- bash -c 'id -u dnsreports >/dev/null 2>&1 || useradd --system --home-dir /opt/dnsreports --shell /usr/sbin/nologin dnsreports'
else
  pct exec "$CTID" -- python3 -c 'import json, pathlib; p=pathlib.Path("/opt/dnsreports/config.json"); x=json.loads(p.read_text()); x["retention_days"]=90; x.setdefault("timezone", "Etc/UTC"); p.write_text(json.dumps(x));'
fi
pct exec "$CTID" -- bash -c '
chown root:dnsreports /opt/dnsreports
chmod 750 /opt/dnsreports
chown root:dnsreports /opt/dnsreports/config.json
chmod 640 /opt/dnsreports/config.json
chown -R root:dnsreports /opt/dnsreports/venv /opt/dnsreports/source
chmod -R g+rX /opt/dnsreports/venv /opt/dnsreports/source
chown dnsreports:dnsreports /opt/dnsreports/data
chmod 750 /opt/dnsreports/data
'
for unit in dnsreports.service dnsreports-collector.service dnsreports-collector.timer; do
  if [[ "$MODE" == install ]]; then pct push "$CTID" "$ROOT/scripts/proxmox/systemd/$unit" "/etc/systemd/system/$unit"; fi
done
if [[ "$MODE" == install ]]; then
  IP=""
  for _ in $(seq 1 25); do
    IP="$(pct exec "$CTID" -- ip -4 -o addr show dev eth0 scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
    [[ "$IP" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && break
    IP=""; sleep 2
  done
  [[ -n "$IP" ]] || die "CT sem IPv4 DHCP na interface eth0"
  pct exec "$CTID" -- mkdir -p /etc/nginx/ssl
  cat > "$ROOT/.adguard-openssl-tmp.cnf" <<EOF
[req]
distinguished_name=dn
x509_extensions=v3_req
prompt=no
[dn]
CN=$IP
[v3_req]
subjectAltName=IP:$IP
EOF
  pct push "$CTID" "$ROOT/.adguard-openssl-tmp.cnf" /root/dnsreports-cert.cnf
  rm -f "$ROOT/.adguard-openssl-tmp.cnf"
  pct exec "$CTID" -- openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout /etc/nginx/ssl/dnsreports.key -out /etc/nginx/ssl/dnsreports.crt \
    -config /root/dnsreports-cert.cnf -extensions v3_req
  pct exec "$CTID" -- chmod 600 /etc/nginx/ssl/dnsreports.key
  pct push "$CTID" "$ROOT/scripts/proxmox/nginx/dnsreports.conf" /etc/nginx/sites-available/dnsreports
  pct exec "$CTID" -- bash -c 'rm -f /etc/nginx/sites-enabled/default; ln -sfn /etc/nginx/sites-available/dnsreports /etc/nginx/sites-enabled/dnsreports'
else
  IP="$(pct exec "$CTID" -- ip -4 -o addr show dev eth0 scope global | awk '{print $4}' | cut -d/ -f1 | head -n1)"
  # Replace existing service units to point at installed Python package,
  # preserving nginx TLS certificates and locally customized reverse proxy.
  for unit in dnsreports.service dnsreports-collector.service dnsreports-collector.timer; do
    pct push "$CTID" "$ROOT/scripts/proxmox/systemd/$unit" "/etc/systemd/system/$unit"
  done
fi
msg "Reiniciando servicos"
pct exec "$CTID" -- systemctl daemon-reload
pct exec "$CTID" -- systemctl enable dnsreports.service dnsreports-collector.timer nginx >/dev/null
pct exec "$CTID" -- systemctl reset-failed dnsreports.service || true
pct exec "$CTID" -- systemctl restart dnsreports.service
pct exec "$CTID" -- systemctl enable --now dnsreports-collector.timer >/dev/null
pct exec "$CTID" -- nginx -t
pct exec "$CTID" -- systemctl restart nginx
pct exec "$CTID" -- systemctl is-active --quiet dnsreports.service || die "Veja: pct exec $CTID -- journalctl -u dnsreports -n 50"
pct exec "$CTID" -- systemctl is-active --quiet nginx || die "Nginx nao iniciado"
msg "Instalacao/atualizacao concluida. Acesso: https://$IP/ | CT: $CTID"
