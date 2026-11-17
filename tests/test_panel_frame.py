import unittest

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel

from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import ROLE_PROPERTY, Role, Surface
from tests.support import make_panel_context, qt_app


class FilesLikePanel(Panel):
    panel_id = "test-files"
    title = "Files"
    surface = Surface.SIDEBAR

    def _setup(self):
        QLabel("content", self)


class PanelFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.panel = FilesLikePanel(make_panel_context())

    def test_header_shows_title_and_panel_sits_below_it(self):
        frame = PanelFrame(self.panel)
        self.assertEqual(frame.header.text(), "FILES")
        self.assertIs(self.panel.parentWidget(), frame)
        layout = frame.layout()
        self.assertEqual(
            [layout.itemAt(i).widget() for i in range(layout.count())],
            [frame.header, self.panel],
        )

    def test_header_follows_header_changed(self):
        frame = PanelFrame(self.panel)
        self.panel.set_header("Files (19)")
        self.assertEqual(frame.header.text(), "FILES (19)")

    def test_new_frame_shows_the_latest_header(self):
        # A reused panel re-wrapped by the layout keeps its dynamic header.
        self.panel.set_header("Files (19)")
        frame = PanelFrame(self.panel)
        self.assertEqual(frame.header.text(), "FILES (19)")

    def test_frame_takes_the_panel_surface(self):
        frame = PanelFrame(self.panel)
        self.assertEqual(frame.property("surface"), "sidebar")

    def test_renders_header_on_its_own_background_above_the_surface(self):
        from rzgitclient.ui.theme.app_theme import LIGHT_THEME

        frame = PanelFrame(self.panel)
        frame.resize(200, 100)
        frame.show()
        self.app.processEvents()
        image = frame.grab().toImage()
        self.assertEqual(image.pixelColor(1, 1).name(), LIGHT_THEME.colors.header_bg)
        below = image.pixelColor(1, frame.height() - 2).name()
        self.assertEqual(below, LIGHT_THEME.colors.sidebar_bg)
        frame.close()


if __name__ == "__main__":
    unittest.main()


class HeaderMenuTests(unittest.TestCase):
    """The ⋯ button of the shared header menu (RZG-48, spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.panel = FilesLikePanel(make_panel_context())
        self.frame = PanelFrame(self.panel)
        self.frame.resize(300, 200)
        self.frame.show()
        self.addCleanup(self.frame.deleteLater)
        self.app.processEvents()

    def test_no_button_without_actions(self):
        self.assertIsNone(self.frame.menu_button)  # not even made

    def test_the_button_shows_the_panel_s_actions_and_follows_them(self):
        one, two = QAction("One", self.panel), QAction("Two", self.panel)
        self.panel.set_header_actions([one, two])
        self.app.processEvents()
        button = self.frame.menu_button
        self.assertTrue(button.isVisible())
        self.assertEqual(button.menu().actions(), [one, two])
        self.assertEqual(button.property(ROLE_PROPERTY), Role.HEADER_MENU)
        self.panel.set_header_actions([two])
        self.assertEqual(button.menu().actions(), [two])
        self.panel.set_header_actions([])
        self.assertFalse(button.isVisible())

    def test_the_button_is_inside_the_header_at_its_right(self):
        self.panel.set_header_actions([QAction("One", self.panel)])
        self.app.processEvents()
        button, header = self.frame.menu_button, self.frame.header
        self.assertIs(button.parentWidget(), header)
        self.assertLessEqual(button.geometry().right(), header.width())
        self.assertGreater(button.geometry().left(), header.width() // 2)
        self.assertEqual(header.text(), "FILES")  # the title is untouched
