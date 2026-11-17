"""File → Recent Repositories (spec §8): the repositories opened recently, newest first."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QMenu, QWidget

from rzgitclient.core.recent_repos import RecentRepos

Opener = Callable[[Path], object]


class RecentReposMenu(QMenu):
    """Rebuilt from ``recent`` each time it opens, so every window shows the same list.
    Each entry has a submenu: Open in Current Window, Open in New Window, Remove."""

    def __init__(self, recent: RecentRepos, open_here: Opener, open_in_window: Opener,
                 parent: QWidget | None = None):
        super().__init__("Re&cent Repositories", parent)
        self._recent = recent
        self._open_here = open_here
        self._open_in_window = open_in_window
        self._entries: list[QMenu] = []  # the submenus, which clear() doesn't delete
        self.setToolTipsVisible(True)
        self.aboutToShow.connect(self.rebuild)
        self.rebuild()

    def rebuild(self) -> None:
        self.clear()
        for entry in self._entries:
            entry.deleteLater()
        self._entries = []
        paths = self._recent.repositories()
        if not paths:
            self.addAction("No Recent Repositories").setEnabled(False)
            return
        for path, label in zip(paths, entry_labels(paths)):
            self._add_entry(path, label)
        self.addSeparator()
        self.addAction("&Clear Recent Repositories", self._recent.clear)

    def _add_entry(self, path: Path, label: str) -> None:
        exists = path.is_dir()
        text = label.replace("&", "&&") + ("" if exists else " (missing)")
        entry = self.addMenu(text)
        self._entries.append(entry)
        entry.menuAction().setToolTip(str(path))
        here = entry.addAction("Open in &Current Window", lambda: self._open_here(path))
        new = entry.addAction("Open in &New Window", lambda: self._open_in_window(path))
        here.setEnabled(exists)
        new.setEnabled(exists)
        entry.addSeparator()
        entry.addAction("&Remove from List", lambda: self._recent.remove(path))


def entry_labels(paths: tuple[Path, ...] | list[Path]) -> list[str]:
    """Each folder's name; names shared by several entries add their parent folder,
    with the home folder as ``~``: ``RZE — ~/dev``."""
    counts = Counter(p.name for p in paths)
    return [f"{p.name} — {home_relative(p.parent)}" if counts[p.name] > 1 else p.name
            for p in paths]


def home_relative(path: Path) -> str:
    try:
        rest = path.relative_to(Path.home().resolve())  # paths are canonical
    except ValueError:
        return str(path)
    return "~" if rest == Path(".") else f"~/{rest}"
