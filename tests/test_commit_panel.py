import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid

from rzgitclient.ui.panels.commit_panel import (
    COMMITTING,
    STAGE_FIRST,
    TYPE_MESSAGE,
    CommitPanel,
    join_message,
    split_message,
)
from rzgitclient.ui.theme.style_roles import ROLE_PROPERTY, Role
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle
from tests.test_repo import add_hook


class MessageTests(unittest.TestCase):
    def test_summary_and_description_join_and_split_back(self):
        for summary, description, message in (
            ("Fix it", "", "Fix it"),
            ("Fix it", "Because.\n\nMore.", "Fix it\n\nBecause.\n\nMore."),
            ("", "Only a body", "\n\nOnly a body"),
        ):
            with self.subTest(message=message):
                self.assertEqual(join_message(summary, description), message)
                self.assertEqual(split_message(message), (summary, description))

    def test_a_blank_description_is_left_out(self):
        self.assertEqual(join_message("Fix it", " \n"), "Fix it")


class CommitPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        self.ctx = make_panel_context()
        self.session = self.ctx.session
        self.panel = panel = CommitPanel(self.ctx)
        self.addCleanup(lambda: isValid(panel) and panel.deleteLater())  # one test deletes it
        self.action = self.ctx.actions.commit

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    @property
    def summary(self):
        """The summary field: built when the panel is first shown."""
        self.assertIsNotNone(self.panel._summary)
        return self.panel._summary

    @property
    def description(self):
        self.assertIsNotNone(self.panel._summary)
        return self.panel._description

    def type(self, summary, description=""):
        self.summary.setText(summary)
        self.description.setPlainText(description)

    def fields(self):
        return self.summary.text(), self.description.toPlainText()

    def button(self):
        return self.panel._button.text(), self.panel._button.isEnabled()

    def open(self, path=None):
        self.session.open(path or self.repo)
        wait_idle(self.session)

    def stage(self, name="a.txt", content="2", repo=None):
        repo = repo or self.repo
        (repo / name).write_text(content)
        git(repo, "add", name)

    def refresh(self):
        self.session.refresh()
        wait_idle(self.session)

    def focus(self, widget, event_type):
        reason = Qt.FocusReason.OtherFocusReason
        QCoreApplication.sendEvent(widget, QFocusEvent(event_type, reason))

    def subjects(self):
        return [c.subject for c in self.session.history]

    # ---- shown when something is staged ------------------------------------

    def test_collapsed_with_no_repo_or_nothing_staged(self):
        self.assertTrue(self.panel.collapsed)
        self.open()
        self.assertTrue(self.panel.collapsed)
        self.assertIsNone(self.panel._summary)  # nothing built while it was never shown
        self.stage()
        self.refresh()
        self.assertFalse(self.panel.collapsed)
        self.assertTrue(self.summary.isEnabled())

    def test_stays_while_either_field_has_focus(self):
        focus_in, focus_out = QEvent.Type.FocusIn, QEvent.Type.FocusOut
        self.stage()
        self.open()
        self.focus(self.summary, focus_in)
        git(self.repo, "reset", "-q")
        self.refresh()
        self.assertFalse(self.panel.collapsed)
        self.focus(self.summary, focus_out)  # on to the description
        self.focus(self.description, focus_in)
        self.app.processEvents()
        self.assertFalse(self.panel.collapsed)
        self.focus(self.description, focus_out)  # out of the panel
        self.assertFalse(self.panel.collapsed)  # not until the event loop turns
        self.app.processEvents()
        self.assertTrue(self.panel.collapsed)

    # ---- the button and the Commit action ----------------------------------

    def test_the_button_says_what_is_needed(self):
        self.stage()
        self.stage("b.txt", "b")
        self.open()
        self.assertEqual(self.button(), (TYPE_MESSAGE, False))
        self.type("", "A description is not enough")
        self.assertEqual(self.button(), (TYPE_MESSAGE, False))
        self.assertFalse(self.action.isEnabled())
        self.type("Change a and b")
        self.assertEqual(self.button(), ("Commit 2 Files", True))
        self.assertTrue(self.action.isEnabled())
        self.type("  ")
        self.assertEqual(self.button(), (TYPE_MESSAGE, False))
        self.type("Change a and b")
        git(self.repo, "reset", "-q", "b.txt")
        self.refresh()
        self.assertEqual(self.button(), ("Commit 1 File", True))
        self.focus(self.summary, QEvent.Type.FocusIn)  # kept shown while typing
        git(self.repo, "reset", "-q")
        self.refresh()
        self.assertEqual(self.button(), (STAGE_FIRST, False))
        self.assertFalse(self.action.isEnabled())

    def test_commit_clears_the_fields_and_everything_updates(self):
        self.stage()
        self.open()
        self.type("Change a", "Why it changed")
        self.panel._button.click()
        self.assertEqual(self.button(), (COMMITTING, False))  # no second commit
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["Change a", "first"])
        self.assertEqual(git(self.repo, "log", "-1", "--format=%B"),
                         "Change a\n\nWhy it changed\n\n")
        self.assertEqual(self.session.commit_count, 2)
        self.assertEqual(self.session.status, ())
        self.assertEqual(self.fields(), ("", ""))
        self.assertEqual(self.ctx.drafts.get(self.repo), "")
        self.assertTrue(self.panel.collapsed)  # nothing staged any more

    def test_a_failed_commit_keeps_the_message(self):
        add_hook(self.repo, "pre-commit", "echo 'lint failed' >&2\nexit 1\n")
        self.stage()
        self.open()
        errors = []
        self.session.error_occurred.connect(errors.append)
        self.type("Change a", "Body")
        self.action.trigger()
        wait_idle(self.session)
        self.assertEqual(self.fields(), ("Change a", "Body"))
        self.assertEqual(errors, ["lint failed"])
        self.assertEqual(self.button(), ("Commit 1 File", True))  # can try again

    def test_a_message_changed_while_committing_is_kept(self):
        self.stage()
        self.open()
        self.type("Change a")
        self.action.trigger()
        self.type("Next commit")
        wait_idle(self.session)
        self.assertEqual(self.subjects()[0], "Change a")
        self.assertEqual(self.summary.text(), "Next commit")

    def test_enter_moves_on_and_ctrl_enter_commits_from_either_field(self):
        self.assertIn("Ctrl+Return", [k.toString() for k in self.action.shortcuts()])
        self.stage()
        self.open()
        self.panel.addAction(self.action)  # as the window's menu bar does
        self.panel.show()
        self.addCleanup(self.panel.hide)
        self.panel.activateWindow()
        QTest.qWaitForWindowActive(self.panel)
        self.summary.setFocus()
        self.summary.setText("By keyboard")
        QTest.keyClick(self.summary, Qt.Key.Key_Return)
        self.assertTrue(self.description.hasFocus())
        QTest.keyClick(self.description, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        wait_idle(self.session)
        self.assertEqual(self.subjects()[0], "By keyboard")
        self.stage(content="3")
        self.refresh()
        self.summary.setFocus()
        self.summary.setText("From the summary")
        QTest.keyClick(self.summary, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        wait_idle(self.session)
        self.assertEqual(self.subjects()[0], "From the summary")

    def test_the_first_commit_in_an_empty_repo(self):
        empty = make_repo(Path(self._tmp.name) / "empty")
        self.stage(repo=empty)
        self.open(empty)
        self.type("First")
        self.action.trigger()
        wait_idle(self.session)
        self.assertEqual(self.subjects(), ["First"])
        self.assertEqual([b.name for b in self.session.branches], ["main"])

    def test_a_deleted_panel_disables_commit(self):
        self.stage()
        self.open()
        self.type("Change a")
        self.assertTrue(self.action.isEnabled())
        self.panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(self.action.isEnabled())

    # ---- drafts and the summary hint ---------------------------------------

    def test_each_repository_keeps_its_draft(self):
        other = make_repo(Path(self._tmp.name) / "other")
        self.stage()
        self.stage(repo=other)
        self.open()
        self.type("Draft for repo", "With a body")
        self.open(other)
        self.assertEqual(self.fields(), ("", ""))
        self.type("Draft for other")
        self.open()
        self.assertEqual(self.fields(), ("Draft for repo", "With a body"))
        self.assertEqual(self.ctx.drafts.get(other), "Draft for other")
        self.assertEqual(self.ctx.drafts.get(self.repo), "Draft for repo\n\nWith a body")

    def test_a_new_panel_shows_the_draft(self):
        self.stage()
        self.open()
        self.type("Still here", "And this")
        again = CommitPanel(self.ctx)
        self.addCleanup(again.deleteLater)
        self.assertEqual((again._summary.text(), again._description.toPlainText()),
                         ("Still here", "And this"))

    def test_a_draft_saved_earlier_is_there_when_the_panel_first_shows(self):
        self.ctx.drafts.set(self.repo, "From last time\n\nBody")
        self.open()
        self.assertFalse(self.action.isEnabled())
        self.stage()
        self.refresh()
        self.assertEqual(self.fields(), ("From last time", "Body"))
        self.assertEqual(self.panel._count.text(), "14/50")
        self.assertTrue(self.action.isEnabled())

    def test_summary_counter_warns_past_72(self):
        self.stage()
        self.open()
        count = self.panel._count
        self.assertEqual(count.text(), "")
        self.type("Short subject", "body " * 40)
        self.assertEqual(count.text(), "13/50")
        self.assertEqual(count.property(ROLE_PROPERTY), Role.HINT)
        self.type("x" * 73)
        self.assertEqual(count.text(), "73/50")
        self.assertEqual(count.property(ROLE_PROPERTY), Role.HINT_WARNING)


if __name__ == "__main__":
    unittest.main()
