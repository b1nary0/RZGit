import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

from rzgit.ui.panels import commit_details_panel
from rzgit.ui.panels.commit_details_panel import CommitDetailsPanel, format_date, full_date
from rzgit.ui.theme.app_theme import DARK_THEME
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class CommitDetailsPanelTests(unittest.TestCase):
    """The commit selected in History, in full (RZG-26, spec §5.4)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "tag", "v1")
        commit_file(self.repo, "b.txt", "2", "Second commit\n\nWhy it was made,\nin two lines.")
        git(self.repo, "-c", "user.name=Reviewer", "commit", "-q", "--allow-empty",
            "-m", "third", "--author", "Writer <writer@example.com>")
        self.ctx = make_panel_context()
        self.session, self.selection = self.ctx.session, self.ctx.selection
        self.panel = CommitDetailsPanel(self.ctx)
        self.addCleanup(self.panel.deleteLater)
        self.session.open(self.repo)
        wait_idle(self.session)
        self.third, self.second, self.first = self.session.history

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    @classmethod
    def tearDownClass(cls):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def choose(self, commit):
        self.selection.commit = commit
        wait_idle(self.session)

    def test_collapsed_and_unbuilt_on_uncommitted_changes(self):
        self.assertTrue(self.panel.collapsed)
        self.assertIsNone(self.panel._content)
        self.assertEqual(self.panel.header, "Commit Details")

    def test_a_selected_commit_shows_its_message_sha_parents_and_author(self):
        self.choose(self.second)
        p = self.panel
        self.assertFalse(p.collapsed)
        self.assertFalse(p._placeholder.isVisibleTo(p))
        self.assertEqual(p._summary.text(), "Second commit")
        self.assertEqual(p._body.text(), "Why it was made, in two lines.")  # reflowed
        self.assertEqual(p._sha.text(), self.second.sha)
        self.assertEqual([lbl.property("sha") for lbl in p._parents], [self.first.sha])
        self.assertEqual(p._author.text(),
                         f"Test User <test@example.com>\n{format_date(self.second.date)}")
        self.assertTrue(p._summary.font().bold())
        self.assertEqual(p._sha.font().family(), self.ctx.theme.current.mono_font().family())

    def test_refs_and_committer_only_when_there_are_any(self):
        self.choose(self.second)
        self.assertFalse(self.panel._refs.isVisibleTo(self.panel))
        self.assertFalse(self.panel._committer.isVisibleTo(self.panel))  # the same as the author
        self.choose(self.first)
        self.assertTrue(self.panel._refs.isVisibleTo(self.panel))
        self.assertEqual(self.panel._refs.text(), "tag: v1")
        self.assertEqual(self.panel._parents, [])  # the first commit
        self.choose(self.third)
        self.assertEqual(self.panel._refs.text(), "HEAD -> main")
        self.assertTrue(self.panel._committer.isVisibleTo(self.panel))
        self.assertTrue(self.panel._committer.text().startswith("Reviewer <test@example.com>"))
        self.assertTrue(self.panel._author.text().startswith("Writer <writer@example.com>"))

    def test_the_commit_s_summary_shows_until_the_details_arrive(self):
        self.selection.commit = self.second  # not waited for
        self.assertEqual(self.panel._summary.text(), "Second commit")
        self.assertEqual(self.panel._sha.text(), self.second.sha)
        self.assertFalse(self.panel._body.isVisibleTo(self.panel))
        wait_idle(self.session)
        self.assertTrue(self.panel._body.isVisibleTo(self.panel))

    def test_a_new_tag_shows_after_a_refresh(self):
        self.choose(self.second)
        git(self.repo, "tag", "v2", self.second.sha)
        self.session.refresh()
        wait_idle(self.session)
        self.assertEqual(self.panel._refs.text(), "tag: v2")

    def test_copy_puts_the_full_sha_on_the_clipboard(self):
        self.choose(self.second)
        self.panel._copy.click()
        self.assertEqual(QGuiApplication.clipboard().text(), self.second.sha)
        menu = self.panel._parent_menu(self.panel._parents[0], QPoint())
        [action] = menu.actions()
        self.assertEqual(action.text(), commit_details_panel.COPY_SHA)
        action.trigger()
        self.assertEqual(QGuiApplication.clipboard().text(), self.first.sha)

    def test_a_loaded_parent_is_a_link_that_selects_it(self):
        self.choose(self.third)
        [parent] = self.panel._parents
        self.assertIn(f'href="{self.second.sha}"', parent.text())
        parent.linkActivated.emit(self.second.sha)
        self.assertEqual(self.selection.commit, self.second)

    def test_a_parent_history_hasnt_loaded_is_plain_text(self):
        with mock.patch("rzgit.core.repo_session.HISTORY_PAGE", 2):
            self.session.open(self.repo)  # one page: third and second
            wait_idle(self.session)
        self.assertEqual(len(self.session.history), 2)
        self.choose(self.session.history[1])
        [parent] = self.panel._parents
        self.assertEqual(parent.text(), self.first.short_sha)
        self.panel.select_parent(self.first.sha)
        self.assertEqual(self.selection.commit, self.session.history[1])  # nothing to select

    def test_back_to_uncommitted_changes_collapses_it(self):
        self.choose(self.second)
        self.choose(None)
        self.assertTrue(self.panel.collapsed)
        self.assertTrue(self.panel._placeholder.isVisibleTo(self.panel))
        self.assertEqual(self.panel._placeholder.text(), commit_details_panel.NO_COMMIT)
        self.assertIsNone(self.session.commit_details_sha)

    def test_a_panel_created_later_shows_the_selected_commit(self):
        self.choose(self.second)
        late = CommitDetailsPanel(self.ctx)
        self.addCleanup(late.deleteLater)
        self.assertFalse(late.collapsed)
        self.assertEqual(late._body.text(), "Why it was made, in two lines.")

    def test_the_sha_never_makes_it_scroll_sideways(self):
        self.panel.resize(200, 300)  # narrower than the SHA
        self.panel.show()
        self.choose(self.second)
        QCoreApplication.processEvents()
        [content] = self.panel._stacks
        self.assertEqual(content.widget().width(), content.viewport().width())
        self.assertEqual(self.panel._sha.toolTip(), self.second.sha)
        self.assertTrue(self.panel._copy.isVisible())

    def test_three_segments_summary_body_and_details(self):
        self.choose(self.second)
        top, body, details = self.panel.segments
        self.assertIs(top.header_label, self.panel._summary)
        self.assertEqual(top.header.property("role"), "segment-header")
        self.assertTrue(body.fill)
        self.assertIs(body.content.layout().itemAt(0).widget(), self.panel._body)
        self.assertEqual(details.header_label.text(), commit_details_panel.DETAILS)
        self.assertTrue(self.panel._sha.isVisibleTo(details))

    def test_a_long_body_scrolls_and_the_details_stay_in_view(self):
        long = "Summary line\n\n" + "\n".join(f"Body line {i}" for i in range(80))
        git(self.repo, "commit", "-q", "--allow-empty", "-m", long)
        self.session.refresh()
        wait_idle(self.session)
        self.panel.resize(260, 320)
        self.panel.show()
        self.choose(self.session.history[0])
        for _ in range(4):
            QCoreApplication.processEvents()
        [stack] = self.panel._stacks  # narrow: one stack
        _top, body, details = stack.segments
        self.assertGreater(body.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(stack.verticalScrollBar().maximum(), 0)
        bottom = details.mapTo(stack.viewport(), details.rect().bottomLeft()).y()
        self.assertLessEqual(bottom, stack.viewport().height())
        self.assertTrue(self.panel._author.isVisible())

    def test_no_body_no_body_segment(self):
        self.choose(self.third)  # "third": a summary only
        _top, body, _details = self.panel.segments
        self.assertFalse(body.isVisibleTo(self.panel))

    def test_a_hard_wrapped_body_is_reflowed_for_display_only(self):
        """RZG-37: the label wraps paragraphs to the panel; git's lines stay git's."""
        body = ("A body hard-wrapped at seventy-two characters, as editors do it,\n"
                "so it reads as one paragraph here.\n\n- an item\n- another")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", f"Wrapped\n\n{body}")
        self.session.refresh()
        wait_idle(self.session)
        self.choose(self.session.history[0])
        self.assertEqual(self.panel._body.text(), (
            "A body hard-wrapped at seventy-two characters, as editors do it, so it reads "
            "as one paragraph here.\n\n- an item\n- another"))
        self.assertEqual(self.panel.details().body, body)

    def test_the_message_menu_copies_the_selection_or_git_s_message(self):
        git(self.repo, "commit", "-q", "--allow-empty", "-m",
            "Wrapped\n\nA body hard-wrapped at seventy-two characters, as editors\ndo it.")
        self.session.refresh()
        wait_idle(self.session)
        self.choose(self.session.history[0])
        body = self.panel._body
        menu = self.panel.message_menu(body)
        copy, message, _sep, select = menu.actions()
        self.assertEqual([copy.text(), message.text(), select.text()],
                         [commit_details_panel.COPY_SELECTION, commit_details_panel.COPY_MESSAGE,
                          commit_details_panel.SELECT_ALL])
        self.assertFalse(copy.isEnabled())  # nothing selected
        message.trigger()
        self.assertEqual(QGuiApplication.clipboard().text(),
                         "Wrapped\n\nA body hard-wrapped at seventy-two characters, as "
                         "editors\ndo it.")
        select.trigger()
        self.assertEqual(body.selectedText(), body.text())
        copy = self.panel.message_menu(body).actions()[0]
        self.assertTrue(copy.isEnabled())
        copy.trigger()
        self.assertEqual(QGuiApplication.clipboard().text(),
                         "A body hard-wrapped at seventy-two characters, as editors do it.")
        summary_menu = self.panel.message_menu(self.panel._summary)
        self.assertEqual(len(summary_menu.actions()), 4)

    def test_copy_message_waits_for_the_details(self):
        self.selection.commit = self.second  # not waited for
        message = self.panel.message_menu(self.panel._summary).actions()[1]
        self.assertFalse(message.isEnabled())
        wait_idle(self.session)

    def show_at(self, width, height=320):
        self.panel.resize(width, height)
        self.panel.show()
        for _ in range(3):
            QCoreApplication.processEvents()

    def test_wide_details_is_a_column_beside_the_message(self):
        """RZG-65: over WIDE px, the message on the left and Details on the right."""
        self.choose(self.second)
        self.show_at(commit_details_panel.WIDE + 100)
        p = self.panel
        self.assertTrue(p.wide)
        message, side = p._stacks
        top, body, details = p.segments
        self.assertEqual(message.segments, (top, body))
        self.assertEqual(side.segments, (details,))
        self.assertEqual(side.width(), commit_details_panel.DETAILS_WIDTH)
        self.assertEqual(side.x(), message.geometry().right() + 1)
        self.assertEqual(side.geometry().right(), p.width() - 1)
        self.assertEqual(top.mapTo(p, QPoint(0, 0)).y(), details.mapTo(p, QPoint(0, 0)).y())
        self.assertTrue(p._sha.isVisible())

    def test_narrow_details_is_below_the_message_and_back(self):
        self.choose(self.second)
        self.show_at(commit_details_panel.WIDE + 100)
        self.panel._summary.setSelection(0, 3)
        self.show_at(commit_details_panel.WIDE)  # not wider than WIDE: narrow
        p = self.panel
        self.assertFalse(p.wide)
        [stack] = p._stacks
        self.assertEqual(stack.segments, p.segments)
        top, body, details = p.segments
        self.assertGreater(details.mapTo(p, QPoint(0, 0)).y(), body.mapTo(p, QPoint(0, 0)).y())
        self.assertEqual(p._summary.text(), "Second commit")  # kept, with its selection
        self.assertEqual(p._summary.selectedText(), "Sec")
        self.show_at(commit_details_panel.WIDE + 1)
        self.assertTrue(p.wide)
        self.assertEqual(len(p._stacks), 2)

    def test_a_hidden_panel_is_built_for_the_width_of_what_holds_it(self):
        """Built while hidden (collapsed), its own width is stale: the shown container's
        is used, so it's arranged once, wide, not narrow first."""
        holder = QWidget()
        self.addCleanup(holder.deleteLater)
        holder.resize(commit_details_panel.WIDE + 200, 400)
        self.panel.setParent(holder)
        self.panel.resize(400, 300)  # a hidden panel's stale width: narrow
        self.panel.hide()
        holder.show()
        with mock.patch.object(commit_details_panel, "PanelSegments",
                               wraps=commit_details_panel.PanelSegments) as made:
            self.choose(self.second)
            self.panel.setGeometry(holder.rect())
            self.panel.show()
            for _ in range(3):
                QCoreApplication.processEvents()
        self.assertEqual(made.call_count, 2)  # the message and Details: wide at once
        self.assertTrue(self.panel.wide)

    def test_a_panel_built_wide_starts_wide(self):
        self.show_at(commit_details_panel.WIDE + 300)
        self.choose(self.second)  # built now, at that width
        self.assertTrue(self.panel.wide)

    def test_the_message_lines_are_capped_and_kept_on_the_left(self):
        """RZG-65: at most MESSAGE_CHARS average characters wide, the rest empty."""
        long = "Summary " * 40 + "\n\n" + "A long body line. " * 40
        git(self.repo, "commit", "-q", "--allow-empty", "-m", long)
        self.session.refresh()
        wait_idle(self.session)
        self.choose(self.session.history[0])
        self.show_at(2400)
        p = self.panel
        for label in (p._summary, p._body):
            with self.subTest(label.text()[:7]):
                cap = commit_details_panel.MESSAGE_CHARS * label.fontMetrics().averageCharWidth()
                self.assertEqual(label.width(), cap)
                self.assertLess(cap, p._stacks[0].width() - 100)  # space is left over
                self.assertLessEqual(label.x(), 8)  # on the left, after its margin
                self.assertGreater(label.height(), 2 * label.fontMetrics().lineSpacing())
        self.show_at(500)  # narrower than the cap: the label takes the width
        self.assertLess(p._body.width(), 500)

    def test_the_theme_keeps_the_fonts(self):
        self.choose(self.third)
        self.panel.apply_theme(DARK_THEME)
        self.assertTrue(self.panel._summary.font().bold())
        mono = DARK_THEME.mono_font().family()
        self.assertEqual(self.panel._parents[0].font().family(), mono)


class DateTests(unittest.TestCase):
    def test_local_to_the_minute_and_the_full_time_with_its_offset(self):
        date = datetime(2026, 11, 16, 21, 50, 3, tzinfo=timezone(timedelta(hours=1)))
        self.assertEqual(format_date(date), date.astimezone().strftime("%Y-%m-%d %H:%M"))
        self.assertEqual(full_date(date), "2026-11-16 21:50:03 +01:00")
        west = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(-timedelta(hours=5, minutes=30)))
        self.assertEqual(full_date(west), "2026-01-02 03:04:05 -05:30")


if __name__ == "__main__":
    unittest.main()
