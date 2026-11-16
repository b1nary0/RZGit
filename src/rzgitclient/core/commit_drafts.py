"""The unfinished commit message of each repository (spec §5.4).

The Commit panel keeps its message here as it is typed, so it comes back
after switching repositories or restarting. Panels don't get AppSettings;
this holds only the drafts.
"""

from __future__ import annotations

from pathlib import Path

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.git.repo import canonical_path


class CommitDrafts:
    def __init__(self, settings: AppSettings | None = None):
        """``settings``: where drafts are saved; None keeps them in memory (tests)."""
        self._settings = settings
        saved = settings.commit_drafts if settings is not None else {}
        self._drafts = self._migrated(saved)
        if settings is not None and self._drafts != saved:
            settings.commit_drafts = self._drafts

    @classmethod
    def _migrated(cls, saved: dict[str, str]) -> dict[str, str]:
        """``saved`` re-keyed by canonical path; a key that already was one wins."""
        drafts = {key: text for key, text in saved.items() if cls._key(key) == key}
        for key, text in saved.items():
            drafts.setdefault(cls._key(key), text)
        return drafts

    @staticmethod
    def _key(root: str | Path) -> str:
        """The canonical ``root``, so every path to a repository is one key (§6.3)."""
        return str(canonical_path(root))

    def get(self, root: str | Path) -> str:
        """The draft for the repository at ``root`` ("" if none)."""
        return self._drafts.get(self._key(root), "")

    def set(self, root: str | Path, text: str) -> None:
        """Keep ``text`` as the draft for ``root``; a blank text removes it."""
        key = self._key(root)
        if text.strip():
            if self._drafts.get(key) == text:
                return
            self._drafts[key] = text
        elif self._drafts.pop(key, None) is None:
            return
        if self._settings is not None:
            self._settings.commit_drafts = self._drafts
