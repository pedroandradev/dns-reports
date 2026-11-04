"""Opt-in Linux/systemd regression: timers restart after uptime without service history.

Run as root with REPORTS_TEST_SYSTEMD=1. Runtime probe units only touch temporary
marker files; they never invoke the application or access production data.
"""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

ENABLED = (os.environ.get("REPORTS_TEST_SYSTEMD") == "1" and sys.platform == "linux"
           and hasattr(os, "geteuid") and os.geteuid() == 0
           and Path("/run/systemd/system").is_dir())
TIMER_DIR = Path(os.environ.get("REPORTS_TEST_TIMER_DIR",
                 str(Path(__file__).resolve().parents[1] / "scripts/proxmox/systemd")))


@unittest.skipUnless(ENABLED, "opt-in root Linux/systemd test")
class TimerRestartTests(unittest.TestCase):
    def test_first_collection_repeats_and_recovers_after_timer_restart(self):
        for filename in ("dnsreports-ntopng.timer", "dnsreports-collector.timer"):
            with self.subTest(timer=filename):
                self.exercise(filename)

    def exercise(self, filename):
        name = "dnsreports-probe-" + uuid.uuid4().hex
        unit_dir = Path("/run/systemd/system")
        service, timer = name + ".service", name + ".timer"
        service_file, timer_file = unit_dir / service, unit_dir / timer
        scratch = Path(tempfile.mkdtemp(prefix=name + "-", dir="/run"))
        marker = scratch / "ran"
        touch = shutil.which("touch")
        self.assertIsNotNone(touch)

        def ctl(*args, check=True):
            return subprocess.run(["systemctl", *args], check=check, capture_output=True,
                                  text=True, timeout=15)

        def wait_for(predicate):
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.05)
            self.fail("Timer did not run: " + ctl("show", timer, "--no-pager").stdout)

        try:
            service_file.write_text("[Service]\nType=oneshot\nExecStart=" + touch + " " + str(marker) + "\n")
            content = (TIMER_DIR / filename).read_text()
            # Accelerate the real timer's intervals while preserving its trigger types.
            content = re.sub(r"(?m)^(OnActiveSec|OnBootSec|OnUnitInactiveSec)=.*$",
                             r"\1=300ms", content)
            content = re.sub(r"(?m)^AccuracySec=.*$", "AccuracySec=50ms", content)
            content = re.sub(r"(?m)^Unit=.*$", "Unit=" + service, content)
            timer_file.write_text(content)
            ctl("daemon-reload")
            ctl("start", timer)
            wait_for(marker.exists)
            first = marker.stat().st_mtime_ns
            wait_for(lambda: marker.stat().st_mtime_ns > first)

            ctl("stop", timer, service)
            marker.unlink()
            # Drop the stopped units' in-memory history, as happens during an upgrade.
            ctl("daemon-reload")
            ctl("start", timer)
            wait_for(marker.exists)
            restarted = marker.stat().st_mtime_ns
            wait_for(lambda: marker.stat().st_mtime_ns > restarted)
        finally:
            ctl("stop", timer, service, check=False)
            service_file.unlink(missing_ok=True)
            timer_file.unlink(missing_ok=True)
            marker.unlink(missing_ok=True)
            scratch.rmdir()
            ctl("daemon-reload", check=False)
            ctl("reset-failed", timer, service, check=False)


if __name__ == "__main__":
    unittest.main()
