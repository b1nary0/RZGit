"""View → Layout → Manage Layouts…: rename and delete saved layouts (spec §6.3)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from rzgit.core.app_settings import AppSettings
from rzgit.ui.layout.layout_presets import layout_name_problem


class ManageLayoutsDialog(QDialog):
    """Each change is written to ``settings`` at once."""

    def __init__(self, settings: AppSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle("Manage Layouts")

        self.list = QListWidget()
        self.rename_button = QPushButton("&Rename…")
        self.delete_button = QPushButton("&Delete")
        self.rename_button.clicked.connect(self.rename_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.list.currentRowChanged.connect(self._update_buttons)
        self.list.itemDoubleClicked.connect(self.rename_selected)

        buttons = QVBoxLayout()
        buttons.addWidget(self.rename_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch()
        body = QHBoxLayout()
        body.addWidget(self.list)
        body.addLayout(buttons)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(body)
        layout.addWidget(close)

        self._fill(0)

    def names(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def rename_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0 and (name := self.ask_name(self.names()[row])) is not None:
            self.rename(row, name)

    def rename(self, row: int, name: str) -> bool:
        """Rename the saved layout at ``row``; ``False`` if the name is refused."""
        name = name.strip()
        saved = self._settings.saved_layouts
        if name == saved[row][0]:
            return True
        others = [n for i, (n, _) in enumerate(saved) if i != row]
        if problem := layout_name_problem(name, others):
            self.show_problem(problem)
            return False
        saved[row] = (name, saved[row][1])
        self._settings.saved_layouts = saved
        self._fill(row)
        return True

    def delete_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0 and self.confirm_delete(self.names()[row]):
            self.delete(row)

    def delete(self, row: int) -> None:
        saved = self._settings.saved_layouts
        del saved[row]
        self._settings.saved_layouts = saved
        self._fill(min(row, len(saved) - 1))

    def _fill(self, current: int) -> None:
        self.list.clear()
        self.list.addItems([name for name, _ in self._settings.saved_layouts])
        self.list.setCurrentRow(current)
        self._update_buttons()

    def _update_buttons(self) -> None:
        selected = self.list.currentRow() >= 0
        self.rename_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    # ---- prompts (replaced in tests) ----------------------------------

    def ask_name(self, current: str) -> str | None:
        name, ok = QInputDialog.getText(self, "Rename Layout", "New name of the layout:",
                                        QLineEdit.EchoMode.Normal, current)
        return name if ok else None

    def confirm_delete(self, name: str) -> bool:
        answer = QMessageBox.question(
            self, "Delete Layout", f"Delete the saved layout “{name}”?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel)
        return answer == QMessageBox.StandardButton.Yes

    def show_problem(self, text: str) -> None:
        QMessageBox.warning(self, "Rename Layout", text)
