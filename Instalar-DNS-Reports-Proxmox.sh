#!/usr/bin/env bash
# Single public entrypoint: fetch the modular installer and retain private diagnostics.
set -Eeuo pipefail
umask 077
[[ ${EUID:-1} -eq 0 ]] || { echo "[ERRO] Execute como root no host Proxmox." >&2; exit 1; }
for cmd in pct curl tar; do command -v "$cmd" >/dev/null || { echo "[ERRO] $cmd indisponivel no host Proxmox." >&2; exit 1; }; done
REF="${REPORTS_REF:-main}"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$REF" != *".."* ]] || { echo "[ERRO] Ref invalida." >&2; exit 1; }
REPORTS_INSTALL_LOG="${REPORTS_INSTALL_LOG:-/var/log/dnsreports/install-$(date -u +%Y%m%dT%H%M%SZ)-$$.log}"
mkdir -p "$(dirname "$REPORTS_INSTALL_LOG")"
[[ ! -L "$REPORTS_INSTALL_LOG" ]] || { echo "[ERRO] Log nao pode ser link simbolico." >&2; exit 1; }
touch "$REPORTS_INSTALL_LOG"; chmod 600 "$REPORTS_INSTALL_LOG"
export REPORTS_INSTALL_LOG
TEMP="$(mktemp -d /tmp/adguard-reports.XXXXXXXX)"
DOWNLOAD_PID=
cleanup(){
  if [[ -n "$DOWNLOAD_PID" ]]; then kill "$DOWNLOAD_PID" 2>/dev/null || true; wait "$DOWNLOAD_PID" 2>/dev/null || true; fi
  [[ ! -t 1 ]] || printf '\033[?25h'
  rm -rf "$TEMP"
}
trap cleanup EXIT
printf '\n  DNS-REPORTS | Preparando instalador (%s)\n' "$REF"
curl -fLsS --retry 3 "https://codeload.github.com/pedroandradev/dns-reports/tar.gz/refs/heads/$REF" \
  -o "$TEMP/repo.tar.gz" >> "$REPORTS_INSTALL_LOG" 2>&1 &
DOWNLOAD_PID=$!
if [[ -t 1 && "${TERM:-dumb}" != dumb && "${REPORTS_NO_COLOR:-0}" != 1 ]]; then
  spin='|/-\'; tick=0
  while kill -0 "$DOWNLOAD_PID" 2>/dev/null; do
    printf '\r  [....................] %s Baixando codigo do projeto' "${spin:tick%4:1}"
    tick=$((tick + 1)); sleep 0.15
  done
else
  printf '  [...] Baixando codigo do projeto\n'
fi
if wait "$DOWNLOAD_PID"; then DOWNLOAD_PID=; else
  DOWNLOAD_PID=
  printf '\n  [ERRO] Download falhou. Log: %s\n' "$REPORTS_INSTALL_LOG" >&2
  exit 1
fi
printf '\n'
if ! tar -tzf "$TEMP/repo.tar.gz" >> "$REPORTS_INSTALL_LOG" 2>&1; then
  printf '  [ERRO] Pacote invalido. Log: %s\n' "$REPORTS_INSTALL_LOG" >&2
  exit 1
fi
mkdir -p "$TEMP/repo"
if ! tar -xzf "$TEMP/repo.tar.gz" -C "$TEMP/repo" --strip-components=1 >> "$REPORTS_INSTALL_LOG" 2>&1; then
  printf '  [ERRO] Extracao falhou. Log: %s\n' "$REPORTS_INSTALL_LOG" >&2
  exit 1
fi
[[ -f "$TEMP/repo/scripts/proxmox/install.sh" ]] || { echo "[ERRO] Pacote incompleto." >&2; exit 1; }
bash -n "$TEMP/repo/scripts/proxmox/install.sh"
bash "$TEMP/repo/scripts/proxmox/install.sh" "$TEMP/repo" "$@"
