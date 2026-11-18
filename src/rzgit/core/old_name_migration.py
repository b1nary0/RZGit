"""What the app left behind under its old name, RZGitClient, copied once (spec §6.3).

The app was renamed RZGit (RZG-59), and its organisation and application names
decide where QSettings and its data folder are. So on the first start after the
rename, the old settings file and the commit drafts are copied to the new places.
Each is copied only when the new one doesn't exist yet, and the old files are never
changed or deleted. This works on paths only: QSettings stays AppSettings' alone.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths

log = logging.getLogger(__name__)

OLD_NAME = "RZGitClient"
DRAFTS = "commit-drafts"  # the folder CommitDrafts uses in the data folder


def settings_file(config_home: Path, organisation: str, application: str) -> Path:
    """Where QSettings keeps an app's settings on Linux: ``<config>/<org>/<app>.conf``."""
    return config_home / organisation / f"{application}.conf"


def data_folder(data_home: Path, organisation: str, application: str) -> Path:
    """An app's ``AppDataLocation`` on Linux: ``<data>/<org>/<app>``."""
    return data_home / organisation / application


def migrate_old_name(config_home: str | Path | None = None,
                     data_home: str | Path | None = None) -> list[Path]:
    """Copies the old name's settings file and drafts folder to the app's current
    names (set on the QCoreApplication first), each only if the new one is missing.
    ``config_home`` and ``data_home`` default to the user's (tests pass folders).
    Returns what was copied, as the new paths."""
    config = Path(config_home) if config_home is not None else _location(
        QStandardPaths.StandardLocation.GenericConfigLocation)
    data = Path(data_home) if data_home is not None else _location(
        QStandardPaths.StandardLocation.GenericDataLocation)
    org = QCoreApplication.organizationName()
    app = QCoreApplication.applicationName()
    pairs = [
        (settings_file(config, OLD_NAME, OLD_NAME), settings_file(config, org, app)),
        (data_folder(data, OLD_NAME, OLD_NAME) / DRAFTS, data_folder(data, org, app) / DRAFTS),
    ]
    return [new for old, new in pairs if _copy_once(old, new)]


def _location(kind: QStandardPaths.StandardLocation) -> Path:
    return Path(QStandardPaths.writableLocation(kind))


def _copy_once(old: Path, new: Path) -> bool:
    """Copies the file or folder ``old`` to ``new`` if ``old`` is there and ``new``
    isn't. The copy is made in a fresh temporary folder beside ``new`` and renamed into
    place, so ``new`` is whole or missing. A failed copy is logged."""
    if not old.exists() or new.exists() or new.is_symlink():
        return False
    try:
        new.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=new.parent, prefix=".old-name-") as scratch:
            copy = Path(scratch) / new.name
            if old.is_dir():
                shutil.copytree(old, copy, symlinks=True)
            else:
                shutil.copy2(old, copy)
            if new.exists() or new.is_symlink():
                return False  # another process copied it meanwhile
            copy.rename(new)
    except OSError as exc:
        log.warning("Can't copy %s to %s (%s); the app starts without it", old, new, exc)
        return False
    log.info("Copied %s to %s (the app's old name)", old, new)
    return True
