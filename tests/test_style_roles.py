import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QWidget

from rzgit.ui.theme.app_theme import LIGHT_THEME
from rzgit.ui.theme.style_roles import Role, Surface, set_role, set_surface
from rzgit.ui.theme.theme_manager import ThemeManager
from tests.support import qt_app


class StyleRolesTests(unittest.TestCase):
    def setUp(self):
        self.app = qt_app()
        ThemeManager(self.app, LIGHT_THEME)

    def test_set_surface_sets_property_and_styled_background(self):
        w = QWidget()
        set_surface(w, Surface.SIDEBAR)
        self.assertEqual(w.property("surface"), "sidebar")
        self.assertTrue(w.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))

    def test_set_role(self):
        label = QLabel("x")
        set_role(label, Role.SECTION_HEADER)
        self.assertEqual(label.property("role"), "section-header")

    def test_change_after_shown_restyles(self):
        w = QWidget()
        set_surface(w, Surface.CONTENT)
        w.resize(20, 20)
        w.show()
        self.app.processEvents()
        content = w.grab().toImage().pixelColor(10, 10).name()
        set_surface(w, Surface.SIDEBAR)
        self.app.processEvents()
        sidebar = w.grab().toImage().pixelColor(10, 10).name()
        self.assertEqual(content, LIGHT_THEME.colors.content_bg)
        self.assertEqual(sidebar, LIGHT_THEME.colors.sidebar_bg)
        w.close()


if __name__ == "__main__":
    unittest.main()
