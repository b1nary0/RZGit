import dataclasses
import tempfile
import unittest
from pathlib import Path

from rzgitclient.ui.panels.history_panel import HistoryPanel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from tests.support import SignalRecorder, commit_file, make_panel_context, make_repo, qt_app, wait_idle


class HistoryPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.repo = make_repo(self.tmp / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        commit_file(self.repo, "a.txt", "2", "second")
        self.ctx = make_panel_context()
        self.panel = HistoryPanel(self.ctx)
        self.tree = self.panel._tree
        self.rec = SignalRecorder(self.ctx.selection, "commit_changed")

    def tearDown(self):
        self._tmp.cleanup()

    def subjects(self):
        return [self.tree.topLevelItem(i).text(0) for i in range(self.tree.topLevelItemCount())]

    def test_empty_state(self):
        self.assertEqual(self.subjects(), [])
        self.assertIsNone(self.ctx.selection.commit)

    def test_rows_hash_and_muted_columns(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.subjects(), ["second", "first"])
        item, commit = self.tree.topLevelItem(0), self.ctx.session.history[0]
        self.assertEqual(item.text(1), "Test User")
        self.assertEqual(item.text(3), commit.short_sha)
        self.assertEqual(item.toolTip(3), commit.sha)
        self.assertEqual(item.font(3).family(), LIGHT_THEME.mono_font().family())
        for col in (1, 2, 3):
            self.assertEqual(item.foreground(col).color().name(), LIGHT_THEME.colors.text_muted)

    def test_selecting_a_row_sets_selection(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.tree.setCurrentItem(self.tree.topLevelItem(1))
        self.assertEqual(self.ctx.selection.commit.subject, "first")

    def test_selection_survives_refresh_silently(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.tree.setCurrentItem(self.tree.topLevelItem(1))
        self.rec.clear()
        commit_file(self.repo, "a.txt", "3", "third")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.subjects(), ["third", "second", "first"])
        self.assertEqual(self.tree.currentItem().text(0), "first")
        self.assertEqual(self.rec.events, [])  # no flicker through None

    def test_selection_cleared_when_commit_is_gone(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.tree.setCurrentItem(self.tree.topLevelItem(0))
        other = make_repo(self.tmp / "other")
        commit_file(other, "b.txt", "1", "other")
        self.ctx.session.open(other)
        wait_idle(self.ctx.session)
        self.assertIsNone(self.tree.currentItem())
        self.assertIsNone(self.ctx.selection.commit)

    def test_apply_theme_recolours_rows(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        colors = dataclasses.replace(LIGHT_THEME.colors, text_muted="#123456")
        self.ctx.theme.set_theme(dataclasses.replace(LIGHT_THEME, colors=colors))
        self.assertEqual(self.tree.topLevelItem(0).foreground(1).color().name(), "#123456")


if __name__ == "__main__":
    unittest.main()
