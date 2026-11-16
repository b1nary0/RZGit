import unittest

from PySide6.QtWidgets import QLabel

from rzgitclient.ui.layout.panel_frame import PanelFrame
from rzgitclient.ui.panels.panel import Panel
from rzgitclient.ui.theme.style_roles import Surface
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
