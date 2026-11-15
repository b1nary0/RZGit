"""The branch list (spec §5.4)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QVBoxLayout

from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Surface


class BranchesPanel(Panel):
    panel_id = "branches"
    title = "Branches"
    surface = Surface.SIDEBAR

    _list: QListWidget

    def _setup(self) -> None:
        self._list = QListWidget()
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._list)

        session = self.ctx.session
        session.branches_changed.connect(self._render)
        session.head_changed.connect(self._render)
        self._render()

    def _render(self, _value: object = None) -> None:
        # Depends on both the branches and HEAD, so it reads the session.
        branches, head = self.ctx.session.branches, self.ctx.session.head
        current = self._list.currentItem()
        keep = current.text() if current else None

        blocked = self._list.blockSignals(True)
        try:
            self._list.clear()
            for b in branches:
                item = QListWidgetItem(b.name)
                if b.is_current:
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                self._list.addItem(item)
                if b.name == keep:
                    self._list.setCurrentItem(item)
            if not branches and head:
                # Fresh repo: the branch exists in HEAD but has no ref yet.
                self._list.addItem(QListWidgetItem(f"{head} (no commits)"))
        finally:
            self._list.blockSignals(blocked)
