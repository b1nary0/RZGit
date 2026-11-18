"""The Diff panel's find bar (spec §5.4, RZG-74): a query field, the match
options, ↑ and ↓, the count and ×. It knows nothing of the diff: it says what
to find and when to step, and shows the count it's given."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon, QKeyEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

from rzgit.ui.panels.find_query import FindQuery
from rzgit.ui.theme.style_roles import Role, set_role

SEARCH_DELAY_MS = 100  # after the last keystroke
NO_RESULTS = "No results"
INVALID = "Invalid pattern"


@dataclass
class _Options:
    case: bool = False
    word: bool = False
    regex: bool = False
    keep: bool = False


# The toggles, kept while the app runs (one setting for every bar), not saved.
_options = _Options()


class FindBar(QWidget):
    changed = Signal()  # the query or an option changed: search again
    next_requested = Signal()
    previous_requested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 4, 2)
        layout.setSpacing(2)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Find")
        self.field.setClearButtonEnabled(True)
        self.field.installEventFilter(self)
        layout.addWidget(self.field, 1)
        self.case = self._toggle("Aa", "Match case", _options.case)
        self.word = self._toggle("W", "Whole word", _options.word)
        self.regex = self._toggle(".*", "Regular expression", _options.regex)
        self.keep = self._toggle("Keep", "Keep the search when another file is selected",
                                 _options.keep)
        for toggle in (self.case, self.word, self.regex, self.keep):
            layout.addWidget(toggle)
        self.count = QLabel()
        set_role(self.count, Role.HINT)
        layout.addWidget(self.count)
        self.previous = self._button("go-up", "↑", "Previous match (Shift+F3)")
        self.previous.clicked.connect(self.previous_requested)
        self.next = self._button("go-down", "↓", "Next match (F3)")
        self.next.clicked.connect(self.next_requested)
        self.close_button = self._button("window-close", "×", "Close (Esc)")
        self.close_button.clicked.connect(self.close_bar)
        for button in (self.previous, self.next, self.close_button):
            layout.addWidget(button)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SEARCH_DELAY_MS)
        self._timer.timeout.connect(self.changed)
        self.field.textChanged.connect(lambda _text: self._timer.start())
        for toggle in (self.case, self.word, self.regex):
            toggle.toggled.connect(self._option_toggled)
        self.keep.toggled.connect(self._remember)

    def query(self) -> FindQuery:
        return FindQuery(self.field.text(), self.case.isChecked(), self.word.isChecked(),
                         self.regex.isChecked())

    def open(self, text: str | None = None) -> None:
        """Show the bar and focus the field, all of it selected; with ``text``, fill
        it first. It doesn't emit ``changed``: the owner searches."""
        if text is not None:
            self.field.setText(text)
        self._timer.stop()
        self.show()
        self.field.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.field.selectAll()

    def close_bar(self) -> None:
        self._timer.stop()
        was_open = self.isVisible()
        self.hide()
        if was_open:
            self.closed.emit()

    def flush(self) -> bool:
        """Search now if a keystroke is still waiting; whether one was."""
        if not self._timer.isActive():
            return False
        self._timer.stop()
        self.changed.emit()
        return True

    def set_count(self, current: int | None, total: int, invalid: bool = False,
                  more: bool = False) -> None:
        """Show "3 of 12" (``current`` counts from 0), "12 matches", "No results"
        or, for a pattern that doesn't compile, "Invalid pattern". With ``more``,
        there are more than ``total``: "3 of 10,000+"."""
        plus = "+" if more else ""
        if invalid:
            text = INVALID
        elif not self.field.text():
            text = ""
        elif total == 0:
            text = NO_RESULTS
        elif current is None:
            text = f"{total:,}{plus} match{'' if total == 1 and not more else 'es'}"
        else:
            text = f"{current + 1:,} of {total:,}{plus}"
        self.count.setText(text)
        set_role(self.count, Role.HINT_WARNING if invalid else Role.HINT)
        for button in (self.previous, self.next):
            button.setEnabled(total > 0)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.field and event.type() is QEvent.Type.KeyPress:
            key_event: QKeyEvent = event  # type: ignore[assignment]
            key = key_event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if not self.flush():  # a waiting search goes to the first match itself
                    shift = key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier
                    (self.previous_requested if shift else self.next_requested).emit()
                return True
            if key == Qt.Key.Key_Escape:
                self.close_bar()
                return True
        return super().eventFilter(watched, event)

    def _option_toggled(self) -> None:
        self._remember()
        self._timer.stop()
        self.changed.emit()

    def _remember(self) -> None:
        _options.case, _options.word = self.case.isChecked(), self.word.isChecked()
        _options.regex, _options.keep = self.regex.isChecked(), self.keep.isChecked()

    def _toggle(self, text: str, tooltip: str, checked: bool) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _button(self, icon: str, text: str, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(QIcon.fromTheme(icon))
        button.setText(text)
        button.setToolTip(tooltip)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setIconSize(QSize(16, 16))
        return button
