"""The unfinished commit message of each repository (spec §5.4).

The Commit panel keeps its message here as it is typed, so it comes back
after switching repositories or restarting. Panels don't get AppSettings;
this holds only the drafts.
"""

from __future__ import annotations

from pathlib import Path

from rzgitclient.core.app_settings import AppSettings


class CommitDrafts:
    def __init__(self, settings: AppSettings | None = None):
        """``settings``: where drafts are saved; None keeps them in memory (tests)."""
        self._settings = settings
        self._drafts = settings.commit_drafts if settings is not None else {}

    def get(self, root: str | Path) -> str:
        """The draft for the repository at ``root`` ("" if none)."""
        return self._drafts.get(str(root), "")

    def set(self, root: str | Path, text: str) -> None:
        """Keep ``text`` as the draft for ``root``; a blank text removes it."""
        key = str(root)
        if text.strip():
            if self._drafts.get(key) == text:
                return
            self._drafts[key] = text
        elif self._drafts.pop(key, None) is None:
            return
        if self._settings is not None:
            self._settings.commit_drafts = self._drafts
