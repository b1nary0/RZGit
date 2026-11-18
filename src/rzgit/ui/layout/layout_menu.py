"""View → Layout: the built-in presets and the user's saved layouts (spec §6.1, §6.3, §6.4)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QInputDialog, QLineEdit, QMenu, QMessageBox, QWidget

from rzgit.core.app_settings import AppSettings
from rzgit.ui.layout.layout_host import LayoutHost
from rzgit.ui.layout.layout_model import to_dict
from rzgit.ui.layout.layout_presets import (
    PRESETS,
    Preset,
    layout_name_problem,
    restore_layout,
)
from rzgit.ui.layout.manage_layouts_dialog import ManageLayoutsDialog


class LayoutMenu(QMenu):
    def __init__(self, host: LayoutHost, settings: AppSettings, parent: QWidget | None = None):
        super().__init__("&Layout", parent)
        self._host = host
        self._settings = settings
        self.rebuild()

    def rebuild(self) -> None:
        """List the presets and the saved layouts as they are now."""
        self.clear()
        for preset in PRESETS:
            action = self.addAction(preset.title)
            action.triggered.connect(lambda _checked=False, p=preset: self.apply(p))
        saved = self._settings.saved_layouts
        if saved:
            self.addSeparator()
        for name, data in saved:
            action = self.addAction(name)
            action.triggered.connect(lambda _checked=False, d=data: self.open_saved(d))
        self.addSeparator()
        self.addAction("&Save Layout As…").triggered.connect(self.save_as)
        manage = self.addAction("&Manage Layouts…")
        manage.setEnabled(bool(saved))
        manage.triggered.connect(self.manage)

    def apply(self, preset: Preset) -> None:
        """Show ``preset``. Presets show every panel, so nothing stays hidden."""
        self._host.apply(preset.state)

    def open_saved(self, data: dict[str, Any]) -> None:
        """Show a saved layout, normalised like the layout at startup (its hidden
        panels stay hidden)."""
        self._host.apply(restore_layout(data))

    def save_as(self) -> None:
        name = self.ask_name()
        if name is not None:
            self.save(name)

    def save(self, name: str) -> bool:
        """Save the current layout as ``name``; ``False`` if refused or not confirmed."""
        name = name.strip()
        state = self._host.state()
        if problem := layout_name_problem(name):
            self.show_problem(problem)
            return False
        saved = self._settings.saved_layouts
        entry = (name, to_dict(state))
        same = [i for i, (n, _) in enumerate(saved) if n.casefold() == name.casefold()]
        if same:
            if not self.confirm_replace(saved[same[0]][0]):
                return False
            saved[same[0]] = entry
        else:
            saved.append(entry)
        self._settings.saved_layouts = saved
        self.rebuild()
        return True

    def manage(self) -> None:
        ManageLayoutsDialog(self._settings, self._host.window()).exec()
        self.rebuild()

    # ---- prompts (replaced in tests) ----------------------------------

    def ask_name(self) -> str | None:
        name, ok = QInputDialog.getText(self._host.window(), "Save Layout",
                                        "Name of the layout:", QLineEdit.EchoMode.Normal)
        return name if ok else None

    def confirm_replace(self, name: str) -> bool:
        answer = QMessageBox.question(
            self._host.window(), "Replace Layout",
            f"A saved layout is already called “{name}”. Replace it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    def show_problem(self, text: str) -> None:
        QMessageBox.warning(self._host.window(), "Save Layout", text)
