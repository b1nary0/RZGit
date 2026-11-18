"""A tree whose top-level rows can be group headings (spec §5.1, RZG-53).

The file panels, Branches and Stashes put their rows under a heading per
group (``SortOptions.grouped``). A heading spans the columns, shows the
group's name and count in bold, and is coloured from the theme's
``group_header_*`` tokens: its background is painted under the row in
``drawRow``, since the app's stylesheet ignores an item's own background. It folds by its arrow or a double-click, and a
folded one stays folded by its group while the app runs (``fold_headings``
puts that back after each render). With ``selectable_headings`` off
(Branches, Stashes) a heading can't be selected: a click on one leaves the
selection as it is, and the arrow keys skip it.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QItemSelectionModel, QModelIndex, QPersistentModelIndex, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QWidget,
)

from rzgit.core.sort_options import Group
from rzgit.ui.theme.app_theme import AppTheme

HEADING = Qt.ItemDataRole.UserRole + 16  # a heading's (grouping key, group name)
INDENT = 10  # px: rows under a heading, as the file panels' folders

_Cursor = QAbstractItemView.CursorAction
_UPWARD = (_Cursor.MoveUp, _Cursor.MovePrevious, _Cursor.MovePageUp, _Cursor.MoveEnd)


def heading_key(item: QTreeWidgetItem | None) -> tuple[str, str] | None:
    """The (grouping key, group name) of a heading row; None for any other row."""
    return item.data(0, HEADING) if item is not None else None


class GroupedTree(QTreeWidget):
    def __init__(self, theme: AppTheme, selectable_headings: bool = False,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.selectable_headings = selectable_headings
        self._folded: set[tuple[str, str]] = set()  # headings the user folded
        self._colors(theme)
        self.setIndentation(INDENT)
        self.itemCollapsed.connect(lambda item: self._set_folded(item, True))
        self.itemExpanded.connect(lambda item: self._set_folded(item, False))

    def _colors(self, theme: AppTheme) -> None:
        self._bg = QColor(theme.colors.group_header_bg)
        self._text = QColor(theme.colors.group_header_text)

    def set_theme(self, theme: AppTheme) -> None:
        """Colour the headings from ``theme``, those shown and those made later."""
        self._colors(theme)
        for item in self.headings():
            item.setForeground(0, self._text)
        self.viewport().update()

    def drawRow(self, painter: QPainter, option: QStyleOptionViewItem,
                index: QModelIndex | QPersistentModelIndex) -> None:
        """A heading's background under the whole row, its arrow too; hover and
        selection are drawn over it."""
        if index.data(HEADING) is not None:
            painter.fillRect(option.rect, self._bg)
        super().drawRow(painter, option, index)

    # ---- headings ------------------------------------------------------------

    def heading(self, group: Group, count: int | None = None) -> QTreeWidgetItem:
        """A heading row for ``group``, showing ``count`` rows (its items' by
        default). Its rows are added as its children; it is styled before it
        joins the tree, which is the fast way (see FileList.render)."""
        count = len(group.items) if count is None else count
        item = QTreeWidgetItem([f"{group.name} ({count:,})"])
        item.setData(0, HEADING, (group.key, group.name))
        item.setToolTip(0, group.name)
        font = item.font(0)
        font.setBold(True)
        item.setFont(0, font)
        item.setForeground(0, self._text)
        if not self.selectable_headings:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    def fold_headings(self) -> None:
        """After the rows are added: headings span the columns, and those the user
        folded are folded again; the others are open."""
        for item in self.headings():
            item.setFirstColumnSpanned(True)
            item.setExpanded(heading_key(item) not in self._folded)

    def forget_folded(self) -> None:
        """Another repository: every heading starts open."""
        self._folded.clear()

    def _set_folded(self, item: QTreeWidgetItem, folded: bool) -> None:
        key = heading_key(item)
        if key is not None:
            (self._folded.add if folded else self._folded.discard)(key)

    def headings(self) -> list[QTreeWidgetItem]:
        return [item for i in range(self.topLevelItemCount())
                if heading_key(item := self.topLevelItem(i)) is not None]

    def rows(self) -> list[QTreeWidgetItem]:
        """Every row that isn't a heading, in tree order. A list, not a generator:
        an iterator left alive while the tree is cleared crashes Qt."""
        found, it = [], QTreeWidgetItemIterator(self)
        while (item := it.value()) is not None:
            if heading_key(item) is None:
                found.append(item)
            it += 1
        return found

    # ---- headings that can't be selected ---------------------------------------

    def _fixed(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        return (not self.selectable_headings and index.isValid()
                and index.data(HEADING) is not None)

    def selectionCommand(self, index: QModelIndex | QPersistentModelIndex,
                         event: QEvent | None = None) -> QItemSelectionModel.SelectionFlag:
        if self._fixed(index):  # a click on a heading keeps the selection
            return QItemSelectionModel.SelectionFlag.NoUpdate
        return super().selectionCommand(index, event)

    def moveCursor(self, action: QAbstractItemView.CursorAction,
                   modifiers: Qt.KeyboardModifier) -> QModelIndex:
        """The arrow keys (and Home, End, Page Up and Down) skip headings that
        can't be selected: on past them, or back if there is nothing further."""
        index = super().moveCursor(action, modifiers)
        if not self._fixed(index):
            return index
        down = action not in _UPWARD
        for forward in (down, not down):
            found = index
            while self._fixed(found):
                found = self.indexBelow(found) if forward else self.indexAbove(found)
            if found.isValid():
                return found
        return self.currentIndex()
