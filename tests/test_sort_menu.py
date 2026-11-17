import unittest

import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeView, QWidget

from rzgitclient.core.column_prefs import ColumnPrefs
from rzgitclient.core.sort_options import SortOption, SortOptions, SortOrder
from rzgitclient.core.sort_prefs import SortPrefs
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.widgets.column_header import ColumnHeader
from rzgitclient.ui.widgets.sort_menu import REVERSED, SORT_BY, SortMenu
from tests.support import click_column_label, qt_app

SORTS = SortOptions((
    SortOption("age", "Age", column="Age"),
    SortOption("message", "Message", str.casefold, column="Message"),
    SortOption("size", "Size", len, descending=True),
))
LABELS = ("Message", "Branch", "Age")


class SortMenuTests(unittest.TestCase):
    """A panel's Sort By menu and its order (spec §5.1, RZG-43)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def setUp(self):
        self.prefs = SortPrefs()
        self.owner = QWidget()
        self.addCleanup(self.owner.deleteLater)
        self.menu = self.make_menu()
        self.changes = []
        self.menu.order_changed.connect(lambda: self.changes.append(self.menu.order))

    def make_menu(self) -> SortMenu:
        return SortMenu("stash", SORTS, self.prefs, self.owner)

    def entries(self) -> list[tuple[str, bool]]:
        return [(a.text(), a.isChecked()) for a in self.menu.actions() if not a.isSeparator()]

    def make_header(self) -> tuple[QTreeView, ColumnHeader]:
        view = QTreeView()
        self.addCleanup(lambda: shiboken6.isValid(view) and view.deleteLater())
        header = ColumnHeader("stash", ColumnPrefs(), LIGHT_THEME)
        view.setHeader(header)
        model = QStandardItemModel(0, len(LABELS), view)
        model.setHorizontalHeaderLabels(LABELS)
        view.setModel(model)
        view.resize(400, 200)
        view.show()
        QTest.qWaitForWindowExposed(view)
        return view, header

    def test_the_options_as_radio_items_then_reversed(self):
        self.assertEqual(self.menu.title(), SORT_BY)
        self.assertEqual(self.menu.menuAction().menu(), self.menu)  # a submenu in the ⋯ menu
        self.assertEqual(self.entries(), [("Age", True), ("Message", False), ("Size", False),
                                          (REVERSED, False)])
        self.assertTrue(self.menu.actions()[3].isSeparator())
        self.assertEqual(self.menu.order, SortOrder("age"))

    def test_choosing_an_option_sorts_saves_and_emits(self):
        self.menu.actions()[1].trigger()
        self.assertEqual(self.menu.order, SortOrder("message"))
        self.assertEqual(self.changes, [SortOrder("message")])
        self.assertEqual(self.prefs.order("stash"), SortOrder("message"))
        self.assertEqual(self.entries()[:2], [("Age", False), ("Message", True)])
        self.assertEqual(self.menu.apply(["b", "C", "a"]), ["a", "b", "C"])

    def test_reversed_flips_the_order_and_a_new_option_starts_unreversed(self):
        self.menu.reversed_action.trigger()
        self.assertEqual(self.menu.order, SortOrder("age", True))
        self.assertEqual(self.menu.apply(["x", "y"]), ["y", "x"])
        self.menu.choose("size")
        self.assertEqual(self.menu.order, SortOrder("size"))
        self.assertFalse(self.menu.reversed_action.isChecked())

    def test_the_natural_order_is_saved_as_none(self):
        self.menu.choose("message")
        self.menu.choose("age")
        self.assertIsNone(self.prefs.order("stash"))
        self.menu.choose("age")  # no change: no signal
        self.assertEqual(len(self.changes), 2)

    def test_a_default_other_than_the_natural_order_is_saved_as_none(self):
        """RZG-50: the natural order is then saved like any other."""
        menu = SortMenu("stash", SortOptions(SORTS.options, default_key="size"),
                        self.prefs, self.owner)
        self.assertEqual(menu.order, SortOrder("size"))
        self.assertTrue(menu._actions["size"].isChecked())
        menu.choose("age")
        self.assertEqual(self.prefs.order("stash"), SortOrder("age"))
        menu.choose("size")
        self.assertIsNone(self.prefs.order("stash"))

    def test_a_new_menu_starts_with_the_saved_order(self):
        self.prefs.set_order("stash", SortOrder("size", True))
        menu = self.make_menu()
        self.assertEqual(menu.order, SortOrder("size", True))
        self.assertTrue(menu.reversed_action.isChecked())
        self.prefs.set_order("stash", SortOrder("gone"))  # an option this version hasn't
        self.assertEqual(self.make_menu().order, SortOrder("age"))

    # ---- column labels ----------------------------------------------------------

    def test_attached_labels_sort_and_show_the_arrow(self):
        _view, header = self.make_header()
        self.menu.attach(header)
        self.assertTrue(header.is_sortable(0))  # Message
        self.assertFalse(header.is_sortable(1))  # Branch: no option
        self.assertEqual(header.sort_indicator, ("Age", False))
        click_column_label(header, 0)
        self.assertEqual(self.menu.order, SortOrder("message"))
        self.assertEqual(header.sort_indicator, ("Message", False))
        click_column_label(header, 0)  # again: reversed
        self.assertEqual(self.menu.order, SortOrder("message", True))
        self.assertEqual(header.sort_indicator, ("Message", True))
        click_column_label(header, 1)  # not sortable: nothing
        self.assertEqual(self.menu.order, SortOrder("message", True))

    def test_an_option_without_a_column_shows_no_arrow(self):
        _view, header = self.make_header()
        self.menu.attach(header)
        self.menu.choose("size")
        self.assertIsNone(header.sort_indicator)

    def test_the_menu_follows_a_deleted_header(self):
        view, header = self.make_header()
        self.menu.attach(header)
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.menu.choose("message")  # no RuntimeError from the deleted header
        self.assertEqual(self.menu._headers, [])


if __name__ == "__main__":
    unittest.main()
