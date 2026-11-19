#!/usr/bin/env python3
"""Run RZGit's unittest suite N times, reporting progress for the lane's tab.

From the worktree root, with the lane's own src first:
    PYTHONPATH=src /home/JPOD/dev/RZGit/.venv/bin/python /home/JPOD/dev/RZGit/.claude/workflow/run_tests.py --runs 5 [-v]

Each run discovers the suite afresh (`-s tests -t .`) and runs it. Across all runs, the number of
tests finished out of the total (tests per run x runs), and the run it's on, go to
$LANE_DIR/progress/$LANE_NAME.json, which the lanes skill's watch shows as a progress bar on the lane's tab. Without those variables
(outside a lane) it just runs the tests. Exit code 0 only if every run passed.

Per repository (RZGit), never synced; the lanes skill reads the file it writes.
"""
import argparse
import json
import os
import sys
import time
import unittest
from pathlib import Path


class Progress:
    """Tests finished out of the total, written at most every 0.25 s and at the end."""

    def __init__(self, total, runs):
        self.done, self.total, self.last = 0, total, 0.0
        self.run, self.runs = 1, runs
        lane_dir, lane = os.environ.get("LANE_DIR"), os.environ.get("LANE_NAME")
        self.path = Path(lane_dir, "progress", f"{lane}.json") if lane_dir and lane else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write(force=True)

    def step(self):
        self.done += 1
        self.write()

    def write(self, force=False):
        now = time.monotonic()
        if self.path is None or (not force and now - self.last < 0.25):
            return
        self.last = now
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"done": self.done, "total": self.total, "run": self.run,
                                   "runs": self.runs, "pid": os.getpid()}))
        os.replace(tmp, self.path)

    def clear(self):
        if self.path:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass


def result_class(progress):
    class ProgressResult(unittest.TextTestResult):
        def stopTest(self, test):
            super().stopTest(test)
            progress.step()
    return ProgressResult


def discover():
    return unittest.defaultTestLoader.discover("tests", top_level_dir=".")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--runs", type=int, default=1, help="how many times to run the whole suite")
    parser.add_argument("-v", "--verbose", action="store_true", help="name each test as it runs")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    per_run = discover().countTestCases()
    progress = Progress(per_run * args.runs, args.runs)
    failed = []
    try:
        for run in range(1, args.runs + 1):
            progress.run = run
            progress.write(force=True)
            print(f"=== run {run} of {args.runs} ({per_run} tests)", flush=True)
            runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1,
                                             resultclass=result_class(progress))
            if not runner.run(discover()).wasSuccessful():
                failed.append(run)
    finally:
        progress.write(force=True)
        progress.clear()
    print(f"=== {args.runs - len(failed)} of {args.runs} runs passed"
          + (f"; failed: {', '.join(map(str, failed))}" if failed else ""), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
