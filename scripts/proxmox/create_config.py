#!/usr/bin/env python3
"""Create private credentials only for a new installation."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import sys

salt = secrets.token_bytes(16)
rounds = 380000
digest = hashlib.pbkdf2_hmac("sha256", os.environ["PANEL_PASS"].encode(), salt, rounds)
settings = {
    "adguard_url": os.environ["ADGUARD_URL"],
    "adguard_username": os.environ["ADGUARD_USER"],
    "adguard_password": os.environ["ADGUARD_PASS"],
    "panel_username": os.environ["PANEL_USER"],
    "panel_password_hash": "$".join(("pbkdf2_sha256", str(rounds), salt.hex(), digest.hex())),
    "session_secret": secrets.token_hex(48),
    "retention_days": 90, "initial_history_days": 7, "timezone": os.environ["TZ_NAME"]
}
path = Path(sys.argv[1])
with path.open("x", encoding="utf-8") as stream:
    json.dump(settings, stream, ensure_ascii=False)
path.chmod(0o600)
