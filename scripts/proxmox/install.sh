#!/usr/bin/env bash
# Proxmox deployment. Entrypoint: Instalar-DNS-Reports-Proxmox.sh.
set -Eeuo pipefail
umask 077
ROOT="${1:?Diretorio do codigo ausente}"
shift
[[ -f "$ROOT/pyproject.toml" ]] || { echo "[ERRO] Codigo fonte incompleto." >&2; exit 1; }
msg(){ printf '\n[*] %s\n' "$*"; }
die(){ echo "[ERRO] $*" >&2; exit 1; }
MODE=install
if [[ "${1:-}" == "--update" || "${1:-}" == "--upgrade" || "${1:-}" == "--configure-access" ]]; then
  [[ $# -eq 2 && "${2:-}" =~ ^[0-9]+$ ]] || die "Uso: --update CTID ou --configure-access CTID"
  MODE=update
  [[ "$1" == "--configure-access" ]] && MODE=access
  CTID="$2"
elif [[ $# -ne 0 ]]; then die "Uso: sem argumentos, --update CTID ou --configure-access CTID"; fi
for cmd in pct python3; do command -v "$cmd" >/dev/null || die "$cmd nao encontrado no Proxmox"; done
if [[ "$MODE" == access ]]; then
  [[ "${REPORTS_VPN_CIDRS+x}" == x ]] || die "Defina REPORTS_VPN_CIDRS (vazio remove apenas as permissoes VPN gerenciadas)"
fi
# Validate before starting or changing a CT. An unset variable preserves its policy.
if [[ "${REPORTS_VPN_CIDRS+x}" == x ]]; then
  python3 "$ROOT/scripts/proxmox/configure_access.py" --validate --vpn-cidrs="$REPORTS_VPN_CIDRS" >/dev/null
fi
configure_access(){
  # A unique temporary file avoids races between operators; flock serializes changes.
  local helper_path result=0
  helper_path="$(pct exec "$CTID" -- mktemp /tmp/dnsreports-access.XXXXXXXX.py)"
  pct push "$CTID" "$ROOT/scripts/proxmox/configure_access.py" "$helper_path" || result=$?
  if [[ "$result" -eq 0 ]]; then
    pct exec "$CTID" -- flock /run/lock/dnsreports-access.lock python3 "$helper_path" \
      --vpn-cidrs="${REPORTS_VPN_CIDRS:-}" "$@" || result=$?
  fi
  pct exec "$CTID" -- rm -f "$helper_path"
  return "$result"
}
if [[ "$MODE" == access ]]; then
  pct config "$CTID" >/dev/null || die "CT $CTID ausente"
  [[ "$(pct status "$CTID")" == *running* ]] || die "Inicie o CT antes de configurar o acesso"
  configure_access
  msg "Acesso VPN configurado. Valide HTTPS pela VPN e LAN; veja docs/VPN.md."
  exit 0
fi
source "$ROOT/scripts/proxmox/lib/ui.sh"
ui_init 8
if [[ "$MODE" == update ]]; then ui_banner "Atualizando container existente $CTID"; else ui_banner "Instalando container LXC"; fi
APPDIR=/opt/dnsreports
UPDATE_STOPPED=0
BACKUP_DIR=
UPGRADE_HELPER=
UNITS=(dnsreports.service dnsreports-collector.service dnsreports-collector.timer dnsreports-ntopng.service dnsreports-ntopng.timer)
cleanup(){
  ui_cleanup
  [[ -z "${UI_PID:-}" ]] || { kill "$UI_PID" 2>/dev/null || true; wait "$UI_PID" 2>/dev/null || true; }
  rm -f "$ROOT/.adguard-config-tmp.json" "$ROOT/.adguard-source-tmp.tar.gz" "$ROOT/.adguard-openssl-tmp.cnf" "$ROOT/.adguard-ip-tmp"
  if [[ -n "$UPGRADE_HELPER" ]]; then pct exec "$CTID" -- rm -f "$UPGRADE_HELPER" >> "$REPORTS_INSTALL_LOG" 2>&1 || true; fi
}
recover_update(){
  pct exec "$CTID" -- systemctl stop dnsreports.service dnsreports-collector.timer dnsreports-collector.service >> "$REPORTS_INSTALL_LOG" 2>&1 || true
  pct exec "$CTID" -- systemctl disable --now dnsreports-ntopng.timer >> "$REPORTS_INSTALL_LOG" 2>&1 || true
  pct exec "$CTID" -- systemctl stop dnsreports-ntopng.service >> "$REPORTS_INSTALL_LOG" 2>&1 || true
  if pct exec "$CTID" -- test -f "$BACKUP_DIR/manifest.json"; then
    pct exec "$CTID" -- python3 "$UPGRADE_HELPER" restore "$BACKUP_DIR" >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  fi
  pct exec "$CTID" -- systemctl daemon-reload >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  pct exec "$CTID" -- systemctl restart dnsreports.service >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  pct exec "$CTID" -- systemctl enable --now dnsreports-collector.timer >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  if pct exec "$CTID" -- test -f /etc/systemd/system/dnsreports-ntopng.timer; then
    pct exec "$CTID" -- systemctl enable --now dnsreports-ntopng.timer >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  fi
  pct exec "$CTID" -- curl --fail --silent --show-error --connect-timeout 2 --max-time 3 \
    --retry 15 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/login -o /dev/null >> "$REPORTS_INSTALL_LOG" 2>&1 || return 1
  pct exec "$CTID" -- systemctl is-active --quiet dnsreports.service >> "$REPORTS_INSTALL_LOG" 2>&1
}
on_failure(){
  local status="$1"
  trap - ERR
  if [[ -n "${UI_PID:-}" ]]; then
    printf '\n  Aguardando a tarefa em andamento para recuperar com seguranca...\n' >&2
    wait "$UI_PID" 2>/dev/null || true
    UI_PID=
  fi
  ui_cleanup
  printf '\n  [ERRO] Falha na etapa %s: %s\n  Log: %s\n' "$UI_STEP" "$UI_LABEL" "$REPORTS_INSTALL_LOG" >&2
  if [[ "$MODE" == update && "$UPDATE_STOPPED" == 1 ]]; then
    printf '  Recuperando a aplicacao anterior...\n' >&2
    if recover_update; then
      printf '  Aplicacao anterior reiniciada. Confira o painel. Backup: %s\n' "$BACKUP_DIR" >&2
    else
      printf '  Recuperacao requer intervencao. Backup: %s | veja o log.\n' "$BACKUP_DIR" >&2
    fi
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'on_failure "$?"' ERR
trap 'on_failure 130' INT
trap 'on_failure 143' TERM

ui_stage "Validacao e configuracao"
for cmd in pvesh pveam pvesm openssl curl tar; do command -v "$cmd" >/dev/null || die "$cmd nao encontrado no Proxmox"; done
STORAGE="${REPORTS_STORAGE:-local-lvm}"
TEMPLATE_STORAGE="${REPORTS_TEMPLATE_STORAGE:-local}"
BRIDGE="${REPORTS_BRIDGE:-vmbr0}"
TZ_NAME="${REPORTS_TIMEZONE:-$(timedatectl show -p Timezone --value 2>/dev/null || true)}"
TZ_NAME="${TZ_NAME:-Etc/UTC}"
if [[ "$MODE" == install ]]; then
  [[ -f "/usr/share/zoneinfo/$TZ_NAME" ]] || die "Fuso horario invalido: $TZ_NAME"
fi
if [[ "$MODE" == install ]]; then
  CTID="$(pvesh get /cluster/nextid | tr -dc '0-9')"
  [[ "$CTID" =~ ^[0-9]+$ ]] || die "Falha ao obter CTID"
  ip -o link show "$BRIDGE" >> "$REPORTS_INSTALL_LOG" 2>&1 || die "Bridge $BRIDGE ausente"
  pvesm status | awk 'NR>1{print $1}' | grep -Fxq "$STORAGE" || die "Storage $STORAGE ausente"
  pvesm status | awk 'NR>1{print $1}' | grep -Fxq "$TEMPLATE_STORAGE" || die "Storage de templates $TEMPLATE_STORAGE ausente"
  printf '  Debian 13 | DHCP | 2 vCPU | 1 GB RAM | 8 GB | CT %s\n\n' "$CTID"
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
  pct config "$CTID" >> "$REPORTS_INSTALL_LOG" 2>&1 || die "CT $CTID ausente"
  printf '  Atualizacao no mesmo CT. Configuracao, dados, usuarios e Nginx local serao preservados.\n'
fi

# Serialize updates/installations of the same CT on this Proxmox host.
LOCK_DIR="${REPORTS_LOCK_DIR:-/run/lock}"
mkdir -p "$LOCK_DIR"
exec 9>"$LOCK_DIR/dnsreports-$CTID.lock"
flock -n 9 || die "Outra instalacao/atualizacao esta usando o CT $CTID"

create_container(){
  local template
  template="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -1 || true)"
  if [[ -z "$template" ]]; then
    pveam update
    template="$(pveam available --section system | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -1 || true)"
    [[ -n "$template" ]] || die "Template Debian 13 indisponivel"
    pveam download "$TEMPLATE_STORAGE" "$template"
  fi
  umask 022
  pct create "$CTID" "${TEMPLATE_STORAGE}:vztmpl/$template" \
    --hostname dns-reports --ostype debian --unprivileged 1 \
    --rootfs "${STORAGE}:8" --cores 2 --memory 1024 --swap 512 \
    --net0 "name=eth0,bridge=${BRIDGE},ip=dhcp" --timezone "$TZ_NAME" --onboot 1 --start 1
}
prepare_existing(){
  if [[ "$(pct status "$CTID")" != *running* ]]; then pct start "$CTID"; fi
  pct exec "$CTID" -- test -f "$APPDIR/config.json"
  pct exec "$CTID" -- test -x "$APPDIR/venv/bin/python"
}
wait_ip(){
  local address= attempt
  for attempt in $(seq 1 30); do
    address="$(pct exec "$CTID" -- ip -4 -o addr show dev eth0 scope global 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1 || true)"
    if [[ "$address" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
      printf '%s\n' "$address" > "$ROOT/.adguard-ip-tmp"
      return 0
    fi
    sleep 2
  done
  die "CT sem IPv4 na interface eth0"
}
ui_stage "Preparando container"
if [[ "$MODE" == install ]]; then ui_run "Obtendo Debian e criando LXC" create_container; else ui_run "Verificando instalacao existente" prepare_existing; fi
ui_run "Aguardando endereco IPv4" wait_ip
IP="$(cat "$ROOT/.adguard-ip-tmp")"

ui_stage "Dependencias do sistema"
if [[ "$MODE" == install ]]; then
  ui_run "Instalando Python, Nginx e certificados" pct exec "$CTID" -- bash -c \
    'DEBIAN_FRONTEND=noninteractive apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 python3-venv python3-pip nginx openssl ca-certificates curl'
else
  ui_run "Verificando Python e Nginx existentes" pct exec "$CTID" -- bash -c 'python3 --version && nginx -t'
fi

protect_data(){
  pct exec "$CTID" -- systemctl stop dnsreports-collector.timer dnsreports-collector.service dnsreports.service
  pct exec "$CTID" -- bash -c 'if [ -f /etc/systemd/system/dnsreports-ntopng.timer ]; then systemctl stop dnsreports-ntopng.timer dnsreports-ntopng.service; fi'
  pct exec "$CTID" -- python3 "$UPGRADE_HELPER" backup "$BACKUP_DIR"
}
new_configuration(){
  pct exec "$CTID" -- mkdir -p "$APPDIR/data"
  python3 "$ROOT/scripts/proxmox/create_config.py" "$ROOT/.adguard-config-tmp.json"
  pct push "$CTID" "$ROOT/.adguard-config-tmp.json" "$APPDIR/config.json"
}
ui_stage "Protecao de dados"
if [[ "$MODE" == update ]]; then
  UPGRADE_HELPER="$(pct exec "$CTID" -- mktemp /tmp/dnsreports-upgrade.XXXXXXXX.py)"
  pct push "$CTID" "$ROOT/scripts/proxmox/upgrade.py" "$UPGRADE_HELPER" >> "$REPORTS_INSTALL_LOG" 2>&1
  BACKUP_DIR="$APPDIR/backups/update-$(date -u +%Y%m%dT%H%M%SZ)-$(openssl rand -hex 4)"
  UPDATE_STOPPED=1
  ui_run "Pausando servicos e criando backup consistente" protect_data
else
  export ADGUARD_URL ADGUARD_USER ADGUARD_PASS PANEL_USER PANEL_PASS TZ_NAME
  ui_run "Gravando configuracao inicial privada" new_configuration
  unset ADGUARD_PASS PANEL_PASS
fi

deploy_package(){
  pct exec "$CTID" -- bash -c 'id -u dnsreports >/dev/null 2>&1 || useradd --system --home-dir /opt/dnsreports --shell /usr/sbin/nologin dnsreports'
  pct exec "$CTID" -- mkdir -p "$APPDIR/source"
  if [[ "$MODE" == install ]]; then pct exec "$CTID" -- python3 -m venv "$APPDIR/venv"; fi
  tar -C "$ROOT" -czf "$ROOT/.adguard-source-tmp.tar.gz" pyproject.toml README.md src
  pct push "$CTID" "$ROOT/.adguard-source-tmp.tar.gz" /tmp/adguard-source.tar.gz
  pct exec "$CTID" -- tar -xzf /tmp/adguard-source.tar.gz -C "$APPDIR/source"
  pct exec "$CTID" -- rm -f /tmp/adguard-source.tar.gz
  pct exec "$CTID" -- "$APPDIR/venv/bin/pip" install --no-cache-dir --upgrade "$APPDIR/source"
  pct exec "$CTID" -- bash -c '
    chown root:dnsreports /opt/dnsreports /opt/dnsreports/config.json
    chmod 750 /opt/dnsreports
    chmod 640 /opt/dnsreports/config.json
    chown -R root:dnsreports /opt/dnsreports/venv /opt/dnsreports/source
    chmod -R g+rX /opt/dnsreports/venv /opt/dnsreports/source
    chown -R dnsreports:dnsreports /opt/dnsreports/data
    chmod 750 /opt/dnsreports/data
  '
}
ui_stage "Aplicacao Python"
ui_run "Instalando DNS, coleta ntopng e dependencias Python" deploy_package

services_and_schema(){
  local unit
  for unit in "${UNITS[@]}"; do pct push "$CTID" "$ROOT/scripts/proxmox/systemd/$unit" "/etc/systemd/system/$unit"; done
  pct exec "$CTID" -- systemctl daemon-reload
  pct exec "$CTID" -- runuser -u dnsreports -- "$APPDIR/venv/bin/python" -c \
    'from adguard_dns_reports.database import db_connect,create_schema; c=db_connect(); create_schema(c); c.close()'
}
ui_stage "Servicos e banco"
ui_run "Aplicando migracoes aditivas e configurando coletores" services_and_schema

new_proxy(){
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
  pct exec "$CTID" -- openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
    -keyout /etc/nginx/ssl/dnsreports.key -out /etc/nginx/ssl/dnsreports.crt \
    -config /root/dnsreports-cert.cnf -extensions v3_req
  pct exec "$CTID" -- chmod 600 /etc/nginx/ssl/dnsreports.key
  pct push "$CTID" "$ROOT/scripts/proxmox/nginx/dnsreports.conf" /etc/nginx/sites-available/dnsreports
  pct exec "$CTID" -- bash -c 'rm -f /etc/nginx/sites-enabled/default; ln -sfn /etc/nginx/sites-available/dnsreports /etc/nginx/sites-enabled/dnsreports'
  configure_access --no-reload
}
ui_stage "Proxy HTTPS"
if [[ "$MODE" == install ]]; then ui_run "Configurando HTTPS e permissoes de rede" new_proxy; else ui_run "Validando Nginx local sem substituir ACL ou certificado" pct exec "$CTID" -- nginx -t; fi

start_services(){
  pct exec "$CTID" -- systemctl enable dnsreports.service dnsreports-collector.timer dnsreports-ntopng.timer nginx
  pct exec "$CTID" -- systemctl reset-failed dnsreports.service || true
  pct exec "$CTID" -- systemctl restart dnsreports.service
  pct exec "$CTID" -- curl --fail --silent --show-error --connect-timeout 2 --max-time 3 \
    --retry 15 --retry-connrefused --retry-delay 1 http://127.0.0.1:8088/login -o /dev/null
  pct exec "$CTID" -- systemctl enable --now nginx
  pct exec "$CTID" -- systemctl reload nginx
  pct exec "$CTID" -- systemctl start dnsreports-collector.timer dnsreports-ntopng.timer
  pct exec "$CTID" -- systemctl is-active --quiet dnsreports.service
  pct exec "$CTID" -- systemctl is-active --quiet nginx
}
ui_stage "Inicializacao e validacao"
ui_run "Iniciando painel e validando resposta HTTP" start_services
if [[ "$MODE" == update && "${REPORTS_VPN_CIDRS+x}" == x ]]; then
  ui_run "Aplicando lista VPN solicitada" configure_access
fi
UPDATE_STOPPED=0
ui_finish "DNS-REPORTS pronto"
printf '\n  Container: %s\n  Acesso: https://%s/\n' "$CTID" "$IP"
[[ -z "$BACKUP_DIR" ]] || printf '  Backup no CT: %s\n' "$BACKUP_DIR"
printf '  ntopng: entre com o administrador e abra Integracao ntopng.\n  Log detalhado: %s\n' "$REPORTS_INSTALL_LOG"
