import unittest

from rzgit.core.sort_options import GroupOption, SortOption, SortOptions, SortOrder

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
        sorts = SortOptions(SORTS.options, first=(lambda w: w == "pear",))
        for order in (SortOrder("given"), SortOrder("given", True), SortOrder("name"),
                      SortOrder("name", True), SortOrder("length", True)):
            self.assertEqual(sorts.apply(WORDS, order)[0], "pear", order)

    def test_first_pins_by_its_tests_in_order_each_row_once(self):
        """RZG-99: the first test's rows, then the second's, then the rest."""
        sorts = SortOptions(SORTS.options, first=(lambda w: w == "fig", str.istitle))
        self.assertEqual(sorts.apply(WORDS, SortOrder("name")), ["fig", "Apple", "apple", "pear"])
        self.assertEqual(sorts.apply(WORDS, SortOrder("name", True)),
                         ["fig", "Apple", "pear", "apple"])
        both = SortOptions(SORTS.options, first=(str.istitle, lambda w: w.startswith("A")))
        self.assertEqual(both.apply(WORDS, SortOrder("given")), ["Apple", "pear", "fig", "apple"])
        self.assertEqual(both.top(WORDS, SortOrder("given")), ["Apple"])
        self.assertEqual(SORTS.top(WORDS, SortOrder("given")), [])

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

    def test_another_option_can_be_the_default(self):
        """RZG-50: the default needn't be the natural order; unknown keys read as it."""
        sorts = SortOptions(SORTS.options, default_key="length")
        self.assertEqual(sorts.natural.key, "given")
        self.assertEqual(sorts.default.key, "length")
        self.assertEqual(sorts.resolve(None), SortOrder("length"))
        self.assertEqual(sorts.resolve(SortOrder("gone", True)), SortOrder("length"))
        self.assertEqual(sorts.option("gone").key, "length")
        self.assertEqual(sorts.apply(WORDS, SortOrder("gone")), ["Apple", "apple", "pear", "fig"])
        self.assertEqual(sorts.resolve(SortOrder("given")), SortOrder("given"))
        self.assertEqual(SORTS.default.key, "given")
        with self.assertRaises(ValueError):
            SortOptions(SORTS.options, default_key="gone")

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


FRUIT = ("pear", "Apple", "fig", "apple", "plum", "kiwi")
GROUPED = SortOptions(SORTS.options, groups=(
    GroupOption("initial", "Initial", lambda w: w[0].upper()),
    GroupOption("size", "Size", lambda w: "long" if len(w) > 4 else "short" if len(w) > 3 else "tiny",
                order=("short", "long")),
    GroupOption("first", "First", lambda w: w[0], last=("a",)),
))


class GroupingTests(unittest.TestCase):
    """A panel's groupings, plain Python (spec §5.1, RZG-53)."""

    def groups(self, order):
        return [(g.name, g.items) for g in GROUPED.grouped(FRUIT, order)]

    def test_not_grouped_is_none(self):
        self.assertIsNone(GROUPED.grouped(FRUIT, SortOrder("given")))
        self.assertIsNone(SORTS.grouped(FRUIT, SortOrder("given", group="initial")))

    def test_groups_by_name_ignoring_case_and_the_items_keep_the_sort(self):
        self.assertEqual(self.groups(SortOrder("given", group="initial")), [
            ("A", ["Apple", "apple"]), ("F", ["fig"]), ("K", ["kiwi"]), ("P", ["pear", "plum"])])
        self.assertEqual(self.groups(SortOrder("length", group="initial")), [
            ("A", ["Apple", "apple"]), ("F", ["fig"]), ("K", ["kiwi"]), ("P", ["pear", "plum"])])
        # Reversed reverses inside the groups only.
        self.assertEqual(self.groups(SortOrder("given", True, group="initial")), [
            ("A", ["apple", "Apple"]), ("F", ["fig"]), ("K", ["kiwi"]), ("P", ["plum", "pear"])])
        named = SortOptions(SORTS.options, groups=(
            GroupOption("raw", "Raw", lambda w: w[0]),))
        self.assertEqual([g.name for g in named.grouped(FRUIT, SortOrder("given", group="raw"))],
                         ["A", "a", "f", "k", "p"])  # ignoring case, then as written

    def test_listed_groups_first_in_their_order_then_others(self):
        self.assertEqual(self.groups(SortOrder("name", group="size")), [
            ("short", ["kiwi", "pear", "plum"]), ("long", ["Apple", "apple"]), ("tiny", ["fig"])])

    def test_last_groups_after_all_others_and_empty_groups_are_left_out(self):
        self.assertEqual([n for n, _ in self.groups(SortOrder("given", group="first"))],
                         ["A", "f", "k", "p", "a"])
        options = SortOptions(SORTS.options, groups=(
            GroupOption("g", "G", lambda w: "Another" if w[0] in "pk" else "Fruit",
                        order=("Gone",), last=("Another", "Never")),))
        self.assertEqual([g.name for g in options.grouped(FRUIT, SortOrder("given", group="g"))],
                         ["Fruit", "Another"])

    def test_the_group_knows_its_grouping(self):
        group = GROUPED.grouped(FRUIT, SortOrder("given", group="size"))[0]
        self.assertEqual((group.key, group.name), ("size", "short"))

    def test_first_rows_stand_above_the_groups_in_none(self):
        """RZG-99: ``top`` gives them; a group left empty by them isn't there."""
        options = SortOptions(SORTS.options, first=(lambda w: w == "plum",), groups=GROUPED.groups)
        order = SortOrder("name", group="initial")
        groups = options.grouped(FRUIT, order)
        self.assertEqual(groups[-1].items, ["pear"])
        self.assertNotIn("plum", [w for g in groups for w in g.items])
        self.assertEqual(options.top(FRUIT, order), ["plum"])
        alone = SortOptions(SORTS.options, first=(lambda w: w.startswith("f"),),
                            groups=GROUPED.groups)
        self.assertNotIn("f", [g.name for g in alone.grouped(FRUIT, order)])

    def test_of_reads_the_value_for_the_group_too(self):
        wrapped = [(w, i) for i, w in enumerate(FRUIT)]
        groups = GROUPED.grouped(wrapped, SortOrder("given", group="initial"), of=lambda p: p[0])
        self.assertEqual(groups[0].items, [("Apple", 1), ("apple", 3)])

    def test_resolve_drops_an_unknown_grouping_and_keeps_a_known_one(self):
        self.assertEqual(GROUPED.resolve(SortOrder("name", True, "nope")), SortOrder("name", True))
        self.assertEqual(GROUPED.resolve(SortOrder("nope", True, "size")), SortOrder("given", group="size"))
        order = SortOrder("name", True, "size")
        self.assertIs(GROUPED.resolve(order), order)
        self.assertEqual(SORTS.resolve(SortOrder("name", group="size")), SortOrder("name"))
        self.assertIsNone(GROUPED.grouping(None))
        self.assertEqual(GROUPED.grouping("size").label, "Size")

    def test_group_keys_must_differ(self):
        with self.assertRaises(ValueError):
            SortOptions(SORTS.options, groups=(GroupOption("a", "A", str), GroupOption("a", "B", str)))


if __name__ == "__main__":
    unittest.main()
