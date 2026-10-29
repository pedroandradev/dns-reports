"""Installer terminal progress and in-place deployment orchestration."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("REPORTS_TEST_BASH") or shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="bash unavailable")


def shell(code, tmp_path, **env):
    return subprocess.run([BASH, "-c", code], capture_output=True, text=True, timeout=20,
                          env={**os.environ, "REPORTS_INSTALL_LOG": (tmp_path / "install.log").as_posix(),
                               "TEST_ROOT": ROOT.as_posix(), "REPORTS_NO_COLOR": "1", **env})


def test_compact_progress_logs_details_without_printing_them(tmp_path):
    result = shell(r"""
set -Eeuo pipefail
source "$TEST_ROOT/scripts/proxmox/lib/ui.sh"
ui_init 2
ui_banner "Instalando container"
ui_stage "Dependencias"
ui_run "Instalando componentes" bash -c 'echo DETAIL_INSTALL_OUTPUT; sleep 0.02'
ui_stage "Validacao"
ui_run "Verificando painel" true
ui_finish "DNS-REPORTS pronto"
""", tmp_path)
    assert result.returncode == 0, result.stderr
    assert "DNS-REPORTS" in result.stdout
    assert "Etapa 1/2" in result.stdout and "Etapa 2/2" in result.stdout
    assert "Instalando componentes" in result.stdout
    assert "[####################]" in result.stdout
    assert "DETAIL_INSTALL_OUTPUT" not in result.stdout + result.stderr
    assert "DETAIL_INSTALL_OUTPUT" in (tmp_path / "install.log").read_text()
    assert "\x1b" not in result.stdout


def test_spinner_and_cursor_restoration(tmp_path):
    result = shell(r"""
set -Eeuo pipefail
source "$TEST_ROOT/scripts/proxmox/lib/ui.sh"
ui_init 1
UI_TTY=1
ui_stage "Python"
ui_run "Instalando Python" bash -c 'sleep 0.4'
""", tmp_path)
    assert result.returncode == 0
    assert "\x1b[?25l" in result.stdout and "\x1b[?25h" in result.stdout
    assert "[OK]" in result.stdout


def test_failed_component_preserves_exit_code_and_log(tmp_path):
    result = shell(r"""
set -Eeuo pipefail
source "$TEST_ROOT/scripts/proxmox/lib/ui.sh"
ui_init 1
ui_stage "Dependencias"
ui_run "Instalando Python" bash -c 'echo diagnostic-detail >&2; exit 7'
echo SHOULD_NOT_RUN
""", tmp_path)
    assert result.returncode == 7
    assert "[FALHOU]" in result.stdout
    assert "SHOULD_NOT_RUN" not in result.stdout
    assert "diagnostic-detail" not in result.stdout + result.stderr
    assert "diagnostic-detail" in (tmp_path / "install.log").read_text()


def update(tmp_path, fail=False):
    log = tmp_path / "proxmox.log"
    code = r"""
set -Eeuo pipefail
pct(){
  printf '%s\n' "$*" >> "$TEST_CALL_LOG"
  case "$1" in
    status) echo 'status: running' ;;
    exec)
      case "$4" in
        mktemp) echo /tmp/dnsreports-upgrade.test.py ;;
        ip) echo '2: eth0 inet 10.77.0.10/24 scope global eth0' ;;
      esac
      if [[ "$TEST_FAIL" == 1 && "$*" == *'pip install'* ]]; then return 23; fi ;;
  esac
  return 0
}
pvesh(){ return 0; }
pveam(){ return 0; }
pvesm(){ return 0; }
flock(){ return 0; }
tar(){ return 0; }
export -f pct pvesh pveam pvesm flock tar
source "$TEST_ROOT/scripts/proxmox/install.sh" "$TEST_ROOT" --update 105
"""
    result = shell(code, tmp_path, TEST_CALL_LOG=log.as_posix(), TEST_FAIL="1" if fail else "0",
                   REPORTS_LOCK_DIR=tmp_path.as_posix())
    return result, log.read_text() if log.exists() else ""


def test_update_preserves_ct_proxy_credentials_and_adds_service(tmp_path):
    result, calls = update(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Etapa 8/8" in result.stdout
    assert "create " not in calls
    assert "create_config.py" not in calls
    assert "/etc/nginx/sites-available/dnsreports" not in calls
    assert "retention_days" not in calls
    assert " backup /opt/dnsreports/backups/update-" in calls
    assert "dnsreports-ntopng.timer" in calls
    assert "runuser -u dnsreports" in calls


def test_update_failure_restores_application_and_old_services(tmp_path):
    result, calls = update(tmp_path, fail=True)
    assert result.returncode != 0
    assert " restore /opt/dnsreports/backups/update-" in calls
    assert "systemctl restart dnsreports.service" in calls
    assert "pronto" not in result.stdout
