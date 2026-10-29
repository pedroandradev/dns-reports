#!/usr/bin/env python3
"""Private, versioned backups and rollback for an in-place application upgrade."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

APP_ITEMS = ("config.json", "venv", "source", "app.py", "core.py")
UNITS = ("dnsreports.service", "dnsreports-collector.service", "dnsreports-collector.timer",
         "dnsreports-ntopng.service", "dnsreports-ntopng.timer")


def checked_directory(directory, appdir):
    appdir = appdir.resolve()
    directory = directory.resolve()
    if directory.parent != appdir / "backups" or not directory.name.startswith("update-"):
        raise ValueError("Backup deve estar em /opt/dnsreports/backups/update-IDENTIFICADOR.")
    return directory


def preserve_owner(source, target):
    if not hasattr(os, "chown"):
        return
    owner, copied = source.lstat(), target.lstat()
    if (owner.st_uid, owner.st_gid) != (copied.st_uid, copied.st_gid):
        os.chown(target, owner.st_uid, owner.st_gid, follow_symlinks=False)


def copy_item(source, target):
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
    elif source.is_symlink():
        target.symlink_to(os.readlink(source), target_is_directory=source.is_dir())
    else:
        shutil.copy2(source, target)
    preserve_owner(source, target)
    if source.is_dir() and not source.is_symlink():
        for original in source.rglob("*"):
            preserve_owner(original, target / original.relative_to(source))


def backup(directory, appdir=Path("/opt/dnsreports"), unitsdir=Path("/etc/systemd/system")):
    directory = checked_directory(directory, appdir)
    if not (appdir / "config.json").is_file():
        raise ValueError("Instalação existente não encontrada.")
    needed = 256 * 1024 * 1024
    for name in (*APP_ITEMS, "data"):
        source = appdir / name
        if source.is_dir() and not source.is_symlink():
            needed += sum(item.stat().st_size for item in source.rglob("*")
                          if item.is_file() and not item.is_symlink())
        elif source.is_file() and not source.is_symlink():
            needed += source.stat().st_size
    if shutil.disk_usage(appdir).free < needed:
        raise OSError("Espaço insuficiente para backup e atualização; libere espaço no CT.")
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    directory.chmod(0o700)
    (directory / "app").mkdir(mode=0o700)
    (directory / "units").mkdir(mode=0o700)
    present = {"app": [], "units": [], "database": False}
    for name in APP_ITEMS:
        source = appdir / name
        if source.exists() or source.is_symlink():
            copy_item(source, directory / "app" / name)
            present["app"].append(name)
    for name in UNITS:
        source = unitsdir / name
        if source.exists():
            copy_item(source, directory / "units" / name)
            present["units"].append(name)
    database = appdir / "data" / "queries.sqlite3"
    if database.exists():
        with sqlite3.connect(database) as source, sqlite3.connect(directory / "queries.sqlite3") as target:
            source.backup(target)
        (directory / "queries.sqlite3").chmod(0o600)
        present["database"] = True
    shutil.copy2(Path(__file__), directory / "upgrade.py")
    (directory / "manifest.json").write_text(json.dumps(present), encoding="utf-8")
    (directory / "manifest.json").chmod(0o600)


def restore(directory, appdir=Path("/opt/dnsreports"), unitsdir=Path("/etc/systemd/system")):
    directory = checked_directory(directory, appdir)
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    # Keep the live DB; additive tables also work with the old application.
    failed = Path(tempfile.mkdtemp(prefix="failed-", dir=directory))
    for name in APP_ITEMS:
        target = appdir / name
        if name in manifest["app"]:
            if target.exists() or target.is_symlink():
                shutil.move(str(target), failed / name)
            copy_item(directory / "app" / name, target)
    for name in UNITS:
        target = unitsdir / name
        if name in manifest["units"]:
            copy_item(directory / "units" / name, target)
        elif target.exists():
            target.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("backup", "restore"))
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    (backup if args.action == "backup" else restore)(args.directory)
    print("Snapshot de atualização: " + str(args.directory))


if __name__ == "__main__":
    main()
