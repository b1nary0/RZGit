import unittest

from rzgit.core.symbol_at import find_symbol_near, symbol_at, utf16_col


class SymbolAtTests(unittest.TestCase):
    """The identifier at a column (spec §8, RZG-75)."""

    def test_in_or_next_to_an_identifier(self):
        text = "    total = add_one(x2)"
        self.assertEqual(symbol_at(text, 4), (4, "total"))  # at its start
        self.assertEqual(symbol_at(text, 9), (4, "total"))  # just after it
        self.assertEqual(symbol_at(text, 14), (12, "add_one"))
        self.assertEqual(symbol_at(text, 21), (20, "x2"))

    def test_none_off_an_identifier(self):
        self.assertIsNone(symbol_at("    x = 1", 1))  # indentation
        self.assertIsNone(symbol_at("n = 42", 5))  # digits alone aren't one
        self.assertIsNone(symbol_at("", 0))

    def test_a_unicode_identifier(self):
        self.assertEqual(symbol_at("größe = 1", 2), (0, "größe"))
        self.assertIsNone(symbol_at("9lives", 0))  # on the digit: "lives" starts after it
        self.assertEqual(symbol_at("9lives", 1), (1, "lives"))


class FindSymbolNearTests(unittest.TestCase):
    LINES = ["def helper():", "    return 1", "", "x = helper()", "helpers = []"]

    def test_on_the_line(self):
        self.assertEqual(find_symbol_near(self.LINES, 4, "helper"), (4, 5))

    def test_the_nearest_line_above_first(self):
        self.assertEqual(find_symbol_near(self.LINES, 2, "helper"), (1, 5))
        self.assertEqual(find_symbol_near(self.LINES, 3, "helper"), (4, 5))  # 2 above is farther
        self.assertEqual(find_symbol_near(["a = helper", "", "b = helper"], 2, "helper"), (1, 5))

    def test_a_whole_word_only(self):
        self.assertEqual(find_symbol_near(["helpers = helper"], 1, "helper"), (1, 11))
        self.assertIsNone(find_symbol_near(["helpers"], 1, "helper"))

    def test_within_the_radius(self):
        lines = ["target"] + ["pass"] * 30
        self.assertEqual(find_symbol_near(lines, 21, "target"), (1, 1))
        self.assertIsNone(find_symbol_near(lines, 22, "target"))
        self.assertIsNone(find_symbol_near(lines, None, "target"))
        self.assertIsNone(find_symbol_near(lines, 200, "target", radius=5))

    def test_the_column_is_vs_codes_utf16_one(self):
        self.assertEqual(utf16_col("😀 name", 2), 4)  # the emoji is two units
        self.assertEqual(find_symbol_near(["s = '😀'; name"], 1, "name"), (1, 11))


if __name__ == "__main__":
    unittest.main()
