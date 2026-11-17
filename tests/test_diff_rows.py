import unittest

from rzgitclient.git.git_models import DiffLine, FileDiff, Hunk, LineKind
from rzgitclient.ui.panels.diff_rows import (
    NO_NEWLINE,
    WORD_MAX_LENGTH,
    DiffRow,
    RowKind,
    split_partners,
    split_rows,
    unified_partners,
    unified_rows,
    whole_file,
    word_spans,
)

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

    def test_rows_know_their_hunk_and_line(self):
        rows = unified_rows(DIFF)
        self.assertEqual([(r.hunk, r.line) for r in rows],
                         [(0, None), (0, 0), (0, 1), (0, 2), (0, 3), (0, 4), (0, 5), (None, None)])
        left, right = split_rows(DIFF)
        self.assertEqual([r.line for r in left if r.kind is K.REMOVED], [1, 2])
        self.assertEqual([r.line for r in right if r.kind is K.ADDED], [3, 5])

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


class WholeFileTests(unittest.TestCase):
    """RZG-61: a whole added or deleted file shows unified (spec §5.4)."""

    ADDED = FileDiff("n", (Hunk("@@ -0,0 +1,2 @@", 0, 1, (
        DiffLine(A, None, 1, "a"), DiffLine(A, None, 2, "b"))),))
    DELETED = FileDiff("d", (Hunk("@@ -1,2 +0,0 @@", 1, 0, (
        DiffLine(R, 1, None, "a"), DiffLine(R, 2, None, "b"))),))

    def test_a_whole_added_or_deleted_file(self):
        self.assertEqual(whole_file(self.ADDED), "added")
        self.assertEqual(whole_file(self.DELETED), "deleted")

    def test_lines_appended_to_a_file_are_not_a_whole_file(self):
        appended = FileDiff("f", (Hunk("@@ -2,0 +3,1 @@", 2, 3, (DiffLine(A, None, 3, "c"),)),))
        self.assertIsNone(whole_file(appended))
        cut = FileDiff("f", (Hunk("@@ -3,1 +2,0 @@", 3, 2, (DiffLine(R, 3, None, "c"),)),))
        self.assertIsNone(whole_file(cut))

    def test_a_diff_with_both_sides_or_no_hunks(self):
        self.assertIsNone(whole_file(DIFF))
        self.assertIsNone(whole_file(FileDiff("e")))
        mixed = FileDiff("m", (Hunk("@@ -0,0 +1,2 @@", 0, 1, (
            DiffLine(A, None, 1, "a"), DiffLine(R, 1, None, "b"))),))
        self.assertIsNone(whole_file(mixed))


if __name__ == "__main__":
    unittest.main()


class WordSpanTests(unittest.TestCase):
    """Changed words of a removed and added pair (RZG-22, spec §5.4)."""

    def test_only_the_changed_word_is_a_span(self):
        self.assertEqual(word_spans("count = total + 1", "count = total + 2"),
                         (((16, 17),), ((16, 17),)))

    def test_tokens_are_words_spaces_and_punctuation_runs(self):
        old, new = word_spans("call(a, b);", "call(a, c);")
        self.assertEqual((old, new), (((8, 9),), ((8, 9),)))
        old, new = word_spans("x = foo_bar", "x = foo_baz")
        self.assertEqual(old, ((4, 11),))  # a whole word, not just its last letter
        old, new = word_spans("a += b", "a -= b")
        self.assertEqual(old, ((2, 4),))  # a run of punctuation is one token

    def test_an_insertion_has_a_span_on_the_new_side_only(self):
        old, new = word_spans("f(a, b)", "f(a, x, b)")
        self.assertEqual(old, ())
        self.assertEqual(new, ((5, 8),))  # "x, "

    def test_changed_tokens_in_a_row_are_one_span(self):
        old, new = word_spans("one two-three four five", "one 2+3 four five")
        self.assertEqual(old, ((4, 13),))
        self.assertEqual(new, ((4, 7),))
        old, new = word_spans("one two three four five", "one 2 3 four five")
        self.assertEqual(old, ((4, 7), (8, 13)))  # the unchanged space between splits them

    def test_identical_lines_have_no_spans(self):
        self.assertEqual(word_spans("same", "same"), ((), ()))

    def test_lines_with_little_in_common_are_not_compared(self):
        self.assertIsNone(word_spans("return value;", "if (ready) start();"))

    def test_long_lines_are_not_compared(self):
        long = "x " * (WORD_MAX_LENGTH // 2 + 1)
        self.assertIsNone(word_spans(long, long + "y"))
        self.assertIsNotNone(word_spans("x " * 10, "x " * 10 + "y"))


class PartnerTests(unittest.TestCase):
    """Which changed rows pair up, in both views (RZG-22)."""

    def test_unified_pairs_runs_like_side_by_side(self):
        rows = unified_rows(DIFF)
        # rows: 2 "old 1", 3 "old 2" removed; 4 "new 1" added; 6 "tail" added after context
        self.assertEqual(unified_partners(rows), {2: "new 1", 4: "old 1"})

    def test_unified_agrees_with_side_by_side(self):
        diff = FileDiff("f", (Hunk("@@", 1, 1, (
            DiffLine(R, 1, None, "a1"), DiffLine(R, 2, None, "a2"),
            DiffLine(A, None, 1, "b1"), DiffLine(A, None, 2, "b2"), DiffLine(A, None, 3, "b3"),
            DiffLine(R, 3, None, "c1"), DiffLine(A, None, 4, "d1"),
        )),))
        unified = unified_rows(diff)
        pairs = {unified[n].text: text for n, text in unified_partners(unified).items()}
        left, right = split_rows(diff)
        old, new = split_partners(left, right)
        split = {left[n].text: t for n, t in old.items()} | {right[n].text: t for n, t in new.items()}
        self.assertEqual(pairs, split)
        self.assertEqual(pairs, {"a1": "b1", "b1": "a1", "a2": "b2", "b2": "a2",
                                 "c1": "d1", "d1": "c1"})

    def test_a_no_newline_note_does_not_break_a_run(self):
        diff = FileDiff("f", (Hunk("@@", 1, 1, (
            DiffLine(R, 1, None, "end", no_newline=True), DiffLine(A, None, 1, "end;"),
        )),))
        rows = unified_rows(diff)
        self.assertEqual(unified_partners(rows), {1: "end;", 3: "end"})

    def test_side_by_side_pairs_only_removed_with_added(self):
        left, right = split_rows(DIFF)
        old, new = split_partners(left, right)
        self.assertEqual({left[n].text for n in old}, {"old 1"})
        self.assertEqual(set(new.values()), {"old 1"})
