"""The small uppercase label above a section (spec §6.2)."""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QWidget

from rzgit.ui.theme.style_roles import Role, set_role


class SectionHeader(QLabel):
    """Shows its text in upper case; ``text()`` returns what is displayed."""

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        set_role(self, Role.SECTION_HEADER)
        self.set_title(text)

    def set_title(self, text: str) -> None:
        self.setText(text.upper())
