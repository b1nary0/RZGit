"""The repositories and worktrees opened recently (spec §6.3).

One record for the app: File → Recent Repositories lists its main repositories, and
the Worktrees panel's recently-used sort reads each worktree's time.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.git.repo import Repo, canonical_path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Entry:
    root: Path  # the worktree's root (canonical)
    main: Path  # its main repository's root (canonical)
    opened: float  # epoch seconds


class RecentRepos(QObject):
    changed = Signal()
    LIMIT = 10  # main repositories listed and kept

    def __init__(self, settings: AppSettings | None = None, parent: QObject | None = None,
                 *, clock: Callable[[], float] = time.time):
        """``settings``: where the list is saved; in memory if not given (tests)."""
        super().__init__(parent)
        self._settings = settings
        self._clock = clock
        self._entries: list[_Entry] = self._load() if settings is not None else []

    # ---- reading ---------------------------------------------------------------

    def repositories(self) -> tuple[Path, ...]:
        """The main repositories, newest first, at most ``LIMIT``."""
        return tuple(dict.fromkeys(e.main for e in self._entries))[:self.LIMIT]

    def last_opened(self, root: str | Path) -> float | None:
        """When the worktree at ``root`` was last opened (epoch seconds), if it was."""
        root = canonical_path(root)
        return next((e.opened for e in self._entries if e.root == root), None)

    # ---- changing --------------------------------------------------------------

    def touch(self, repo: Repo) -> None:
        """``repo`` was opened now: it, and its main repository, go to the top."""
        entry = _Entry(canonical_path(repo.root), canonical_path(repo.main_root),
                       self._clock())
        self._entries = [entry] + [e for e in self._entries if e.root != entry.root]
        self._commit()

    def remove(self, main_root: str | Path) -> None:
        """Take a repository, with its worktrees, off the list."""
        main = canonical_path(main_root)
        kept = [e for e in self._entries if e.main != main]
        if len(kept) != len(self._entries):
            self._entries = kept
            self._commit()

    def clear(self) -> None:
        if self._entries:
            self._entries = []
            self._commit()

    # ---- internals -------------------------------------------------------------

    def _commit(self) -> None:
        mains = set(self.repositories())
        self._entries = [e for e in self._entries if e.main in mains]
        if self._settings is not None:
            self._settings.recent_repos = [
                {"root": str(e.root), "main": str(e.main), "opened": e.opened}
                for e in self._entries]
        self.changed.emit()

    def _load(self) -> list[_Entry]:
        entries: list[_Entry] = []
        seen: set[Path] = set()
        for raw in self._settings.recent_repos:
            entry = _parse(raw)
            if entry is None:
                log.warning("Dropping a malformed recent repository: %.80r", raw)
            elif entry.root not in seen:
                seen.add(entry.root)
                entries.append(entry)
        mains = set(tuple(dict.fromkeys(e.main for e in entries))[:self.LIMIT])
        return [e for e in entries if e.main in mains]


def _parse(raw: Any) -> _Entry | None:
    if not isinstance(raw, dict):
        return None
    root, main, opened = raw.get("root"), raw.get("main"), raw.get("opened")
    if not (isinstance(root, str) and root and isinstance(main, str) and main
            and isinstance(opened, (int, float)) and not isinstance(opened, bool)):
        return None
    return _Entry(Path(root), Path(main), float(opened))
