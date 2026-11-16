"""Where a dragged panel will land (spec §6.2)."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from rzgitclient.ui.layout.layout_model import DropZone
from rzgitclient.ui.theme.theme_manager import ThemeManager


def zone_rect(rect: QRect, zone: DropZone) -> QRect:
    """The part of a pane a panel dropped on ``zone`` takes: a half, or all for tabs."""
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    match zone:
        case DropZone.LEFT:
            return QRect(x, y, w // 2, h)
        case DropZone.RIGHT:
            return QRect(x + w - w // 2, y, w // 2, h)
        case DropZone.TOP:
            return QRect(x, y, w, h // 2)
        case DropZone.BOTTOM:
            return QRect(x, y + h - h // 2, w, h // 2)
    return QRect(rect)


class DropIndicator(QWidget):
    """Where a dragged panel will land: the theme's accent, drawn over the panes."""

    def __init__(self, theme: ThemeManager, parent: QWidget):
        super().__init__(parent)
        self._theme = theme
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, event: QPaintEvent) -> None:
        accent = QColor(self._theme.current.colors.accent)
        fill = QColor(accent)
        fill.setAlpha(60)
        painter = QPainter(self)
        painter.fillRect(self.rect(), fill)
        painter.setPen(QPen(accent, 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))
