"""Exercise the access-only shell entrypoint with mocked Proxmox commands."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = os.environ.get("REPORTS_TEST_BASH") or shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="bash unavailable")


def invoke(tmp_path, value, args=("--configure-access", "105"), running=True):
    log = tmp_path / "calls.log"
    # Functions allow the same test on Linux and Git Bash, without executing pct.
    prelude = r"""
pct() {
  printf '%s\n' "$*" >> "$TEST_LOG"
  case "$1" in
    config) return 0 ;;
    status) printf 'status: %s\n' "$TEST_STATUS" ;;
    push) return 0 ;;
    exec)
      if [[ "$4" == mktemp ]]; then printf '/tmp/dnsreports-test.py\n'; fi
      return 0 ;;
    *) return 1 ;;
  esac
}
python3() { "$TEST_PYTHON" "$@"; }
export -f pct python3
source "$TEST_INSTALL" "$TEST_ROOT" "$@"
"""
    env = dict(os.environ, TEST_LOG=str(log), TEST_STATUS="running" if running else "stopped",
               TEST_PYTHON=Path(sys.executable).as_posix(),
               TEST_INSTALL=(ROOT / "scripts/proxmox/install.sh").as_posix(),
               TEST_ROOT=ROOT.as_posix())
    env.pop("REPORTS_VPN_CIDRS", None)
    if value is not None:
        env["REPORTS_VPN_CIDRS"] = value
    result = subprocess.run([BASH, "-c", prelude, "test-access", *args],
                            env=env, capture_output=True, text=True, timeout=15)
    return result, log.read_text() if log.exists() else ""


@pytest.mark.parametrize("value", ["100.96.0.0/24", ""])
def test_access_only_never_installs_or_updates_application(tmp_path, value):
    result, log = invoke(tmp_path, value)
    assert result.returncode == 0, result.stderr
    assert "push 105 " in log
    assert "flock /run/lock/dnsreports-access.lock python3 " in log
    assert "--vpn-cidrs=" + value in log
    assert "rm -f /tmp/dnsreports-test.py" in log
    for forbidden in ("pvesh", "apt-get", "pip", "config.json", "sqlite", "systemctl restart"):
        assert forbidden not in log


@pytest.mark.parametrize("value, args", [
    (None, ("--configure-access", "105")),
    ("all", ("--configure-access", "105")),
    ("0.0.0.0/0", ("--configure-access", "105")),
    ("100.96.0.0/24", ("--configure-access", "invalid")),
    ("100.96.0.0/24", ("--configure-access", "105", "extra")),
])
def test_bad_input_fails_before_touching_ct(tmp_path, value, args):
    result, log = invoke(tmp_path, value, args=args)
    assert result.returncode != 0
    assert not log


def test_stopped_ct_is_not_started_or_changed(tmp_path):
    result, log = invoke(tmp_path, "100.96.0.0/24", running=False)
    assert result.returncode != 0
    assert "push " not in log
    assert "exec " not in log
    assert "start " not in log
