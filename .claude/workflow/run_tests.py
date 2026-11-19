#!/usr/bin/env python3
"""Run RZGit's unittest suite N times, reporting progress for the lane's tab.

From the worktree root, with the lane's own src first:
    PYTHONPATH=src /home/JPOD/dev/RZGit/.venv/bin/python /home/JPOD/dev/RZGit/.claude/workflow/run_tests.py --runs 5 [-v] [TEST ...]

Each run loads the tests afresh and runs them: the whole suite (`-s tests -t .`), or only the
TESTs named, as unittest names them (`tests.test_diff_panel`, `tests.test_diff_panel.DiffPanelTest`,
or one test method), for example for a mutation check. Progress (tests finished out of the total,
across all runs, and the run it's on) goes through the lanes skill's test_progress.py, which the
lane watch shows on the lane's tab; outside a lane it just runs the tests. Exit code 0 only if
every run passed.

Per repository (RZGit), never synced. RZE's runner has the same interface.
"""
import argparse
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "lanes"))
from test_progress import Progress  # noqa: E402


def result_class(progress):
    class ProgressResult(unittest.TextTestResult):
        def stopTest(self, test):
            super().stopTest(test)
            progress.step()
    return ProgressResult


def load(names):
    """The whole suite, or only the named tests."""
    loader = unittest.defaultTestLoader
    return loader.loadTestsFromNames(names) if names else loader.discover("tests", top_level_dir=".")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=1, help="how many times to run the tests")
    parser.add_argument("-v", "--verbose", action="store_true", help="name each test as it runs")
    parser.add_argument("tests", nargs="*", metavar="TEST",
                        help="only these tests (unittest names); the whole suite without any")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    # Run as a script, the worktree root isn't on sys.path (python -m unittest adds it), and named
    # tests import from it (tests.test_x). After PYTHONPATH's src, so the lane's own src still wins.
    if os.getcwd() not in sys.path:
        sys.path.insert(1, os.getcwd())
    per_run = load(args.tests).countTestCases()
    failed = []
    with Progress(total=per_run * args.runs, runs=args.runs) as progress:
        for run in range(1, args.runs + 1):
            progress.start_run(run)
            print(f"=== run {run} of {args.runs} ({per_run} tests)", flush=True)
            runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1,
                                             resultclass=result_class(progress))
            if not runner.run(load(args.tests)).wasSuccessful():
                failed.append(run)
    print(f"=== {args.runs - len(failed)} of {args.runs} runs passed"
          + (f"; failed: {', '.join(map(str, failed))}" if failed else ""), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
