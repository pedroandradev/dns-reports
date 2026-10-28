#!/usr/bin/env bash
# VALLO | Consulta Acesso de Redes - Proxmox VE 8/9, Debian 13 LXC
# Installer created from the user's existing DNS Reports project.
# Instalar: bash <(curl -fsSL URL_RAW)   (prefer local review before executing)
# Atualizar sem perder registros: bash <(curl -fsSL URL_RAW) --update 103
set -Eeuo pipefail
umask 077
export LC_ALL=C
GREEN='\033[0;32m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; YELLOW='\033[0;33m'; RED='\033[0;31m'; RESET='\033[0m'
info(){ printf '%b  ⚙️  %s%b\n' "$BLUE" "$*" "$RESET"; }
ok(){ printf '%b  ✔️  %s%b\n' "$GREEN" "$*" "$RESET"; }
warn(){ printf '%b  ⚠️  %s%b\n' "$YELLOW" "$*" "$RESET" >&2; }
fail(){ printf '%b  ✖  %s%b\n' "$RED" "$*" "$RESET" >&2; exit 1; }
errline(){ warn "Erro na linha $1. O container nao sera apagado; confira os logs e use --update ID se necessario."; }
trap 'errline "$LINENO"' ERR
[[ $EUID -eq 0 ]] || fail 'Execute como root no shell do Proxmox.'
for cmd in pct pvesh pveam pvesm python3 openssl curl; do command -v "$cmd" >/dev/null || fail "Ausente no Proxmox: $cmd"; done
printf '\n%b' "$CYAN"
cat <<'HEADER'
  __      ___    _     _     ___
  \ \    / /_\  | |   | |   / _ \
   \ \/\/ / _ \ | |__ | |__| (_) |
    \_/\_/_/ \_\|____||____|\___/

         CONSULTA ACESSO DE REDES
HEADER
printf '%b\n' "$RESET"
printf '%b  Painel corporativo | HTTPS | DNS | CSV / Excel / PDF%b\n\n' "$BLUE" "$RESET"
MODE=install
if [[ ${1:-} == --update || ${1:-} == --upgrade ]]; then
  MODE=update
  CTID="${2:-}"
  [[ "$CTID" =~ ^[0-9]+$ ]] || fail 'Informe o CTID: bash instalador.sh --update 103'
elif [[ ${1:-} == --help || ${1:-} == -h ]]; then
  printf 'Instalar: bash instalador.sh\nAtualizar painel existente: bash instalador.sh --update 103\n'
  exit 0
elif [[ $# -gt 0 ]]; then
  fail 'Argumento desconhecido. Use --help.'
fi
STORAGE=local-lvm
TEMPLATE_STORAGE=local
BRIDGE=vmbr0
ADGUARD_URL=http://172.20.0.253
RETENTION=90
APPDIR=/opt/dnsreports
if [[ $MODE == install ]]; then
  CTID="$(pvesh get /cluster/nextid 2>/dev/null | tr -dc '0-9')"
  [[ "$CTID" =~ ^[0-9]+$ ]] || fail 'Nao foi possivel encontrar um ID livre.'
  [[ ! -e "/etc/pve/lxc/${CTID}.conf" ]] || fail "CT ${CTID} ja existe."
  ip -o link show "$BRIDGE" >/dev/null 2>&1 || fail "Bridge ${BRIDGE} nao existe."
  pvesm status | awk 'NR>1{print $1}' | grep -Fx "$STORAGE" >/dev/null || fail "Storage $STORAGE ausente."
  pvesm status | awk 'NR>1{print $1}' | grep -Fx "$TEMPLATE_STORAGE" >/dev/null || fail "Storage de templates $TEMPLATE_STORAGE ausente."
  AVAIL=$(pvesm status | awk -v s="$STORAGE" '$1==s {print $6}')
  [[ "$AVAIL" =~ ^[0-9]+$ ]] && (( AVAIL > 10*1024*1024 )) || fail "Pouco espaco em $STORAGE; mantenha pelo menos 10 GiB livres."
  printf '  📦 Container:   %s (automatico)\n  🐧 Sistema:     Debian 13 | LXC nao privilegiado\n  🌐 Rede:        DHCP na vmbr0\n  🧠 Recursos:    2 CPU / 1024 MB RAM / 8 GB disco\n  🗓️  Historico:    90 dias\n  🛡️  AdGuard:     %s\n\n' "$CTID" "$ADGUARD_URL"
  printf '%b  Informe somente os quatro dados de acesso:%b\n' "$BLUE" "$RESET"
  read -r -p '  Usuario AdGuard [admin]: ' ADGUARD_USER
  ADGUARD_USER="${ADGUARD_USER:-admin}"
  read -r -s -p '  Senha do AdGuard: ' ADGUARD_PASS; printf '\n'
  [[ -n "$ADGUARD_PASS" ]] || fail 'Senha do AdGuard vazia.'
  read -r -p '  Administrador do painel [admin]: ' PANEL_USER
  PANEL_USER="${PANEL_USER:-admin}"
  [[ "$PANEL_USER" =~ ^[a-zA-Z0-9._-]{3,40}$ ]] || fail 'Usuario do painel invalido (3-40 caracteres, letras, numeros, ponto, _ ou -).'
  read -r -s -p '  Senha do administrador (minimo 10 caracteres): ' PANEL_PASS; printf '\n\n'
  (( ${#PANEL_PASS} >= 10 && ${#PANEL_PASS} <= 256 )) || fail 'Senha deve ter de 10 a 256 caracteres.'
else
  pct config "$CTID" >/dev/null || fail "Container $CTID nao existe."
  if [[ $(pct status "$CTID") != *running* ]]; then pct start "$CTID"; fi
  pct exec "$CTID" -- test -f "$APPDIR/config.json" || fail 'O CT informado nao parece conter o painel original.'
  pct exec "$CTID" -- test -f '/etc/systemd/system/dnsreports.service' || fail 'Unidade dnsreports nao encontrada neste CT.'
  info "Atualizando o painel do CT $CTID sem recriar o container ou apagar o banco."
fi
TMP="$(mktemp -d /root/vallo-consulta.XXXXXXXX)"
trap 'rm -rf "${TMP:-}"' EXIT
if [[ $MODE == install ]]; then
  info 'Localizando o template Debian 13...'
  TEMPLATE="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
  if [[ -z "$TEMPLATE" ]]; then
    pveam update
    TEMPLATE="$(pveam available --section system | grep -oE 'debian-13-standard_[^[:space:]]+\.tar\.(zst|gz|xz)' | sort -V | tail -n 1 || true)"
    [[ -n "$TEMPLATE" ]] || fail 'Template Debian 13 indisponivel.'
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
  fi
  ok "Template: $TEMPLATE"
  export ADGUARD_URL ADGUARD_USER ADGUARD_PASS PANEL_USER PANEL_PASS RETENTION
  python3 - "$TMP/config.json" <<'PYCONF'
import os,sys,json,secrets,hashlib
salt=secrets.token_bytes(16); rounds=380000
h=hashlib.pbkdf2_hmac('sha256',os.environ['PANEL_PASS'].encode(),salt,rounds)
settings={
 'adguard_url':os.environ['ADGUARD_URL'],
 'adguard_username':os.environ['ADGUARD_USER'],
 'adguard_password':os.environ['ADGUARD_PASS'],
 'panel_username':os.environ['PANEL_USER'],
 'panel_password_hash':f'pbkdf2_sha256${rounds}${salt.hex()}${h.hex()}',
 'session_secret':secrets.token_hex(48),
