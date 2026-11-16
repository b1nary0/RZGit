import dataclasses
import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QLabel

from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.app_theme import LIGHT_THEME
from rzgitclient.ui.theme.style_roles import Surface
from tests.support import (
    wait_idle,
    SignalRecorder,
    commit_file,
    make_panel_context,
    make_repo,
    qt_app,
)

OTHER_THEME = dataclasses.replace(LIGHT_THEME, name="Other")


class BranchNamesPanel(Panel):
    """Shows the branch names; records the order of the base's calls."""

    panel_id = "test-branches"
    title = "Branches"
    surface = Surface.SIDEBAR

    _label: QLabel

    def _setup(self):
        self.calls = ["setup"]
        self._label = QLabel(self)
        self.ctx.session.branches_changed.connect(self._show)
        self._show(self.ctx.session.branches)

    def _show(self, branches):
        self._label.setText(", ".join(b.name for b in branches) or "No branches")

    def apply_theme(self, theme):
        # The widgets exist by the time the base calls this.
        self.calls.append(("apply_theme", theme.name, self._label is not None))


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self._tmp.name) / "repo")
        commit_file(self.repo, "a.txt", "1", "first")
        self.ctx = make_panel_context()

    def tearDown(self):
        self._tmp.cleanup()

    # ---- construction ---------------------------------------------------

    def test_setup_runs_before_first_apply_theme(self):
        panel = BranchNamesPanel(self.ctx)
        self.assertEqual(panel.calls, ["setup", ("apply_theme", "Light", True)])
        self.assertIs(panel.ctx, self.ctx)

    def test_surface_is_set_on_the_panel(self):
        panel = BranchNamesPanel(self.ctx)
        self.assertEqual(panel.property("surface"), "sidebar")

    def test_empty_state_then_follows_the_session(self):
        panel = BranchNamesPanel(self.ctx)
        self.assertEqual(panel._label.text(), "No branches")
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        self.assertEqual(panel._label.text(), "main")

    def test_renders_current_state_when_built_late(self):
        self.ctx.session.open(self.repo)
        wait_idle(self.ctx.session)
        panel = BranchNamesPanel(self.ctx)
        self.assertEqual(panel._label.text(), "main")

    # ---- theme ----------------------------------------------------------

    def test_theme_change_reaches_apply_theme(self):
        panel = BranchNamesPanel(self.ctx)
        self.ctx.theme.set_theme(OTHER_THEME)
        self.assertEqual(panel.calls[-1], ("apply_theme", "Other", True))

    def test_deleted_panel_is_disconnected_from_theme(self):
        panel = BranchNamesPanel(self.ctx)
        panel.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.ctx.theme.set_theme(OTHER_THEME)  # must not touch the dead panel

    # ---- header ---------------------------------------------------------

    def test_header_defaults_to_title_and_emits_on_change(self):
        panel = BranchNamesPanel(self.ctx)
        rec = SignalRecorder(panel, "header_changed")
        self.assertEqual(panel.header, "Branches")
        panel.set_header("Branches (2)")
        panel.set_header("Branches (2)")
        self.assertEqual(panel.header, "Branches (2)")
        self.assertEqual(rec.events, [("header_changed", "Branches (2)")])

    def test_request_activation_emits(self):
        panel = BranchNamesPanel(self.ctx)
        asked = []
        panel.activate_requested.connect(lambda: asked.append(1))
        panel.request_activation()
        self.assertEqual(asked, [1])

    # ---- subclass rules -------------------------------------------------

    def test_overriding_init_is_rejected(self):
        with self.assertRaises(TypeError):
            class _Bad(Panel):
                panel_id = "bad"
                title = "Bad"

                def __init__(self, ctx, parent=None):
                    super().__init__(ctx, parent)

    def test_panel_id_and_title_are_required(self):
        with self.assertRaises(TypeError):
            class _NoId(Panel):
                title = "No id"
        with self.assertRaises(TypeError):
            class _NoTitle(Panel):
                panel_id = "no-title"


if __name__ == "__main__":
    unittest.main()
