"""Asking where to save, and saying if it failed (spec §8.3).

Every save in the app goes through ``SaveDialog.ask_and_save``; the writing and the
folders it remembers are ``FileSaver``'s (``core/file_saver.py``).
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QWidget

from rzgit.core.file_saver import FileSaver


class SaveDialog:
    def __init__(self, saver: FileSaver | None = None):
        """``saver``: the app's; one in memory if not given (tests)."""
        self.saver = saver if saver is not None else FileSaver()
        self.saved = self.saver.saved  # (Path, origin) after each save

    def ask_and_save(self, parent: QWidget | None, data: str | bytes, *, kind: str,
                     suggested_name: str, title: str, filters: str = "",
                     start: str | Path | None = None) -> Path | None:
        """Ask for a path (in the last folder of ``kind``, else ``start``, else home),
        then save ``data`` there. The path saved, or None if cancelled or it failed
        (the error is shown)."""
        folder = self.saver.folder(kind) or (Path(start) if start else Path.home())
        path = self.ask_path(parent, title, folder / suggested_name, filters)
        if not path:
            return None
        try:
            return self.saver.save(path, data, kind=kind, origin=parent)
        except OSError as exc:
            QMessageBox.warning(parent, "Couldn't save",
                                f"Couldn't save {path}:\n{exc.strerror or exc}")
            return None

    def ask_path(self, parent: QWidget | None, title: str, suggested: Path,
                 filters: str) -> str:
        """The path chosen, or "" if cancelled. The dialog asks before overwriting."""
        path, _filter = QFileDialog.getSaveFileName(parent, title, str(suggested), filters)
        return path
