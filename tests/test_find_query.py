import unittest

from rzgitclient.ui.panels.diff_rows import DiffRow, RowKind
from rzgitclient.ui.panels.find_query import FindMatch, FindQuery, find_matches, word_around

ROWS = (
    DiffRow(RowKind.HUNK, "@@ -1,3 +1,3 @@ def needle():"),
    DiffRow(RowKind.CONTEXT, "a needle, Needle, needles", 1, 1),
    DiffRow(RowKind.REMOVED, "needle_old", 2, None),
    DiffRow(RowKind.ADDED, "new needle", None, 2),
    DiffRow(RowKind.FILLER),
    DiffRow(RowKind.NOTE, "\\ No newline at end of file needle"),
    DiffRow(RowKind.FOLD, "⋯ 2 unchanged lines",
            hidden=(DiffRow(RowKind.CONTEXT, "x", 3, 3),
                    DiffRow(RowKind.CONTEXT, "the NEEDLE", 4, 4))),
)


def spans(query, rows=ROWS):
    return [(m.row, m.start, m.end, m.hidden) for m in find_matches(rows, query.pattern())]


class FindQueryTests(unittest.TestCase):
    """Find in a diff (RZG-74, spec §5.4): what matches, and where."""

    def test_plain_text_ignores_case_by_default(self):
        self.assertEqual(spans(FindQuery("needle")),
                         [(1, 2, 8, None), (1, 10, 16, None), (1, 18, 24, None),
                          (2, 0, 6, None), (3, 4, 10, None), (6, 4, 10, 1)])

    def test_match_case(self):
        self.assertEqual(spans(FindQuery("Needle", case=True)), [(1, 10, 16, None)])

    def test_whole_word(self):
        found = spans(FindQuery("needle", word=True))
        self.assertNotIn((1, 18, 24, None), found)  # "needles"
        self.assertNotIn((2, 0, 6, None), found)  # "needle_old": _ is a word character
        self.assertIn((1, 2, 8, None), found)
        self.assertIn((6, 4, 10, 1), found)

    def test_regex(self):
        self.assertEqual(spans(FindQuery(r"ne+dle_\w+", regex=True)), [(2, 0, 10, None)])
        # Without regex, the same text is literal.
        self.assertEqual(spans(FindQuery(r"ne+dle_\w+")), [])
        self.assertEqual(spans(FindQuery("a.needle")), [])
        self.assertEqual(spans(FindQuery("a.needle", regex=True)), [(1, 0, 8, None)])

    def test_whole_word_with_a_regex_alternation_applies_to_every_branch(self):
        rows = (DiffRow(RowKind.CONTEXT, "ab abc b", 1, 1),)
        self.assertEqual(spans(FindQuery("ab|b", word=True, regex=True), rows),
                         [(0, 0, 2, None), (0, 7, 8, None)])

    def test_an_invalid_regex_raises_value_error(self):
        with self.assertRaises(ValueError):
            FindQuery("(", regex=True).pattern()
        self.assertIsNotNone(FindQuery("(").pattern())  # literal: fine

    def test_nothing_to_find(self):
        self.assertIsNone(FindQuery("").pattern())
        self.assertEqual(find_matches(ROWS, None), [])

    def test_hunk_headers_filler_and_notes_are_not_searched(self):
        self.assertEqual(spans(FindQuery("def")), [])
        self.assertEqual(spans(FindQuery("newline")), [])
        self.assertEqual(spans(FindQuery("unchanged")), [])  # a fold row's own text

    def test_empty_matches_are_skipped(self):
        self.assertEqual(spans(FindQuery("x*", regex=True)), [(6, 0, 1, 0)])

    def test_a_match_never_spans_rows(self):
        self.assertEqual(spans(FindQuery(r"old\snew", regex=True)), [])

    def test_joined_and_row_by_row_find_the_same(self):
        for query in (FindQuery("needle"), FindQuery("Needle", case=True),
                      FindQuery("needle", word=True), FindQuery("e")):
            with self.subTest(query=query):
                pattern = query.pattern()
                self.assertTrue(query.joinable)
                self.assertEqual(find_matches(ROWS, pattern, joinable=True),
                                 find_matches(ROWS, pattern))
        self.assertFalse(FindQuery("x", regex=True).joinable)

    def test_a_regex_anchors_at_each_rows_ends(self):
        query = FindQuery("^needle|needle$", regex=True)
        self.assertEqual(spans(query), [(2, 0, 6, None), (3, 4, 10, None), (6, 4, 10, 1)])

    def test_the_limit(self):
        for joinable in (False, True):
            found = find_matches(ROWS, FindQuery("needle").pattern(), joinable, limit=2)
            self.assertEqual([(m.row, m.start) for m in found], [(1, 2), (1, 10)])

    def test_word_around_widens_a_match_to_its_whole_word(self):
        text = "#include <Commands/CommandQueue.h>"
        at = text.index("Command", 12)
        self.assertEqual(word_around(text, at, at + 7), (at, at + 12))  # CommandQueue
        q = text.index("ueue")
        self.assertEqual(word_around(text, q, q + 4), (at, at + 12))  # from the middle
        self.assertEqual(word_around(text, at - 1, at + 3), (at - 1, at + 12))  # "/Com"
        self.assertEqual(word_around(text, 0, 2), (0, 8))  # "#i": a word from its edge on
        self.assertEqual(word_around("a b", 1, 2), (1, 2))  # no word to widen to
        self.assertEqual(word_around("x_1é", 1, 2), (0, 4))  # _, digits and letters

    def test_order_puts_hidden_lines_in_their_place(self):
        a, b = FindMatch(6, 9, 10, 0), FindMatch(6, 0, 1, 1)
        self.assertLess(a.order, b.order)
        self.assertLess(FindMatch(5, 3, 4).order, a.order)


if __name__ == "__main__":
    unittest.main()
