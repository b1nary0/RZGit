import unittest
from datetime import datetime, timezone

from rzgitclient.core.ref_labels import RefKind, RefLabel, labels_by_commit, row_tooltip
from rzgitclient.git.git_models import Ref, Stash

DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stash(index: int, base: str, message: str = "wip") -> Stash:
    return Stash(f"s{index}", "A", DATE, message, index, "main", message, False,
                 parents=(base, f"i{index}"))


def texts(labels) -> list[str]:
    return [label.text for label in labels]


class RefLabelsTests(unittest.TestCase):
    """History's ref labels (RZG-77, spec §5.4)."""

    def test_no_refs_no_labels(self):
        self.assertEqual(labels_by_commit([], "main", None), {})

    def test_kinds_and_their_order_on_one_commit(self):
        refs = [Ref("refs/tags/v2", "c"), Ref("refs/tags/v1", "c"),
                Ref("refs/remotes/origin/zeta", "c"), Ref("refs/heads/alpha", "c"),
                Ref("refs/heads/main", "c")]
        labels = labels_by_commit(refs, "main", "c", [stash(1, "c"), stash(0, "c")])["c"]
        self.assertEqual(texts(labels),
                         ["main", "alpha", "origin/zeta", "v1", "v2", "stash@{0}", "stash@{1}"])
        self.assertEqual([l.kind for l in labels],
                         [RefKind.BRANCH, RefKind.BRANCH, RefKind.REMOTE, RefKind.TAG,
                          RefKind.TAG, RefKind.STASH, RefKind.STASH])
        self.assertEqual([l.current for l in labels], [True] + [False] * 6)

    def test_a_branch_and_its_remote_on_one_commit_are_one_label(self):
        refs = [Ref("refs/heads/main", "c"), Ref("refs/remotes/origin/main", "c"),
                Ref("refs/remotes/fork/main", "c"), Ref("refs/remotes/origin/other", "c")]
        labels = labels_by_commit(refs, "main", "c")["c"]
        self.assertEqual(texts(labels), ["main ⇄ fork, origin", "origin/other"])
        self.assertEqual(labels[0].names, ("main", "fork/main", "origin/main"))

    def test_a_remote_elsewhere_is_its_own_label(self):
        refs = [Ref("refs/heads/main", "c"), Ref("refs/remotes/origin/main", "b")]
        labels = labels_by_commit(refs, "main", "c")
        self.assertEqual(texts(labels["c"]), ["main"])
        self.assertEqual(texts(labels["b"]), ["origin/main"])
        self.assertEqual(labels["b"][0].kind, RefKind.REMOTE)

    def test_a_remote_name_with_slashes_and_the_remote_head(self):
        refs = [Ref("refs/heads/feature/x", "c"), Ref("refs/remotes/origin/feature/x", "c"),
                Ref("refs/remotes/origin/HEAD", "c")]
        self.assertEqual(texts(labels_by_commit(refs, None, "z")["c"]), ["feature/x ⇄ origin"])

    def test_only_the_remote_head_gives_no_label(self):
        self.assertEqual(labels_by_commit([Ref("refs/remotes/origin/HEAD", "c")], "main", "x"), {})

    def test_a_detached_head_has_its_own_label_first(self):
        refs = [Ref("refs/heads/main", "c")]
        labels = labels_by_commit(refs, None, "c")["c"]
        self.assertEqual(texts(labels), ["HEAD", "main"])
        self.assertEqual(labels[0].kind, RefKind.HEAD)
        self.assertTrue(labels[0].current)
        self.assertFalse(labels[1].current)
        self.assertEqual(texts(labels_by_commit([], None, "d")["d"]), ["HEAD"])

    def test_on_a_branch_there_is_no_head_label(self):
        self.assertEqual(labels_by_commit([], "main", "c"), {})

    def test_a_stash_is_on_the_commit_it_was_made_on(self):
        labels = labels_by_commit([], "main", "c", [stash(0, "b", "half done")])
        self.assertEqual(list(labels), ["b"])
        self.assertEqual(labels["b"][0].message, "half done")

    def test_tooltips(self):
        refs = [Ref("refs/heads/main", "c"), Ref("refs/remotes/origin/main", "c"),
                Ref("refs/remotes/origin/b", "c"), Ref("refs/tags/v1.0", "c")]
        labels = labels_by_commit(refs, "main", "c", [stash(0, "c", "half done")])["c"]
        self.assertEqual(row_tooltip(labels), "\n".join((
            "Branch main (checked out)", "Remote branch origin/main", "Remote branch origin/b",
            "Tag v1.0", "Stash stash@{0}: half done")))
        self.assertEqual(RefLabel(RefKind.HEAD, "HEAD", ("HEAD",), True).tooltip,
                         "HEAD (detached)")
        self.assertEqual(RefLabel(RefKind.BRANCH, "x", ("x",)).tooltip, "Branch x")


if __name__ == "__main__":
    unittest.main()
