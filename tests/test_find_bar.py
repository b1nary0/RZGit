import unittest
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QSignalSpy, QTest

from rzgit.ui.panels import find_bar
from rzgit.ui.panels.find_bar import FindBar
from rzgit.ui.panels.find_query import FindQuery
from tests.support import qt_app


class FindBarTests(unittest.TestCase):
    """The Diff panel's find bar (RZG-74, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        patcher = mock.patch.object(find_bar, "_options", find_bar._Options())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.bar = FindBar()
        self.addCleanup(self.bar.deleteLater)

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_the_query_follows_the_field_and_the_toggles(self):
        self.bar.field.setText("x")
        self.bar.case.setChecked(True)
        self.bar.regex.setChecked(True)
        self.assertEqual(self.bar.query(), FindQuery("x", case=True, regex=True))

    def test_typing_searches_after_a_pause_and_a_toggle_at_once(self):
        changed = QSignalSpy(self.bar.changed)
        self.bar.field.setText("a")
        self.bar.field.setText("ab")
        self.assertEqual(changed.count(), 0)
        QTest.qWait(find_bar.SEARCH_DELAY_MS + 100)
        self.assertEqual(changed.count(), 1)
        self.bar.word.setChecked(True)
        self.assertEqual(changed.count(), 2)

    def test_flush_searches_a_waiting_keystroke_now(self):
        changed = QSignalSpy(self.bar.changed)
        self.assertFalse(self.bar.flush())
        self.bar.field.setText("a")
        self.assertTrue(self.bar.flush())
        self.assertEqual(changed.count(), 1)
        QTest.qWait(find_bar.SEARCH_DELAY_MS + 50)
        self.assertEqual(changed.count(), 1)  # not twice

    def test_enter_and_shift_enter_step(self):
        self.bar.field.setText("a")
        self.bar.flush()
        nexts, previous = QSignalSpy(self.bar.next_requested), QSignalSpy(self.bar.previous_requested)
        QTest.keyClick(self.bar.field, Qt.Key.Key_Return)
        QTest.keyClick(self.bar.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual((nexts.count(), previous.count()), (1, 1))

    def test_enter_with_a_search_waiting_only_searches(self):
        changed, nexts = QSignalSpy(self.bar.changed), QSignalSpy(self.bar.next_requested)
        self.bar.field.setText("a")
        QTest.keyClick(self.bar.field, Qt.Key.Key_Return)
        self.assertEqual((changed.count(), nexts.count()), (1, 0))

    def test_esc_closes(self):
        self.bar.open()
        closed = QSignalSpy(self.bar.closed)
        QTest.keyClick(self.bar.field, Qt.Key.Key_Escape)
        self.assertTrue(self.bar.isHidden())
        self.assertEqual(closed.count(), 1)

    def test_open_fills_the_field_without_searching(self):
        changed = QSignalSpy(self.bar.changed)
        self.bar.open("word")
        self.assertEqual(self.bar.field.text(), "word")
        self.assertEqual(self.bar.field.selectedText(), "word")
        QTest.qWait(find_bar.SEARCH_DELAY_MS + 50)
        self.assertEqual(changed.count(), 0)  # the owner searches

    def test_the_count(self):
        self.bar.field.setText("x")
        for args, text in (((2, 12), "3 of 12"), ((None, 12), "12 matches"),
                           ((None, 1), "1 match"), ((None, 0), find_bar.NO_RESULTS),
                           ((None, 0, True), find_bar.INVALID)):
            self.bar.set_count(*args)
            self.assertEqual(self.bar.count.text(), text)
        self.assertFalse(self.bar.next.isEnabled())
        self.bar.set_count(2, 10_000, more=True)
        self.assertEqual(self.bar.count.text(), "3 of 10,000+")
        self.bar.set_count(None, 1, more=True)
        self.assertEqual(self.bar.count.text(), "1+ matches")
        self.bar.set_count(0, 1)
        self.assertTrue(self.bar.next.isEnabled())
        self.bar.field.setText("")
        self.bar.set_count(None, 0)
        self.assertEqual(self.bar.count.text(), "")

    def test_the_toggles_are_kept_for_the_next_bar(self):
        self.bar.case.setChecked(True)
        self.bar.keep.setChecked(True)
        other = FindBar()
        self.addCleanup(other.deleteLater)
        self.assertTrue(other.case.isChecked())
        self.assertTrue(other.keep.isChecked())
        self.assertFalse(other.word.isChecked())


if __name__ == "__main__":
    unittest.main()
