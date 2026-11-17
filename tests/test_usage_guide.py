"""The user guide's shortcut table matches the app's shortcuts (spec §10.1, RZG-91)."""

import re
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QAction, QKeySequence, QShortcut

from rzgitclient.core.app_settings import AppSettings
from rzgitclient.ui.main_window import MainWindow
from tests.support import commit_file, git, make_panel_context, make_repo, qt_app, wait_idle

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "USAGE.md"
TABLE_HEADING = "## Keyboard shortcuts"

# Keys a widget handles in its own key events, not through a QAction or QShortcut.
IN_CODE = {
    "Esc": "diff_view.py (clears the selection), find_bar.py (closes the find bar)",
    "Ctrl+A": "diff_view.py (select all of a side); Commit Details' selectable labels",
    "Ctrl+C": "diff_view.py (copy the code); Commit Details' selectable labels",
    "Ctrl+Home": "history_panel.py (_HistoryView.keyPressEvent: the top, past the pinned row)",
    "Shift+Enter": "find_bar.py (the previous match)",
}
# Standard keys whose binding comes from the desktop: the offscreen platform has none
# for these, so the test checks the action follows the standard key instead.
STANDARD = {"Ctrl+Q": QKeySequence.StandardKey.Quit}
# The keyboard's own Open, Refresh, Find and Copy keys, which Qt adds to some standard
# bindings on some platforms. The guide doesn't list them.
KEYBOARD_KEYS = {"Open", "Refresh", "Find", "Copy"}


def normal(key: str) -> str:
    """One spelling per key: Qt's Return and Enter are the same key to a reader."""
    return key.replace("Return", "Enter")


def table_keys(text: str) -> dict[str, str]:
    """The keys in the guide's shortcut table, each with its row's last cell."""
    section = text.split(TABLE_HEADING, 1)[1].split("\n## ", 1)[0]
    keys: dict[str, str] = {}
    for line in section.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) < 3 or set(cells[0]) <= {"-", " ", ":"}:
            continue
        for key in re.findall(r"`([^`]+)`", cells[0]):
            keys[normal(key)] = cells[-1]
    return keys


class ShortcutTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def window(self) -> MainWindow:
        """A window on a repo with a commit and something staged, a commit selected
        and then Uncommitted changes again, so the panels built on demand exist."""
        repo = make_repo(self.tmp / "repo")
        commit_file(repo, "a.txt", "a\n", "first")
        (repo / "a.txt").write_text("b\n")
        git(repo, "add", "a.txt")
        window = MainWindow(make_panel_context(), AppSettings(self.tmp / "settings.ini"))
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.session.shutdown)
        window.show()
        window.session.open(repo)
        wait_idle(window.session)
        selection = window.ctx.selection
        selection.commit = window.session.history[0]
        wait_idle(window.session)
        self.app.processEvents()
        selection.commit = None
        wait_idle(window.session)
        self.app.processEvents()
        return window

    def collected(self, window: MainWindow) -> set[str]:
        keys: set[str] = set()
        actions = window.findChildren(QAction) + window.ctx.actions.findChildren(QAction)
        sequences = [k for a in actions for k in a.shortcuts()]
        sequences += [s.key() for s in window.findChildren(QShortcut)]
        for sequence in sequences:
            key = sequence.toString(QKeySequence.SequenceFormat.PortableText)
            if key and key not in KEYBOARD_KEYS:
                keys.add(normal(key))
        return keys

    def test_the_table_lists_every_shortcut_and_no_other(self):
        documented = table_keys(GUIDE.read_text())
        collected = self.collected(self.window())
        self.assertEqual(sorted(collected - documented.keys()), [],
                         "shortcuts missing from the table in docs/USAGE.md")
        extra = documented.keys() - collected - IN_CODE.keys() - STANDARD.keys()
        self.assertEqual(sorted(extra), [], "keys in the table that the app doesn't have")
        self.assertEqual(sorted(IN_CODE.keys() - documented.keys()), [],
                         "keys handled in code but missing from the table")

    def test_every_row_says_where_it_works(self):
        for key, where in table_keys(GUIDE.read_text()).items():
            self.assertTrue(where, f"{key}: no 'Where it works'")

    def test_standard_keys_follow_the_desktop(self):
        window = MainWindow(make_panel_context(), AppSettings(self.tmp / "settings.ini"))
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.session.shutdown)
        self.assertEqual(window.ctx.actions.quit.shortcuts(),
                         QKeySequence.keyBindings(STANDARD["Ctrl+Q"]))
        self.assertIn("Ctrl+Q", table_keys(GUIDE.read_text()))


if __name__ == "__main__":
    unittest.main()
