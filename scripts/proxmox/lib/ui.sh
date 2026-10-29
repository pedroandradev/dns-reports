#!/usr/bin/env bash
# Original compact terminal UI for DNS-REPORTS.
ui_init(){
  UI_STEP=0 UI_TOTAL="${1:-8}" UI_LABEL= UI_PID=
  UI_TTY=0
  [[ -t 1 && "${TERM:-dumb}" != dumb && "${REPORTS_NO_COLOR:-0}" != 1 ]] && UI_TTY=1
  REPORTS_INSTALL_LOG="${REPORTS_INSTALL_LOG:-/var/log/dnsreports/install-$(date -u +%Y%m%dT%H%M%SZ)-$$.log}"
  mkdir -p "$(dirname "$REPORTS_INSTALL_LOG")"
  [[ ! -L "$REPORTS_INSTALL_LOG" ]] || { echo "[ERRO] Log nao pode ser link simbolico." >&2; return 1; }
  (umask 077; touch "$REPORTS_INSTALL_LOG")
  chmod 600 "$REPORTS_INSTALL_LOG"
  export REPORTS_INSTALL_LOG
}
ui_banner(){
  [[ "$UI_TTY" == 1 ]] && printf '\033[1;36m'
  cat <<'BANNER'

 ____  _   _ ____        ____  _____ ____   ___  ____ _____ ____
|  _ \| \ | / ___|      |  _ \| ____|  _ \ / _ \|  _ \_   _/ ___|
| | | |  \| \___ \ _____| |_) |  _| | |_) | | | | |_) || | \___ \
| |_| | |\  |___) |_____|  _ <| |___|  __/| |_| |  _ < | |  ___) |
|____/|_| \_|____/      |_| \_\_____|_|    \___/|_| \_\|_| |____/

BANNER
  [[ "$UI_TTY" == 1 ]] && printf '\033[0m'
  printf '  DNS-REPORTS | %s\n  Log detalhado: %s\n\n' "$1" "$REPORTS_INSTALL_LOG"
}
ui_stage(){
  UI_STEP=$((UI_STEP + 1))
  UI_LABEL="$1"
  printf '\n  Etapa %s/%s - %s\n' "$UI_STEP" "$UI_TOTAL" "$UI_LABEL"
}
ui_line(){
  local filled=$(( (UI_STEP - 1) * 20 / UI_TOTAL )) i
  (( filled < 0 )) && filled=0
  printf '  ['
  for ((i=0;i<20;i++)); do
    if (( i < filled )); then printf '#'; else printf '.'; fi
  done
  printf '] %s %s' "$1" "$2"
}
ui_cleanup(){
  if [[ "$UI_TTY" == 1 ]]; then printf '\033[?25h'; fi
}
ui_run(){
  local label="$1" status=0 spin='|/-\' tick=0
  shift
  printf '\n[%s] %s\n' "$(date -u +%FT%TZ)" "$label" >> "$REPORTS_INSTALL_LOG"
  if [[ "${REPORTS_VERBOSE:-0}" == 1 ]]; then
    "$@" 2>&1 | tee -a "$REPORTS_INSTALL_LOG"
    return "${PIPESTATUS[0]}"
  fi
  ( trap - ERR EXIT INT TERM; set -e; "$@" ) >> "$REPORTS_INSTALL_LOG" 2>&1 &
  UI_PID=$!
  if [[ "$UI_TTY" == 1 ]]; then
    printf '\033[?25l'
    while kill -0 "$UI_PID" 2>/dev/null; do
      printf '\r\033[K'; ui_line "${spin:tick%4:1}" "$label"
      tick=$((tick + 1)); sleep 0.15
    done
  else
    printf '  [...] %s\n' "$label"
  fi
  if wait "$UI_PID"; then status=0; else status=$?; fi
  UI_PID=
  if [[ "$UI_TTY" == 1 ]]; then printf '\r\033[K'; fi
  if [[ "$status" == 0 ]]; then
    ui_line '[OK]' "$label"; printf '\n'
  else
    ui_line '[FALHOU]' "$label"; printf '\n  Consulte: %s\n' "$REPORTS_INSTALL_LOG"
  fi
  ui_cleanup
  return "$status"
}
ui_finish(){
  printf '\n  [####################] [OK] %s\n' "$1"
  ui_cleanup
}
