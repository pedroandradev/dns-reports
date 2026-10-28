"""Private configuration and paths."""
import json
import os
from pathlib import Path
BASE = Path(os.environ.get("DNS_REPORTS_HOME", "/opt/dnsreports"))
CONFIG = Path(os.environ.get("DNS_REPORTS_CONFIG", str(BASE / "config.json")))
def config():
    return json.loads(CONFIG.read_text(encoding='utf-8'))



