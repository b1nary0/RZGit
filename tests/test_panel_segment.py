import unittest

import shiboken6
from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from rzgitclient.ui.theme.app_theme import DARK_THEME, LIGHT_THEME
from rzgitclient.ui.theme.style_roles import ROLE_PROPERTY, Role, Surface, set_surface
from rzgitclient.ui.theme.theme_manager import ThemeManager
from rzgitclient.ui.widgets.panel_segment import PanelSegment, PanelSegments
from tests.support import qt_app


def text(lines: int, width: int = 1) -> QLabel:
    label = QLabel("\n".join("word " * width for _ in range(lines)))
    label.setWordWrap(True)
    return label


class PanelSegmentTests(unittest.TestCase):
    """A panel's own internal sections (RZG-38, spec §5.2)."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        self.host = QWidget()
        set_surface(self.host, Surface.SIDEBAR)
        self.addCleanup(lambda: shiboken6.delete(self.host))
        QVBoxLayout(self.host).setContentsMargins(0, 0, 0, 0)

    def build(self, body_lines, height, width=260):
        self.top = PanelSegment(header="A summary")
        self.body = PanelSegment(text(body_lines), fill=True)
        self.details = PanelSegment(text(4), header="Details")
        self.stack = PanelSegments((self.top, self.body, self.details))
        self.host.layout().addWidget(self.stack)
        self.host.resize(width, height)
        self.host.show()
        self.settle()

    def settle(self):
        for _ in range(4):
            QCoreApplication.processEvents()

    def bottom(self, widget):
        return widget.mapTo(self.stack.viewport(), widget.rect().bottomLeft()).y()

    def top_of(self, widget):
        return widget.mapTo(self.stack.viewport(), widget.rect().topLeft()).y()

    @staticmethod
    def natural(widget):
        """The height a segment wants at its width (wrapped text depends on it)."""
        if widget.hasHeightForWidth():
            return widget.heightForWidth(widget.width())
        return widget.sizeHint().height()

    def line(self):
        return self.body.fontMetrics().lineSpacing()

    def test_a_short_body_takes_only_its_room(self):
        self.build(body_lines=2, height=600)
        body_text = self.body.content
        self.assertEqual(self.body.height(), body_text.height())  # no gap inside
        self.assertEqual(self.top_of(self.details), self.bottom(self.body) + 1)  # none between
        self.assertLess(self.bottom(self.details), self.stack.viewport().height() - 50)
        self.assertEqual(self.body.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.stack.verticalScrollBar().maximum(), 0)

    def test_a_long_body_scrolls_by_itself_and_the_details_stay_in_view(self):
        self.build(body_lines=80, height=300)
        self.assertGreater(self.body.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.stack.verticalScrollBar().maximum(), 0)  # the panel doesn't
        self.assertLessEqual(self.bottom(self.details), self.stack.viewport().height())
        self.assertEqual(self.details.height(), self.natural(self.details))  # whole
        self.assertEqual(self.top.height(), self.natural(self.top))

    def test_too_short_for_the_pinned_segments_the_whole_stack_scrolls(self):
        self.build(body_lines=80, height=90)
        self.assertGreater(self.stack.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(self.body.height(), self.line())  # one line at the least
        self.assertEqual(self.details.height(), self.natural(self.details))

    def test_contents_never_scroll_sideways(self):
        wide = QLabel("x" * 400)  # wider than the segment, and can't wrap
        segment = PanelSegment(wide, header="a long summary " * 20)
        self.stack = PanelSegments((segment,))
        self.host.layout().addWidget(self.stack)
        self.host.resize(200, 200)
        self.host.show()
        self.settle()
        self.assertEqual(self.stack.widget().width(), self.stack.viewport().width())
        self.assertLessEqual(wide.width(), self.stack.viewport().width())
        self.assertGreater(segment.header_label.height(), segment.header_label.fontMetrics()
                           .lineSpacing() * 2)  # the long header wrapped

    def test_headers_text_or_widget(self):
        plain = PanelSegment(QWidget())
        self.assertIsNone(plain.header)
        self.assertIsNone(plain.header_label)
        titled = PanelSegment(QWidget(), header="Details")
        self.assertEqual(titled.header.property(ROLE_PROPERTY), str(Role.SEGMENT_HEADER))
        self.assertEqual(titled.header_label.text(), "Details")
        flags = titled.header_label.textInteractionFlags()
        self.assertTrue(flags & Qt.TextInteractionFlag.TextSelectableByMouse)
        button = QPushButton("Go")
        custom = PanelSegment(QWidget(), header=button)
        self.assertIsNone(custom.header_label)
        self.assertIs(button.parentWidget(), custom.header)
        for segment in (plain, titled, custom):
            shiboken6.delete(segment)

    def test_the_header_strip_and_the_body_look(self):
        for theme in (LIGHT_THEME, DARK_THEME):
            manager = ThemeManager(self.app, theme)
            self.addCleanup(lambda m=manager: shiboken6.delete(m))
            self.build(body_lines=2, height=300)
            image = self.host.grab().toImage()
            header = self.details.header
            point = header.mapTo(self.host, header.rect().topRight()) + QPoint(-2, 2)
            self.assertEqual(image.pixelColor(point), QColor(theme.colors.header_bg), theme.name)
            self.assertTrue(self.details.header_label.font().bold())
            body = self.body.mapTo(self.host, self.body.rect().topRight()) + QPoint(-2, 2)
            below = QPoint(2, self.host.height() - 3)  # past the last segment: the host
            # The body has no box of its own: the panel's surface shows through.
            self.assertEqual(image.pixelColor(body), image.pixelColor(below), theme.name)
            shiboken6.delete(self.stack)
        shiboken6.delete(ThemeManager(self.app, LIGHT_THEME))


if __name__ == "__main__":
    unittest.main()
