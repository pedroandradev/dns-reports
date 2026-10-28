#!/usr/bin/env bash
# Thin bootstrap; application source lives in src/ and deployment in scripts/proxmox/.
set -Eeuo pipefail
[[ ${EUID:-1} -eq 0 ]] || { echo "[ERRO] Execute como root no host Proxmox." >&2; exit 1; }
command -v pct >/dev/null || { echo "[ERRO] Execute no host Proxmox VE." >&2; exit 1; }
command -v curl >/dev/null || { echo "[ERRO] Instale curl." >&2; exit 1; }
command -v tar >/dev/null || { echo "[ERRO] tar nao esta disponivel." >&2; exit 1; }
REF="${REPORTS_REF:-main}"
[[ "$REF" =~ ^[A-Za-z0-9._/-]+$ && "$REF" != *".."* ]] || { echo "[ERRO] Ref invalida." >&2; exit 1; }
TEMP="$(mktemp -d /tmp/adguard-reports.XXXXXXXX)"
trap 'rm -rf "$TEMP"' EXIT
echo "[*] Obtendo pacote do repositorio (branch $REF)..."
curl -fLsS --retry 3 "https://codeload.github.com/pedroandradev/dns-reports/tar.gz/refs/heads/$REF" -o "$TEMP/repo.tar.gz"
tar -tzf "$TEMP/repo.tar.gz" >/dev/null
mkdir -p "$TEMP/repo"
tar -xzf "$TEMP/repo.tar.gz" -C "$TEMP/repo" --strip-components=1
[[ -f "$TEMP/repo/scripts/proxmox/install.sh" ]] || { echo "[ERRO] Pacote incompleto." >&2; exit 1; }
bash -n "$TEMP/repo/scripts/proxmox/install.sh"
bash "$TEMP/repo/scripts/proxmox/install.sh" "$TEMP/repo" "$@"
