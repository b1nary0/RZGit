import unittest

from rzgitclient.git.git_models import DiffLine, FileDiff, Hunk, LineKind
from rzgitclient.ui.panels.diff_rows import NO_NEWLINE, DiffRow, RowKind, split_rows, unified_rows

C, A, R = LineKind.CONTEXT, LineKind.ADDED, LineKind.REMOVED
K = RowKind
HEADER = "@@ -1,4 +1,4 @@"
DIFF = FileDiff("f", (Hunk(HEADER, 1, 1, (
    DiffLine(C, 1, 1, "same"),
    DiffLine(R, 2, None, "old 1"),
    DiffLine(R, 3, None, "old 2"),
    DiffLine(A, None, 2, "new 1"),
    DiffLine(C, 4, 3, "end"),
    DiffLine(A, None, 4, "tail", no_newline=True),
)),))


class UnifiedRowsTests(unittest.TestCase):
    def test_each_line_once_with_both_numbers(self):
        self.assertEqual(unified_rows(DIFF), (
            DiffRow(K.HUNK, HEADER),
            DiffRow(K.CONTEXT, "same", 1, 1),
            DiffRow(K.REMOVED, "old 1", 2, None),
            DiffRow(K.REMOVED, "old 2", 3, None),
            DiffRow(K.ADDED, "new 1", None, 2),
            DiffRow(K.CONTEXT, "end", 4, 3),
            DiffRow(K.ADDED, "tail", None, 4),
            DiffRow(K.NOTE, NO_NEWLINE),
        ))

    def test_characters_that_would_split_a_row_are_shown_as_symbols(self):
        diff = FileDiff("f", (Hunk(HEADER, 1, 1, (DiffLine(A, None, 1, "a\rb c\r"),)),))
        self.assertEqual(unified_rows(diff)[1].text, "a␍b¶c␍")

    def test_no_hunks_no_rows(self):
        self.assertEqual(unified_rows(FileDiff("f")), ())


class SplitRowsTests(unittest.TestCase):
    def test_removed_and_added_runs_pair_up_and_the_shorter_is_padded(self):
        left, right = split_rows(DIFF)
        self.assertEqual(len(left), len(right))
        self.assertEqual([(l.kind, l.text, r.kind, r.text) for l, r in zip(left, right)], [
            (K.HUNK, HEADER, K.HUNK, HEADER),
            (K.CONTEXT, "same", K.CONTEXT, "same"),
            (K.REMOVED, "old 1", K.ADDED, "new 1"),
            (K.REMOVED, "old 2", K.FILLER, ""),
            (K.CONTEXT, "end", K.CONTEXT, "end"),
            (K.FILLER, "", K.ADDED, "tail"),
            (K.FILLER, "", K.NOTE, NO_NEWLINE),
        ])

    def test_removed_after_added_starts_a_new_pair(self):
        diff = FileDiff("f", (Hunk(HEADER, 1, 1, (
            DiffLine(A, None, 1, "a"), DiffLine(R, 1, None, "r"), DiffLine(A, None, 2, "b"),
        )),))
        left, right = split_rows(diff)
        self.assertEqual([(l.text, r.text) for l, r in zip(left[1:], right[1:])],
                         [("", "a"), ("r", "b")])


if __name__ == "__main__":
    unittest.main()
