"""The Settings window (spec §8.2): a page list on the left, the page in an outline.

It shows the declared settings (``core.preferences``), grouped by page, and on
Appearance the theme as well. A change applies at once; the buttons are Restore
Defaults and Close. A page may also have buttons that run something (a
``PageButton``, declared like a setting, RZG-75).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontInfo
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from rzgit.core.preferences import Preferences, Setting, Value, declared
from rzgit.ui.theme.app_theme import FontKind
from rzgit.ui.theme.style_roles import Role, set_role
from rzgit.ui.theme.theme_manager import ThemeManager, ThemeMode

APPEARANCE = "Appearance"
PAGES = (APPEARANCE, "Diff", "History", "Tools")  # first, in this order; then any other
RESTORE = "Restore Defaults"
RESTORE_ALL = "Restore All Settings"
THEME_MODES = ((ThemeMode.SYSTEM, "System"), (ThemeMode.LIGHT, "Light"),
               (ThemeMode.DARK, "Dark"))
FONT_SIZE_MIN = 4.0  # the size box's lowest; 0, the setting's default, shows the theme's
_INT_LIMIT = 2_147_483_647  # QSpinBox's range, for a setting without one


@dataclass(frozen=True)
class PageButton:
    """A row of a page with a button that runs something, not a setting (RZG-75)."""

    page: str
    label: str  # the row's
    text: str  # the button's
    run: Callable[[SettingsWindow], None]
    help: str = ""  # the tooltip


_BUTTONS: list[PageButton] = []


def declare_page_button(button: PageButton) -> PageButton:
    """Add ``button`` to the Settings window, after its page's settings, as the
    module that runs it is imported (like ``declare``)."""
    if any(b.page == button.page and b.label == button.label for b in _BUTTONS):
        raise ValueError(f"page button declared twice: {button.page}: {button.label}")
    _BUTTONS.append(button)
    return button


def declared_page_buttons() -> list[PageButton]:
    return list(_BUTTONS)


class Restore(Enum):
    """What Restore Defaults puts back."""

    ALL = "all"
    PAGE = "page"


def show_window(window: QWidget) -> None:
    """Show ``window`` and bring it to the front."""
    window.show()
    window.raise_()
    window.activateWindow()


def monospaced_families() -> list[str]:
    """The installed fixed-pitch font families, sorted."""
    return sorted(f for f in QFontDatabase.families() if QFontDatabase.isFixedPitch(f))


class _SizeBox(QDoubleSpinBox):
    """A point size: "10", or "10.5", never "10.0"."""

    def textFromValue(self, value: float) -> str:
        return f"{value:g}"


class SettingsWindow(QWidget):
    def __init__(self, prefs: Preferences, theme: ThemeManager | None = None,
                 settings: Sequence[Setting] | None = None, parent: QWidget | None = None,
                 buttons: Sequence[PageButton] | None = None):
        """``settings``: the ones to show (every declared one if None). ``buttons``:
        the page buttons (every declared one if None and ``settings`` is). ``theme``:
        Appearance's Theme and Colour-blind rows drive it (none without it), and
        the font settings show its font while they are at their default."""
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Settings")
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)  # §8.2
        self._prefs, self._theme = prefs, theme
        self._settings = {s.key: s for s in (declared() if settings is None else settings)}
        self.editors: dict[str, QWidget] = {}  # by setting key
        if buttons is None:
            buttons = declared_page_buttons() if settings is None else ()
        self._buttons = tuple(buttons)
        self.page_buttons: dict[str, QPushButton] = {}  # by "page: label"
        self.theme_mode_box: QComboBox | None = None
        self.colour_blind_box: QCheckBox | None = None

        self.page_list = QListWidget()
        self.page_list.setSizeAdjustPolicy(QListWidget.SizeAdjustPolicy.AdjustToContents)
        self._stack = QStackedWidget()
        self._pages: dict[str, list[Setting]] = self._by_page()
        for page, page_settings in self._pages.items():
            self.page_list.addItem(page)
            self._stack.addWidget(self._build_page(page, page_settings))
        self.page_list.currentRowChanged.connect(self._stack.setCurrentIndex)
        self.page_list.setCurrentRow(0)
        self.page_list.setMaximumWidth(self.page_list.sizeHintForColumn(0) + 32)
        self.page_frame = QFrame()  # the page, in a subtle outline (the stylesheet's)
        set_role(self.page_frame, Role.SETTINGS_PAGE)
        frame_layout = QVBoxLayout(self.page_frame)
        frame_layout.setContentsMargins(12, 12, 12, 12)
        frame_layout.addWidget(self._stack)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.restore_button = buttons.addButton(RESTORE, QDialogButtonBox.ButtonRole.ResetRole)
        self.restore_button.clicked.connect(self.restore_defaults)
        buttons.rejected.connect(self.close)
        body = QHBoxLayout()
        body.addWidget(self.page_list)
        body.addWidget(self.page_frame, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addWidget(buttons)
        self.resize(640, 400)

        prefs.changed.connect(self._on_pref_changed)
        if theme is not None:
            theme.theme_changed.connect(self._on_theme_changed)

    # ---- pages ----------------------------------------------------------------

    @property
    def prefs(self) -> Preferences:
        return self._prefs

    @property
    def page_names(self) -> list[str]:
        return list(self._pages)

    @property
    def current_page(self) -> str:
        return self.page_names[self.page_list.currentRow()]

    def show_page(self, page: str) -> None:
        self.page_list.setCurrentRow(self.page_names.index(page))

    def _by_page(self) -> dict[str, list[Setting]]:
        pages: dict[str, list[Setting]] = {}
        if self._theme is not None:
            pages[APPEARANCE] = []
        for setting in self._settings.values():
            pages.setdefault(setting.page, []).append(setting)
        for button in self._buttons:
            pages.setdefault(button.page, [])
        order = [p for p in PAGES if p in pages] + [p for p in pages if p not in PAGES]
        return {page: pages[page] for page in order}

    def _build_page(self, page: str, settings: list[Setting]) -> QWidget:
        widget = QWidget()
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        if page == APPEARANCE and self._theme is not None:
            self._add_theme_rows(form)
        for setting in settings:
            editor = self._editor(setting)
            self.editors[setting.key] = editor
            editor.setToolTip(setting.help)
            if isinstance(editor, QCheckBox):
                form.addRow(editor)
            else:
                form.addRow(setting.label + ":", editor)
                form.labelForField(editor).setToolTip(setting.help)
        for button in self._buttons:
            if button.page == page:
                push = QPushButton(button.text)
                push.setToolTip(button.help)
                push.clicked.connect(lambda _=False, b=button: b.run(self))
                self.page_buttons[f"{page}: {button.label}"] = push
                form.addRow(button.label + ":", push)
                form.labelForField(push).setToolTip(button.help)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(form)
        layout.addStretch(1)
        return widget

    def _add_theme_rows(self, form: QFormLayout) -> None:
        theme = self._theme
        assert theme is not None
        self.theme_mode_box = box = QComboBox()
        for mode, label in THEME_MODES:
            box.addItem(label, mode.value)
        box.activated.connect(lambda _i: theme.set_mode(ThemeMode(box.currentData())))
        form.addRow("Theme:", box)
        self.colour_blind_box = check = QCheckBox("Colour-blind friendly colours")
        check.toggled.connect(theme.set_colour_blind)
        form.addRow(check)
        self._show_theme()

    # ---- editors --------------------------------------------------------------

    def _editor(self, setting: Setting) -> QWidget:
        prefs = self._prefs
        set_value = lambda value, s=setting: prefs.set(s, value)  # noqa: E731
        editor: QWidget
        if setting.kind is bool:
            editor = QCheckBox(setting.label)
            editor.toggled.connect(set_value)
        elif setting.choices or setting.editor == "font":
            editor = QComboBox()
            items = ([(f, f) for f in monospaced_families()] if setting.editor == "font"
                     else [(label, value) for value, label in setting.choices])
            for label, value in items:
                editor.addItem(label, value)
            editor.currentIndexChanged.connect(
                lambda _i, box=editor: set_value(box.currentData()))
        elif setting.kind in (int, float):
            editor = QSpinBox() if setting.kind is int else _SizeBox()
            if isinstance(editor, QDoubleSpinBox):
                editor.setDecimals(1)
            limit = _INT_LIMIT
            low = setting.minimum if setting.minimum is not None else -limit
            if setting.editor == "font_size":
                low = max(low, FONT_SIZE_MIN)
            editor.setRange(low, setting.maximum if setting.maximum is not None else limit)
            if setting.step is not None:
                editor.setSingleStep(setting.step)
            else:  # 20,000 goes up by 1,000
                editor.setStepType(QSpinBox.StepType.AdaptiveDecimalStepType)
            editor.setGroupSeparatorShown(True)
            editor.setKeyboardTracking(False)  # typed: on Enter or leaving it; arrows: at once
            editor.valueChanged.connect(set_value)
        else:
            editor = QLineEdit()
            editor.setPlaceholderText(setting.special)
            editor.setMinimumWidth(240)
            editor.textEdited.connect(set_value)
        self._show_value(setting, editor)
        return editor

    def _shown(self, setting: Setting) -> Value:
        """The value the editor shows: the setting's, or for a font setting at its
        default, the font or size the theme has."""
        value = self._prefs.value(setting)
        if setting.editor == "font" and not value:
            font = self._theme.current.mono_font() if self._theme is not None else QFont()
            if self._theme is None:
                font.setStyleHint(QFont.StyleHint.Monospace)
                font.setFamily("monospace")
            return QFontInfo(font).family()  # what "monospace" is on this desktop
        if setting.editor == "font_size" and not value:
            if self._theme is not None:
                return self._theme.current.fonts.point_size(FontKind.MONO)
            return QFont().pointSizeF()
        return value

    def _show_value(self, setting: Setting, editor: QWidget) -> None:
        value = self._shown(setting)
        blocker = QSignalBlocker(editor)
        if isinstance(editor, QCheckBox):
            editor.setChecked(bool(value))
        elif isinstance(editor, QComboBox):
            index = editor.findData(value)
            if index < 0:  # a font that isn't installed (or isn't fixed-pitch) now
                editor.addItem(str(value), value)
                index = editor.count() - 1
            editor.setCurrentIndex(index)
        elif isinstance(editor, (QSpinBox, QDoubleSpinBox)):
            editor.setValue(value)
        elif isinstance(editor, QLineEdit) and editor.text() != value:
            editor.setText(str(value))
        blocker.unblock()

    def _on_pref_changed(self, key: str, _value: object) -> None:
        setting, editor = self._settings.get(key), self.editors.get(key)
        if setting is not None and editor is not None:
            self._show_value(setting, editor)

    def _show_theme(self) -> None:
        theme = self._theme
        if theme is None or self.theme_mode_box is None or self.colour_blind_box is None:
            return
        with QSignalBlocker(self.theme_mode_box):
            self.theme_mode_box.setCurrentIndex(self.theme_mode_box.findData(theme.mode.value))
        with QSignalBlocker(self.colour_blind_box):
            self.colour_blind_box.setChecked(theme.colour_blind)

    def _on_theme_changed(self, _theme: object) -> None:
        self._show_theme()
        for setting in self._settings.values():  # a font at its default is the theme's
            if setting.editor in ("font", "font_size") and setting.key in self.editors:
                self._show_value(setting, self.editors[setting.key])

    def showEvent(self, event) -> None:
        self._show_theme()  # the View menu may have changed it meanwhile
        super().showEvent(event)

    # ---- Restore Defaults -----------------------------------------------------

    def restore_defaults(self) -> None:
        """Put back the defaults of every setting, or of the current page's, as the
        user chooses."""
        page = self.current_page
        choice = self.ask_restore(page)
        if choice is None:
            return
        pages = self.page_names if choice is Restore.ALL else [page]
        self._prefs.reset(s for p in pages for s in self._pages[p])
        if APPEARANCE in pages and self._theme is not None:
            self._theme.set_mode(ThemeMode.SYSTEM)
            self._theme.set_colour_blind(False)
            self._show_theme()

    def ask_restore(self, page: str) -> Restore | None:
        """Restore All Settings, Restore <page> Settings, or Cancel (None)."""
        box = QMessageBox(QMessageBox.Icon.Question, RESTORE,
                          "Restore the default settings?", parent=self)
        box.setInformativeText(f"Restore every setting, or only those on the {page} page.")
        everything = box.addButton(RESTORE_ALL, QMessageBox.ButtonRole.DestructiveRole)
        this_page = box.addButton(f"Restore {page} Settings", QMessageBox.ButtonRole.AcceptRole)
        cancel = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is everything:
            return Restore.ALL
        return Restore.PAGE if clicked is this_page else None
