import tempfile
import unittest
from pathlib import Path

from rzgitclient.ui.panels.branches_panel import BranchesPanel
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle


class BranchesPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        self.ctx = make_panel_context()
        self.panel = BranchesPanel(self.ctx)
        self.list = self.panel._list

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self):
        return [self.list.item(i).text() for i in range(self.list.count())]

    def test_empty_state(self):
        self.assertEqual(self.rows(), [])

    def test_fresh_repo_shows_placeholder(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["main (no commits)"])

    def test_lists_branches_with_current_in_bold(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "feature")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["feature", "main"])
        bold = {self.list.item(i).text(): self.list.item(i).font().bold() for i in range(2)}
        self.assertEqual(bold, {"feature": False, "main": True})

    def test_selection_survives_refresh(self):
        commit_file(self.repo, "a.txt", "1", "first")
        git(self.repo, "branch", "feature")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.list.setCurrentRow(0)  # feature
        git(self.repo, "branch", "another")
        self.ctx.session.refresh()
        wait_idle(self.ctx.session)
        self.assertEqual(self.rows(), ["another", "feature", "main"])
        self.assertEqual(self.list.currentItem().text(), "feature")


if __name__ == "__main__":
    unittest.main()
