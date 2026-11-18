"""Tests for local_settings.py, on scratch repositories (never the real ~/dev/<folder>.local.json).

    python3 .claude/skills/local-settings/test_local_settings.py
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "local_settings.py"


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


class LocalSettingsTest(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.tmp = Path(td.name).resolve()
        self.repo, self.other = self.tmp / "Proj", self.tmp / "Other"
        for r in (self.repo, self.other):
            git("init", "-q", "-b", "main", str(r))
            git("-C", str(r), "commit", "-q", "--allow-empty", "-m", "init")
        self.file = self.tmp / "Proj.local.json"

    def run_ls(self, *args, repo=None):
        return subprocess.run([sys.executable, str(SCRIPT), "--repo", str(repo or self.repo), *args],
                              capture_output=True, text=True)

    def test_set_then_get(self):
        r = self.run_ls("set", "test-repo", str(self.other))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(self.file.read_text()), {"test_repo": str(self.other.resolve())})
        self.assertEqual(self.run_ls("get", "test-repo").stdout.strip(), str(self.other.resolve()))

    def test_missing_file_says_how_to_set_it(self):
        r = self.run_ls("get", "test-repo")
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not set", r.stderr)
        self.assertIn("set test-repo <value>", r.stderr)
        self.assertFalse(self.file.exists())

    def test_missing_key_says_how_to_set_it(self):
        self.file.write_text("{}")
        r = self.run_ls("get", "test-repo")
        self.assertEqual(r.returncode, 2)
        self.assertIn("is not set", r.stderr)

    def test_a_test_repo_that_moved_away_is_reported(self):
        self.file.write_text(json.dumps({"test_repo": str(self.tmp / "gone")}))
        r = self.run_ls("get", "test-repo")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no longer exists", r.stderr)

    def test_refuses_a_folder_that_isnt_a_git_repository(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        r = self.run_ls("set", "test-repo", str(plain))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a git repository", r.stderr)
        self.assertFalse(self.file.exists())

    def test_refuses_this_repository(self):
        r = self.run_ls("set", "test-repo", str(self.repo))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("is this repository", r.stderr)

    def test_refuses_a_worktree_of_this_repository(self):
        wt = self.tmp / "Proj-wt"
        git("-C", str(self.repo), "worktree", "add", "-q", "--detach", str(wt))
        r = self.run_ls("set", "test-repo", str(wt))
        self.assertIn("is this repository", r.stderr)

    def test_a_lane_worktree_uses_the_main_checkouts_file(self):
        wt = self.tmp / "Proj-RZG-1"
        git("-C", str(self.repo), "worktree", "add", "-q", "--detach", str(wt))
        self.run_ls("set", "test-repo", str(self.other))
        self.assertEqual(self.run_ls("get", "test-repo", repo=wt).stdout.strip(), str(self.other.resolve()))
        self.assertFalse((self.tmp / "Proj-RZG-1.local.json").exists())

    def test_unknown_key(self):
        r = self.run_ls("get", "colour")
        self.assertIn("unknown key", r.stderr)

    def test_keeps_other_values(self):
        self.file.write_text(json.dumps({"something_else": 1}))
        self.run_ls("set", "test-repo", str(self.other))
        self.assertEqual(json.loads(self.file.read_text())["something_else"], 1)


if __name__ == "__main__":
    unittest.main()
