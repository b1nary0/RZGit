import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree

from rzgit.core.vscode_socket import (
    EXTENSION_ID,
    Reply,
    definition_request,
    parse_reply,
    socket_folder,
    socket_path,
)
from rzgit.core.vsix_builder import build_vsix, extension_info
from rzgit.ui.vscode_extension_installer import EXTENSION_FOLDER


class SocketPathTests(unittest.TestCase):
    """The extension's socket (spec §8, RZG-75)."""

    def test_in_the_runtime_folder_named_by_the_targets_hash(self):
        env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
        path = socket_path("/home/me/repo", env)
        self.assertEqual(path.parent, Path("/run/user/1000/rzgit"))
        self.assertRegex(path.name, r"^vscode-[0-9a-f]{16}\.sock$")
        self.assertEqual(path, socket_path(Path("/home/me/repo"), env))
        self.assertNotEqual(path, socket_path("/home/me/repo.code-workspace", env))

    def test_without_a_runtime_folder_one_of_the_users_own_in_tmp(self):
        folder = socket_folder({})
        self.assertEqual(folder.parent, Path(tempfile.gettempdir()))
        self.assertRegex(folder.name, r"^rzgit-\d+$")


class ProtocolTests(unittest.TestCase):
    def test_the_request_is_one_json_line(self):
        data = definition_request("helper", Path("/r/a.py"), 4, 5)
        self.assertTrue(data.endswith(b"\n"))
        self.assertEqual(data.count(b"\n"), 1)
        self.assertEqual(json.loads(data), {"cmd": "definition", "symbol": "helper",
                                            "file": "/r/a.py", "line": 4, "col": 5})
        self.assertEqual(json.loads(definition_request("x")),
                         {"cmd": "definition", "symbol": "x", "file": None, "line": None,
                          "col": None})

    def test_the_reply(self):
        self.assertEqual(parse_reply(b'{"ok": true, "how": "symbol", "message": ""}\n'),
                         Reply(True, "symbol", ""))
        self.assertEqual(parse_reply(b'{"ok": false, "message": "Not a symbol."}\nmore'),
                         Reply(False, "", "Not a symbol."))
        for bad in (b"", b"not json\n", b"[1]\n", b'{"ok": "yes"}\n', b"\xff\n"):
            self.assertIsNone(parse_reply(bad), bad)


class VsixTests(unittest.TestCase):
    """The .vsix RZGit installs (spec §8, RZG-75)."""

    def test_the_extensions_identity(self):
        info = extension_info(EXTENSION_FOLDER)
        self.assertEqual(info.id, EXTENSION_ID)
        package = json.loads((EXTENSION_FOLDER / "package.json").read_text())
        self.assertEqual(package["main"], "./extension.js")
        self.assertEqual(package["activationEvents"], ["onStartupFinished"])
        self.assertNotIn("dependencies", package)  # no npm packages

    def test_the_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.vsix"
            info = build_vsix(EXTENSION_FOLDER, out)
            with zipfile.ZipFile(out) as vsix:
                names = set(vsix.namelist())
                manifest = ElementTree.fromstring(vsix.read("extension.vsixmanifest"))
                ElementTree.fromstring(vsix.read("[Content_Types].xml"))  # well formed
                self.assertEqual(vsix.read("extension/extension.js"),
                                 (EXTENSION_FOLDER / "extension.js").read_bytes())
        self.assertEqual(names, {"[Content_Types].xml", "extension.vsixmanifest",
                                 "extension/package.json", "extension/extension.js",
                                 "extension/README.md"})
        ns = {"v": "http://schemas.microsoft.com/developer/vsx-schema/2011"}
        identity = manifest.find("v:Metadata/v:Identity", ns)
        self.assertEqual((identity.get("Publisher"), identity.get("Id"),
                          identity.get("Version")),
                         (info.publisher, info.name, info.version))
        asset = manifest.find("v:Assets/v:Asset", ns)
        self.assertEqual(asset.get("Path"), "extension/package.json")


if __name__ == "__main__":
    unittest.main()
