import unittest

from rzgitclient.ui.panels.commit_message_text import reflow


class ReflowTests(unittest.TestCase):
    """A hard-wrapped body as paragraphs (RZG-37, spec §5.4)."""

    def test_a_wrapped_paragraph_is_one_line(self):
        body = ("The diff panel wrapped every line of a commit message again in a\n"
                "narrow sidebar, which made the body look ragged and hard to read.\n"
                "This joins them.")
        self.assertEqual(reflow(body), body.replace("\n", " "))

    def test_blank_lines_still_separate_paragraphs(self):
        body = ("First paragraph, long enough to be a wrapped line of prose, so it\n"
                "goes on.\n\nSecond paragraph.\n\n\nThird, after two blank lines.")
        self.assertEqual(reflow(body), (
            "First paragraph, long enough to be a wrapped line of prose, so it goes on."
            "\n\nSecond paragraph.\n\n\nThird, after two blank lines."))

    def test_list_items_keep_their_lines_and_their_wrapped_text_joins_them(self):
        body = ("Changes:\n"
                "- the first item, which is long enough that its author wrapped\n"
                "  it onto a second line\n"
                "* a second item\n"
                "+ a third\n"
                "• a fourth\n"
                "1. numbered, also wrapped by its author at about seventy-two\n"
                "   characters\n"
                "2) another style")
        self.assertEqual(reflow(body).split("\n"), [
            "Changes:",
            "- the first item, which is long enough that its author wrapped it onto a "
            "second line",
            "* a second item",
            "+ a third",
            "• a fourth",
            "1. numbered, also wrapped by its author at about seventy-two characters",
            "2) another style",
        ])

    def test_indented_lines_are_kept_exactly(self):
        body = ("Run it like this, which is the way the tests do it in the suite:\n"
                "    python -m unittest   discover\n"
                "\tTraceback (most recent call last):  \n"
                "and then the prose goes on, after the code block, on its own line.")
        self.assertEqual(reflow(body), body)

    def test_an_indented_line_after_a_list_item_but_not_under_its_text_is_kept(self):
        body = "- run the tool\n      $ tool --flag\n- done"
        self.assertEqual(reflow(body), body)

    def test_trailers_keep_their_lines(self):
        body = ("Fix the thing that was broken, at last, after a long time of it\n"
                "being broken.\n\n"
                "Fixes: #12\n"
                "Signed-off-by: A Person <a@example.com>\n"
                "Co-Authored-By: Claude <noreply@anthropic.com>")
        self.assertEqual(reflow(body).split("\n")[2:], [
            "Fixes: #12", "Signed-off-by: A Person <a@example.com>",
            "Co-Authored-By: Claude <noreply@anthropic.com>"])

    def test_a_word_and_a_colon_in_prose_is_not_a_trailer(self):
        """As in a083324f: a wrapped line that starts "thread: positions only"."""
        body = ("Each mesh geometry also gets depth buffers, built on the load worker\n"
                "thread: positions only (12 B a vertex), merged where their float bits\n"
                "are equal.\n\nNote: a paragraph of one trailer-like line is kept.")
        self.assertEqual(reflow(body), (
            "Each mesh geometry also gets depth buffers, built on the load worker thread: "
            "positions only (12 B a vertex), merged where their float bits are equal."
            "\n\nNote: a paragraph of one trailer-like line is kept."))

    def test_long_trailers_keep_their_lines_too(self):
        body = ("Reviewed-by: Someone With A Rather Long Name <someone@example.com>\n"
                "Co-Authored-By: Someone Else Entirely <someone.else@example.com>")
        self.assertEqual(reflow(body), body)

    def test_a_list_item_ends_at_the_next_line_that_isn_t_its_text(self):
        body = "- an item\nThen a prose line that follows the list.\n  indented code"
        self.assertEqual(reflow(body), body)

    def test_a_short_line_then_a_capitalised_one_keeps_its_break(self):
        body = ("Why:\nThe old code read the file twice, once to count and once to\n"
                "parse, which was slow.\nShort headline\nNext sentence starts here.")
        self.assertEqual(reflow(body).split("\n"), [
            "Why:",
            "The old code read the file twice, once to count and once to parse, which "
            "was slow.",
            "Short headline",
            "Next sentence starts here.",
        ])

    def test_a_short_line_then_a_lowercase_one_joins(self):
        self.assertEqual(reflow("Short line\ncontinues here"), "Short line continues here")

    def test_crlf_and_trailing_spaces(self):
        body = ("A line of prose long enough to be wrapped by its author here   \r\n"
                "and its end.\r\n")
        self.assertEqual(reflow(body),
                         "A line of prose long enough to be wrapped by its author here "
                         "and its end.\n")

    def test_nothing_to_do(self):
        for body in ("", "One line.", "One\n\nTwo"):
            self.assertEqual(reflow(body), body)


if __name__ == "__main__":
    unittest.main()
