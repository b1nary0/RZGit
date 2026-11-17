import unittest
from datetime import datetime, timezone

from rzgitclient.core.commit_graph import CommitGraph, GraphRow
from rzgitclient.git.git_models import Commit

DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def commits(spec: str) -> list[Commit]:
    """``"c:b a b:a a:"``: each commit, newest first, with its parents after the colon."""
    out = []
    for item in spec.split():
        sha, _, parents = item.partition(":")
        out.append(Commit(sha, "A", DATE, sha, parents=tuple(p for p in parents.split(",") if p)))
    return out


def rows(graph: CommitGraph) -> list[GraphRow]:
    return [graph.row(i) for i in range(len(graph))]


class CommitGraphTests(unittest.TestCase):
    def test_a_straight_history_is_one_lane(self):
        graph = CommitGraph(commits("c:b b:a a:"))
        c, b, a = rows(graph)
        self.assertEqual([r.lane for r in (c, b, a)], [0, 0, 0])
        self.assertEqual(c.upper, ())  # the tip: nothing above it
        self.assertEqual(c.lower, ((0, 0, c.color),))
        self.assertEqual(b.upper, ((0, 0, c.color),))
        self.assertEqual(a.lower, ())  # the first commit: its lane ends
        self.assertEqual({r.color for r in (c, b, a)}, {c.color})
        self.assertEqual([r.width for r in (c, b, a)], [1, 1, 1])

    def test_a_merge_opens_a_lane_for_its_other_parent_and_it_joins_at_the_fork(self):
        # m merges x (a side branch off a) into b.
        graph = CommitGraph(commits("m:b,x b:a x:a a:"))
        m, b, x, a = rows(graph)
        self.assertEqual((m.lane, b.lane, x.lane, a.lane), (0, 0, 1, 0))
        side = m.lower[1][2]
        self.assertEqual(m.lower, ((0, 0, m.color), (0, 1, side)))  # the merge line
        self.assertNotEqual(side, m.color)
        self.assertEqual(b.lower, ((0, 0, m.color), (1, 1, side)))  # x's lane passes by
        self.assertEqual(x.color, side)
        self.assertEqual(x.lower, ((1, 0, m.color), (0, 0, m.color)))  # the fork: x's lane joins a's
        self.assertEqual(a.upper, ((0, 0, m.color),))
        self.assertEqual(m.width, 2)
        self.assertEqual(a.width, 1)

    def test_a_lane_right_of_a_fork_joins_the_left_one(self):
        # Tips t (lane 0) and u (lane 1) share the parent p.
        graph = CommitGraph(commits("t:p u:p p:"))
        t, u, p = rows(graph)
        self.assertEqual((t.lane, u.lane), (0, 1))
        self.assertEqual(u.lower, ((1, 0, t.color), (0, 0, t.color)))  # joins the lane waiting for p
        self.assertEqual(p.lane, 0)

    def test_the_left_lane_is_kept_when_the_right_one_waits_first(self):
        # u (lane 1) waits for p; then t, in lane 0, has p as its first parent: lane 1
        # joins lane 0, and lane 0 carries on with t's colour.
        graph = CommitGraph(commits("s:t,u u:p t:p p:"))
        s, u, t, p = rows(graph)
        self.assertEqual((s.lane, u.lane, t.lane, p.lane), (0, 1, 0, 0))
        self.assertEqual(t.lower, ((0, 0, s.color), (1, 0, u.color)))
        self.assertEqual(p.upper, ((0, 0, s.color),))

    def test_an_octopus_merge_opens_a_lane_per_other_parent(self):
        graph = CommitGraph(commits("m:a,b,c a: b: c:"))
        m = graph.row(0)
        self.assertEqual([line[:2] for line in m.lower], [(0, 0), (0, 1), (0, 2)])
        self.assertEqual(len({line[2] for line in m.lower}), 3)
        self.assertEqual(m.width, 3)
        self.assertEqual([graph.row(i).lane for i in (1, 2, 3)], [0, 1, 2])

    def test_a_merge_into_a_lane_already_waiting_joins_it(self):
        # x waits in lane 1 (from m1); m2 merges x too: no third lane.
        graph = CommitGraph(commits("m1:m2,x m2:a,x x:a a:"))
        m1, m2, *_ = rows(graph)
        self.assertEqual(m2.lower[:2], ((0, 0, m1.color), (0, 1, m1.lower[1][2])))
        self.assertEqual(max(r.width for r in rows(graph)), 2)

    def test_a_free_lane_is_reused_and_lanes_never_move(self):
        # Two side branches one after the other: the second takes lane 1 again.
        graph = CommitGraph(commits("m2:m1,y y:m1 m1:a,x x:a a:"))
        lanes = [r.lane for r in rows(graph)]
        self.assertEqual(lanes, [0, 1, 0, 1, 0])

    def test_a_new_lane_takes_a_colour_no_open_lane_has(self):
        graph = CommitGraph(commits("m:a,b,c,d a: b: c: d:"), colors=4)
        self.assertEqual(sorted(line[2] for line in graph.row(0).lower), [0, 1, 2, 3])

    def test_a_new_lane_skips_colours_still_open_even_far_away(self):
        # Four colours, used by m's lanes; d's and c's lanes close. When a's merge opens
        # lane 2, the next in turn (0) is lane 0's: not a neighbour, but still open.
        graph = CommitGraph(commits("m:a,b,c,d d: c: a:z,y b: y: z:"), colors=4)
        a = graph.row(3)
        self.assertEqual(a.color, 0)
        self.assertIn((0, 2, 2), a.lower)

    def test_a_lane_keeps_its_colour_down_the_column(self):
        graph = CommitGraph(commits("m:b,x b:c x:y y:c c:"))
        m, b, x, y, c = rows(graph)
        side = x.color
        self.assertEqual(y.color, side)
        self.assertTrue(all(line[2] == m.color for line in b.lower if line[0] == 0))
        self.assertIn((1, 1, side), b.lower)

    def test_a_parent_named_twice_is_one_line(self):
        graph = CommitGraph(commits("m:a,a a:"))
        self.assertEqual(graph.row(0).lower, ((0, 0, graph.row(0).color),))

    def test_rows_are_worked_out_when_first_asked_for(self):
        graph = CommitGraph(commits("d:c c:b b:a a:"))
        self.assertEqual(graph.rows_built, 0)
        graph.row(1)
        self.assertEqual(graph.rows_built, 2)
        with self.assertRaises(IndexError):
            graph.row(4)

    def test_a_page_carries_on_where_the_last_ended(self):
        spec = commits("m:b,x b:c x:c c:d,y d:e y:e e:")
        whole = rows(CommitGraph(spec))
        for cut in range(1, len(spec)):
            paged = CommitGraph(spec[:cut])
            first = rows(paged)  # the first page, drawn
            paged.extend(spec[cut:])
            self.assertEqual(rows(paged), whole, cut)
            self.assertTrue(all(a is b for a, b in zip(first, rows(paged))), cut)

    def test_a_tip_below_another_branch_opens_a_lane(self):
        # Two unrelated histories (a tip found further down: --date-order, a second root).
        graph = CommitGraph(commits("a: z:"))
        a, z = rows(graph)
        self.assertEqual((a.lane, z.lane), (0, 0))  # a's lane ended: z reuses it
        self.assertEqual(z.upper, ())
        self.assertEqual(z.width, 1)  # a lone commit: no lines at all


if __name__ == "__main__":
    unittest.main()
