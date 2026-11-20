"""The RZGit VS Code extension, under Node (spec §8, RZG-75).

Its own tests are in tests/js (a stub ``vscode``); this runs them, and checks
that it names a window's socket as core/vscode_socket.py does. Skipped without
``node``.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from rzgit.core.vscode_socket import socket_path
from rzgit.ui.vscode_extension_installer import EXTENSION_FOLDER

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "node isn't installed")
class VSCodeExtensionTests(unittest.TestCase):
    def test_its_tests(self):
        run = subprocess.run([NODE, "--test", str(ROOT / "tests/js/extension_test.js")],
                             capture_output=True, text=True, timeout=60, cwd=ROOT)
        self.assertEqual(run.returncode, 0, run.stdout[-4000:] + run.stderr[-2000:])

    def test_the_socket_path_is_rzgits(self):
        targets = ["/home/me/repo", "/home/me/RZGit-RZG-75/RZGit.code-workspace",
                   "/tmp/größe ü/x", "/a'b\"c"]
        envs = [{"XDG_RUNTIME_DIR": "/run/user/1000"}, {}]
        script = ("const e = require(process.argv[1]);"
                  "const [targets, envs] = JSON.parse(process.argv[2]);"
                  "console.log(JSON.stringify(envs.map(env => targets.map("
                  "t => e.socketPath(t, env)))));")
        run = subprocess.run(
            [NODE, "-e", script, str(EXTENSION_FOLDER / "extension.js"),
             json.dumps([targets, envs])],
            capture_output=True, text=True, timeout=30, check=True)
        self.assertEqual(json.loads(run.stdout),
                         [[str(socket_path(t, env)) for t in targets] for env in envs])


if __name__ == "__main__":
    unittest.main()
