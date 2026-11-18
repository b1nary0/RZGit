import unittest

from rzgit.ui.panels.panel import Panel
from rzgit.ui.panels.panel_registry import PANEL_TYPES, index_panel_types, panel_type
from tests.support import qt_app


class _First(Panel):
    panel_id = "first"
    title = "First"


class _Second(Panel):
    panel_id = "second"
    title = "Second"


class _AlsoFirst(Panel):
    panel_id = "first"
    title = "Also first"


class PanelRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        qt_app()

    def test_registered_ids_are_unique(self):
        ids = [t.panel_id for t in PANEL_TYPES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_registered_type_is_found_by_its_id(self):
        for t in PANEL_TYPES:
            with self.subTest(t.panel_id):
                self.assertIs(panel_type(t.panel_id), t)

    def test_unknown_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            panel_type("no-such-panel")

    def test_index_maps_ids_to_types(self):
        index = index_panel_types((_First, _Second))
        self.assertEqual(dict(index), {"first": _First, "second": _Second})
        with self.assertRaises(TypeError):
            index["third"] = _First  # read-only

    def test_index_rejects_duplicate_ids(self):
        with self.assertRaisesRegex(ValueError, "_First and _AlsoFirst"):
            index_panel_types((_First, _AlsoFirst))


if __name__ == "__main__":
    unittest.main()
