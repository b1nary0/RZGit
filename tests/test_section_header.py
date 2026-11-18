import unittest

from rzgit.ui.widgets.section_header import SectionHeader
from tests.support import qt_app


class SectionHeaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_upper_case_and_role(self):
        header = SectionHeader("Working tree")
        self.assertEqual(header.text(), "WORKING TREE")
        self.assertEqual(header.property("role"), "section-header")

    def test_set_title(self):
        header = SectionHeader()
        header.set_title("Files (3)")
        self.assertEqual(header.text(), "FILES (3)")


if __name__ == "__main__":
    unittest.main()
