"""History's floating "↑ Back to top" control (spec §5.4, RZG-64).

A small pill over the commits, below the pinned row. Its owner shows it once the
view is scrolled well down and places it. Clicking it emits ``clicked``. It paints
itself from the theme's ``floating_button_*`` tokens, and fades in and out unless
the desktop turns effects off."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QEnterEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QApplication, QWidget

from rzgit.ui.theme.app_theme import ThemeColors

FADE_MS = 150
RADIUS = 3  # only slightly rounded
PAD_X, PAD_Y = 10, 3
SHADOW = 2  # px of shadow under the pill
SHADOW_ALPHA = (28, 44)  # the outer, then the inner layer of the shadow
TOOLTIP = "Back to top (Ctrl+Home)"


def label(new_commits: int) -> str:
    if new_commits <= 0:
        return "↑ Back to top"
    return f"↑ {new_commits} new commit{'' if new_commits == 1 else 's'}"


def animations_enabled() -> bool:
    """False when the desktop turns effects off: the button then appears at once."""
    return QApplication.isEffectEnabled(Qt.UIEffect.UI_General)


class BackToTopButton(QWidget):
    clicked = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._new_commits = 0
        self._wanted = False  # shown, or fading in; False while fading out or hidden
        self._opacity = 0.0
        self._bg = self._hover = self._text = self._border = self._shadow = QColor()
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(FADE_MS)
        self._fade.valueChanged.connect(self._set_opacity)
        self._fade.finished.connect(self._faded)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Ctrl+Home is the keyboard's way
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(TOOLTIP)
        self.hide()

    # ---- state -----------------------------------------------------------

    @property
    def wanted(self) -> bool:
        """Whether it is shown or fading in (not fading out, nor hidden)."""
        return self._wanted

    @property
    def opacity(self) -> float:
        return self._opacity

    @property
    def new_commits(self) -> int:
        return self._new_commits

    def text(self) -> str:
        return label(self._new_commits)

    def set_new_commits(self, count: int) -> None:
        if count != self._new_commits:
            self._new_commits = count
            self.resize(self.sizeHint())
            self.update()

    def set_wanted(self, wanted: bool) -> None:
        if wanted == self._wanted:
            return
        self._wanted = wanted
        target = 1.0 if wanted else 0.0
        self._fade.stop()
        if not animations_enabled():
            self._set_opacity(target)
            self.setVisible(wanted)
            return
        self.show()
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(target)
        self._fade.start()

    def _set_opacity(self, value: float) -> None:
        self._opacity = float(value)
        self.update()

    def _faded(self) -> None:
        if not self._wanted:
            self.hide()

    def set_colors(self, colors: ThemeColors) -> None:
        self._bg = QColor(colors.floating_button_bg)
        self._hover = QColor(colors.floating_button_hover)
        self._text = QColor(colors.floating_button_text)
        self._border = QColor(colors.floating_button_border)
        self._shadow = QColor(colors.floating_button_shadow)
        self.update()

    # ---- size and painting -----------------------------------------------

    def sizeHint(self) -> QSize:
        metrics = self.fontMetrics()
        return QSize(metrics.horizontalAdvance(self.text()) + 2 * PAD_X + 2 * SHADOW,
                     metrics.height() + 2 * PAD_Y + SHADOW)

    def pill_rect(self) -> QRectF:
        """The pill itself, inside the margin its shadow takes."""
        return QRectF(SHADOW + 0.5, 0.5, self.width() - 2 * SHADOW - 1, self.height() - SHADOW - 1)

    def paintEvent(self, _event: QPaintEvent) -> None:
        if self._opacity <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(self._opacity)
        pill = self.pill_rect()
        painter.setPen(Qt.PenStyle.NoPen)
        for grow, alpha in zip((SHADOW, SHADOW / 2), SHADOW_ALPHA):
            shadow = QColor(self._shadow)
            shadow.setAlpha(alpha)
            painter.setBrush(shadow)
            painter.drawRoundedRect(pill.adjusted(-grow / 2, grow, grow / 2, grow),
                                    RADIUS + grow / 2, RADIUS + grow / 2)
        painter.setPen(self._border)
        painter.setBrush(self._hover if self.underMouse() else self._bg)
        painter.drawRoundedRect(pill, RADIUS, RADIUS)
        painter.setPen(self._text)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()

    # ---- the pointer -----------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        event.accept()  # not the row beneath

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        event.accept()
        if (event.button() == Qt.MouseButton.LeftButton and self._wanted
                and self.rect().contains(event.position().toPoint())):
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        event.accept()

    def enterEvent(self, event: QEnterEvent) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.update()
        super().leaveEvent(event)
