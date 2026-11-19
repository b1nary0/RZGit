"""Saving files: one atomic write, and the last folder of each kind of save (spec §8.3).

No widgets: ``ui/save_dialog.py`` asks where to save and says if it failed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from rzgit.core.app_settings import AppSettings


class FileSaver(QObject):
    saved = Signal(object, object)  # Path, origin (what the caller passed: the widget that asked)

    def __init__(self, settings: AppSettings | None = None, parent: QObject | None = None):
        """``settings``: where the folders are kept; in memory if not given (tests)."""
        super().__init__(parent)
        self._settings = settings
        self._folders: dict[str, str] = settings.save_folders if settings is not None else {}

    # ---- folders -----------------------------------------------------------------

    def folder(self, kind: str) -> Path | None:
        """The folder the last save of ``kind`` went to, if it still exists."""
        folder = self._folders.get(kind)
        return Path(folder) if folder and Path(folder).is_dir() else None

    def remember(self, kind: str, folder: str | Path) -> None:
        self._folders[kind] = str(folder)
        if self._settings is not None:
            self._settings.save_folders = dict(self._folders)

    # ---- saving ------------------------------------------------------------------

    def save(self, path: str | Path, data: str | bytes, *, kind: str | None = None,
             origin: object = None) -> Path:
        """Write ``data`` to ``path`` atomically and return the path written (a symlink
        followed). ``str`` is written as UTF-8, exactly as given. Raises ``OSError``
        and leaves the file as it was if the write fails. ``kind``: remember its folder."""
        target = Path(path).resolve()
        raw = data.encode("utf-8") if isinstance(data, str) else data
        mode = _mode_for(target)
        fd, temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            os.chmod(temp, mode)
            os.replace(temp, target)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
        if kind is not None:
            self.remember(kind, target.parent)
        self.saved.emit(target, origin)
        return target


def _mode_for(target: Path) -> int:
    """An existing file's permissions; else the usual ones for a new file."""
    try:
        return target.stat().st_mode & 0o7777
    except FileNotFoundError:
        return 0o666 & ~_umask()


def _umask() -> int:
    """The process's umask, read without setting it: ``os.umask`` would change it for a
    moment for every thread, and a git command started then would inherit it."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("Umask:"):
                return int(line.split()[1], 8)
    except (OSError, ValueError):
        pass
    return 0o022
