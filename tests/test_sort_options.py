import unittest

from rzgitclient.core.sort_options import SortOption, SortOptions, SortOrder

WORDS = ("pear", "Apple", "fig", "apple")
SORTS = SortOptions((
    SortOption("given", "Given"),
    SortOption("name", "Name", str.casefold, column="Name"),
    SortOption("length", "Length", len, descending=True),
))


class SortOptionsTests(unittest.TestCase):
    """A panel's sort orders, plain Python (spec §5.1, RZG-43)."""

    def test_the_natural_order_is_the_given_order_and_reversed_reverses_it(self):
        self.assertEqual(SORTS.apply(WORDS, SortOrder("given")), list(WORDS))
        self.assertEqual(SORTS.apply(WORDS, SortOrder("given", True)), list(WORDS[::-1]))

    def test_a_key_sorts_and_ties_keep_the_natural_order(self):
        self.assertEqual(SORTS.apply(WORDS, SortOrder("name")), ["Apple", "apple", "fig", "pear"])
        # Reversed, the ties still keep the given order (a stable sort).
        self.assertEqual(SORTS.apply(WORDS, SortOrder("name", True)),
                         ["pear", "fig", "Apple", "apple"])

    def test_an_option_can_default_to_descending(self):
        self.assertEqual(SORTS.apply(WORDS, SortOrder("length")), ["Apple", "apple", "pear", "fig"])
        self.assertEqual(SORTS.apply(WORDS, SortOrder("length", True)),
                         ["fig", "pear", "Apple", "apple"])
        self.assertTrue(SORTS.descending(SortOrder("length")))
        self.assertFalse(SORTS.descending(SortOrder("length", True)))
        self.assertTrue(SORTS.descending(SortOrder("name", True)))

    def test_first_rows_stay_at_the_top_in_every_order(self):
        sorts = SortOptions(SORTS.options, first=lambda w: w == "pear")
        for order in (SortOrder("given"), SortOrder("given", True), SortOrder("name"),
                      SortOrder("name", True), SortOrder("length", True)):
            self.assertEqual(sorts.apply(WORDS, order)[0], "pear", order)

    def test_of_reads_the_value_from_a_wrapped_item(self):
        rows = [(w, i) for i, w in enumerate(WORDS)]
        self.assertEqual([i for _, i in SORTS.apply(rows, SortOrder("name"), of=lambda r: r[0])],
                         [1, 3, 2, 0])

    def test_an_unknown_key_is_the_natural_order(self):
        self.assertEqual(SORTS.option("gone").key, "given")
        self.assertEqual(SORTS.apply(WORDS, SortOrder("gone")), list(WORDS))
        self.assertEqual(SORTS.resolve(SortOrder("gone", True)), SortOrder("given"))
        self.assertEqual(SORTS.resolve(None), SortOrder("given"))
        self.assertEqual(SORTS.resolve(SortOrder("name", True)), SortOrder("name", True))

    def test_options_by_column(self):
        self.assertEqual(SORTS.for_column("Name").key, "name")
        self.assertIsNone(SORTS.for_column("Length"))

    def test_the_first_option_must_be_the_natural_order_and_keys_unique(self):
        with self.assertRaises(ValueError):
            SortOptions((SortOption("name", "Name", str.casefold),))
        with self.assertRaises(ValueError):
            SortOptions(())
        with self.assertRaises(ValueError):
            SortOptions((SortOption("a", "A"), SortOption("a", "B", len)))


if __name__ == "__main__":
    unittest.main()
