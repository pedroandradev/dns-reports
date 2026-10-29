"""Application upgrade backups use SQLite backup and never overwrite live history."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("upgrade", ROOT / "scripts/proxmox/upgrade.py")
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


def test_private_backup_restores_code_config_units_but_keeps_live_database(tmp_path):
    appdir, units = tmp_path / "app", tmp_path / "units"
    (appdir / "data").mkdir(parents=True)
    (appdir / "venv").mkdir()
    (appdir / "source").mkdir()
    units.mkdir()
    (appdir / "venv" / "version").write_text("old")
    (appdir / "source" / "version").write_text("old")
    config = b'{"session_secret":"unchanged","retention_days":60}'
    (appdir / "config.json").write_bytes(config)
    (units / "dnsreports.service").write_text("old unit")
    database = appdir / "data" / "queries.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE records(value)")
        conn.execute("INSERT INTO records VALUES('old DNS history')")
    destination = appdir / "backups" / "update-test"
    upgrade.backup(destination, appdir, units)
    with sqlite3.connect(destination / "queries.sqlite3") as conn:
        assert conn.execute("SELECT value FROM records").fetchone()[0] == "old DNS history"
    (appdir / "config.json").write_text("{}")
    (appdir / "venv" / "version").write_text("new")
    (appdir / "source" / "version").write_text("new")
    (units / "dnsreports.service").write_text("new unit")
    (units / "dnsreports-ntopng.timer").write_text("new timer")
    with sqlite3.connect(database) as conn:
        conn.execute("INSERT INTO records VALUES('new history')")
    upgrade.restore(destination, appdir, units)
    assert (appdir / "config.json").read_bytes() == config
    assert (appdir / "venv" / "version").read_text() == "old"
    assert (units / "dnsreports.service").read_text() == "old unit"
    assert not (units / "dnsreports-ntopng.timer").exists()
    with sqlite3.connect(database) as conn:
        assert conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 2
    assert (appdir / "backups" / "update-test" / "queries.sqlite3").exists()


def test_backup_path_is_confined_and_missing_installation_rejected(tmp_path):
    appdir = tmp_path / "app"
    appdir.mkdir()
    for target in (tmp_path / "other", appdir / "backups" / "not-an-update"):
        with pytest.raises(ValueError):
            upgrade.backup(target, appdir, tmp_path)
    with pytest.raises(ValueError):
        upgrade.backup(appdir / "backups" / "update-test", appdir, tmp_path)
