"""The unfinished commit message of each repository (spec §5.4).

The Commit panel keeps its message here as it is typed, so it comes back
after switching repositories or restarting. Panels don't get AppSettings;
this holds only the drafts.

Each repository's draft is a file of its own, and nothing is cached: every
read goes to the file and every write replaces only that file, atomically.
One CommitDrafts is shared by every window, and a second app process can
run beside it: none of them can overwrite another repository's draft, or
put back an older copy of one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.git.repo import canonical_path

log = logging.getLogger(__name__)


def drafts_folder() -> Path:
    """Where the app keeps the drafts: ``commit-drafts`` in its data folder."""
    location = QStandardPaths.StandardLocation.AppDataLocation
    return Path(QStandardPaths.writableLocation(location)) / "commit-drafts"


class CommitDrafts:
    def __init__(self, folder: str | Path | None = None, *,
                 legacy: AppSettings | None = None):
        """``folder``: where the draft files are; None keeps drafts in memory (tests).
        ``legacy``: settings that may still hold drafts saved by older versions;
        they are moved into ``folder`` once."""
        self._folder = Path(folder) if folder is not None else None
        self._memory: dict[str, str] = {}
        if legacy is not None and (saved := legacy.old_commit_drafts):
            self._move_old(saved, legacy)
        elif legacy is not None:
            legacy.drop_old_commit_drafts()  # not valid, or empty

    def _move_old(self, saved: dict[str, str], legacy: AppSettings) -> None:
        """``saved`` re-keyed by canonical path (a key that already was one wins), each
        written unless its repository already has a draft (the file is newer). The
        settings keep them until every one is written."""
        drafts = {key: text for key, text in saved.items() if self._key(key) == key}
        for key, text in saved.items():
            drafts.setdefault(self._key(key), text)
        try:
            for key, text in drafts.items():
                if not self.get(key):
                    self._write(key, text)
        except OSError as exc:
            log.warning("Can't move the saved commit drafts to %s (%s); they stay in "
                        "the settings", self._folder, exc)
            return
        legacy.drop_old_commit_drafts()

    @staticmethod
    def _key(root: str | Path) -> str:
        """The canonical ``root``, so every path to a repository is one key (§6.3)."""
        return str(canonical_path(root))

    def _file(self, key: str) -> Path:
        assert self._folder is not None
        return self._folder / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, root: str | Path) -> str:
        """The draft for the repository at ``root`` ("" if none)."""
        key = self._key(root)
        if self._folder is None:
            return self._memory.get(key, "")
        path = self._file(key)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ""
        except (OSError, ValueError) as exc:
            log.warning("Can't read the commit draft %s (%s); ignoring it", path, exc)
            return ""
        if (not isinstance(data, dict) or data.get("root") != key
                or not isinstance(data.get("text"), str)):
            log.warning("The commit draft %s isn't for %s; ignoring it", path, key)
            return ""
        return data["text"]

    def set(self, root: str | Path, text: str) -> None:
        """Keep ``text`` as the draft for ``root``; a blank text removes it."""
        key = self._key(root)
        if text.strip() and self.get(key) == text:
            return
        try:
            self._write(key, text)
        except OSError as exc:
            log.warning("Can't save the commit draft for %s (%s)", key, exc)

    def _write(self, key: str, text: str) -> None:
        """Replace the file of ``key`` with ``text``, or delete it for a blank text."""
        if self._folder is None:
            if text.strip():
                self._memory[key] = text
            else:
                self._memory.pop(key, None)
            return
        path = self._file(key)
        if not text.strip():
            path.unlink(missing_ok=True)
            return
        self._folder.mkdir(parents=True, exist_ok=True)
        self._replace(path, json.dumps({"root": key, "text": text}))

    @staticmethod
    def _replace(path: Path, content: str) -> None:
        """Write ``path`` whole or not at all: a reader never sees half a file."""
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".draft-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
