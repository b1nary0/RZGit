import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PySide6.QtCore import QCoreApplication, QThread
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QWidget

from rzgit.core.preferences import Preferences
from rzgit.core.repo_session import RepoSession
from rzgit.core.selection import Selection
from rzgit.core.vscode_socket import Reply, socket_path
from rzgit.ui import vscode_definition
from rzgit.ui.app_actions import NO_SYMBOL, NO_VSCODE_CHECK, AppActions
from rzgit.ui.settings_window import SettingsWindow
from rzgit.ui.vscode_definition import DefinitionRequest, VSCodeDefinition
from rzgit.ui.vscode_extension_installer import INSTALLED, VSCodeExtensionInstaller
from rzgit.ui.vscode_launcher import NO_COMMAND, NO_REPO, VSCODE_COMMAND, VSCodeLauncher
from tests.support import commit_file, make_repo, pump, qt_app, wait_idle


def wait_for(condition, ms: int) -> bool:
    """Process events until ``condition()`` holds; False if it didn't in ``ms``."""
    deadline = time.monotonic() + ms / 1000
    while not condition():
        if time.monotonic() > deadline:
            return False
        QCoreApplication.processEvents()
        QThread.msleep(1)
    return True

SOURCE = "import os\n\n\ndef helper():\n    return 1\n\n\nvalue = helper()\n"
# A stand-in for `code`: --list-extensions prints $FAKE_EXTENSIONS; anything else
# records its arguments in $FAKE_LOG and exits with $FAKE_EXIT.
FAKE_CODE = """import json, os, sys
if sys.argv[1:] == ["--list-extensions"]:
    print(os.environ.get("FAKE_EXTENSIONS", ""))
    sys.exit(0)
with open(os.environ["FAKE_LOG"], "a") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""


class FakeExtension:
    """A window's extension: listens on its socket, records the requests, and
    answers with ``reply``."""

    def __init__(self, path: Path, reply: bytes = b'{"ok": true, "how": "definition"}\n'):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.reply, self.requests = path, reply, []
        self.server = QLocalServer()

    def listen(self) -> None:
        assert self.server.listen(str(self.path)), self.server.errorString()
        self.server.newConnection.connect(self._on_connection)

    def _on_connection(self) -> None:
        conn = self.server.nextPendingConnection()

        def read():
            data = bytes(conn.readAll().data())
            if data:
                self.requests.append(json.loads(data))
                if self.reply:
                    conn.write(self.reply)
                    conn.flush()
                conn.disconnectFromServer()
        conn.readyRead.connect(read)

    def close(self) -> None:
        self.server.close()


class VSCodeDefinitionTests(unittest.TestCase):
    """Go to Definition in VS Code (spec §8, RZG-75)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.path = make_repo(tmp / "repo")
        commit_file(self.path, "a.py", SOURCE, "first")
        self.env = {"XDG_RUNTIME_DIR": str(tmp / "run")}
        self.session = RepoSession()
        self.actions = AppActions(self.session, Selection(self.session))
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.prefs = Preferences()
        self.prefs.set(VSCODE_COMMAND, sys.executable)  # found: an absolute path
        self.launcher = VSCodeLauncher(self.window, self.session, self.actions, self.prefs)
        self.started = []
        self.launcher.start_detached = lambda argv, folder: self.started.append(argv) or True
        self.definition = VSCodeDefinition(self.window, self.launcher, self.actions, self.env)
        self.installed = True  # what --list-extensions says (None: it couldn't run)
        self.definition.list_extensions = lambda done: done(self.installed)
        self.asked = []
        self.definition.ask_install = lambda: self.asked.append(True) or False
        self.replies = []
        self.definition.finished.connect(self.replies.append)
        for name, value in (("CONNECT_MS", 1500), ("RETRY_MS", 50), ("REPLY_MS", 1500)):
            patch = mock.patch.object(vscode_definition, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def tearDown(self):
        self.session.shutdown()
        self._tmp.cleanup()

    def open_repo(self):
        self.assertTrue(self.session.open(self.path))
        wait_idle(self.session)
        self.root = self.session.repo.root
        self.file = self.root / "a.py"

    def extension(self, **kwargs) -> FakeExtension:
        fake = FakeExtension(socket_path(self.root, self.env), **kwargs)
        self.addCleanup(fake.close)
        return fake

    def wait_reply(self, timeout=5000):
        self.assertTrue(wait_for(lambda: bool(self.replies), timeout))
        return self.replies[0]

    def test_the_check(self):
        self.assertEqual(self.definition.problem(), NO_REPO)
        self.open_repo()
        self.assertIsNone(self.definition.problem())
        self.assertIsNone(self.actions.definition_problem("helper"))
        self.assertEqual(self.actions.definition_problem(""), NO_SYMBOL)
        self.prefs.set(VSCODE_COMMAND, "/no/such/code")
        self.assertEqual(self.actions.definition_problem("helper"), NO_COMMAND)

    def test_before_it_is_made_the_check_says_vs_code_isnt_set_up(self):
        actions = AppActions(self.session, Selection(self.session))
        self.assertEqual(actions.definition_problem("helper"), NO_VSCODE_CHECK)
        self.assertFalse(actions.request_definition("helper", "a.py", 1))

    def test_the_request_finds_the_symbol_near_the_diffs_line(self):
        self.open_repo()
        request = self.definition.request
        self.assertEqual(request("helper", "a.py", 8), DefinitionRequest("helper", self.file, 8, 9))
        self.assertEqual(request("helper", "a.py", 6), DefinitionRequest("helper", self.file, 4, 5))
        # Not near the line: no column, so the extension looks for it by name.
        self.assertEqual(request("missing", "a.py", 6),
                         DefinitionRequest("missing", self.file, 6, None))
        # Not in the working tree (deleted, or only in a commit): by name only.
        self.assertEqual(request("helper", "gone.py", 6),
                         DefinitionRequest("helper", None, None, None))
        self.assertEqual(request("helper", None, None), DefinitionRequest("helper", None, None, None))

    def test_it_raises_the_window_and_asks_its_extension(self):
        self.open_repo()
        fake = self.extension()
        fake.listen()
        self.assertTrue(self.actions.request_definition("helper", "a.py", 8))
        self.assertEqual(self.wait_reply(), Reply(True, "definition", ""))
        self.assertEqual(self.started, [[sys.executable, str(self.root)]])  # no --goto
        self.assertEqual(fake.requests, [{"cmd": "definition", "symbol": "helper",
                                          "file": str(self.file), "line": 8, "col": 9}])
        self.assertEqual(self.asked, [])

    def test_a_window_that_is_opening_is_waited_for(self):
        self.open_repo()
        fake = self.extension()
        self.actions.request_definition("helper", "a.py", 8)
        pump(300)  # a few failed tries
        self.assertEqual(self.replies, [])
        fake.listen()
        self.assertEqual(self.wait_reply().how, "definition")
        self.assertEqual(len(fake.requests), 1)

    def test_no_window_listens_the_file_opens_at_the_symbol(self):
        self.open_repo()
        self.actions.request_definition("helper", "a.py", 8)
        self.assertIsNone(self.wait_reply())
        self.assertEqual(self.started, [
            [sys.executable, str(self.root)],
            [sys.executable, str(self.root), "--goto", f"{self.file}:8:9"]])

    def test_an_extension_that_doesnt_answer(self):
        self.open_repo()
        fake = self.extension(reply=b"")
        fake.listen()
        self.actions.request_definition("helper", "a.py", 8)
        self.assertIsNone(self.wait_reply())
        self.assertEqual(self.started[-1][-1], f"{self.file}:8:9")

    def test_without_the_extension_the_file_opens_and_installing_is_offered_once(self):
        self.open_repo()
        self.installed = False
        self.actions.request_definition("helper", "a.py", 8)
        self.assertEqual(self.replies, [None])
        self.assertEqual(self.started, [[sys.executable, str(self.root), "--goto",
                                         f"{self.file}:8:9"]])
        self.actions.request_definition("missing", "a.py", 6)
        self.assertEqual(self.started[-1][-1], f"{self.file}:6")  # no column
        self.actions.request_definition("helper", "gone.py", 6)
        self.assertEqual(self.started[-1], [sys.executable, str(self.root)])  # the window only
        self.assertEqual(self.asked, [True])

    def test_installing_when_offered(self):
        self.open_repo()
        self.installed = False
        self.definition.ask_install = lambda: True
        with mock.patch.object(VSCodeExtensionInstaller, "install") as install:
            self.actions.request_definition("helper", "a.py", 8)
        install.assert_called_once()
        self.definition._installer.finished.emit(True)
        self.installed = None  # not asked again: installed
        self.definition.list_extensions = lambda done: self.fail("asked again")
        fake = self.extension()
        fake.listen()
        self.replies.clear()
        self.actions.request_definition("helper", "a.py", 8)
        self.assertEqual(self.wait_reply().how, "definition")

    def test_list_extensions_runs_the_command(self):
        self.open_repo()
        script = Path(self._tmp.name) / "fake_code.py"
        script.write_text(FAKE_CODE)
        self.prefs.set(VSCODE_COMMAND, f"{sys.executable} {script}")
        definition = VSCodeDefinition(self.window, self.launcher, self.actions, self.env)
        for listed, expected in (("ms-python.python\nRZGit.rzgit-vscode", True),
                                 ("ms-python.python", False)):
            answers = []
            with mock.patch.dict("os.environ", {"FAKE_EXTENSIONS": listed}):
                definition.list_extensions(answers.append)
                self.assertTrue(wait_for(lambda: bool(answers), 10_000))
            self.assertEqual(answers, [expected])
        self.prefs.set(VSCODE_COMMAND, "/no/such/code")
        answers = []
        definition.list_extensions(answers.append)
        self.assertEqual(answers, [None])


class VSCodeExtensionInstallerTests(unittest.TestCase):
    """Installing the extension (spec §8, §8.2, RZG-75)."""

    @classmethod
    def setUpClass(cls):
        qt_app()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.script = tmp / "fake_code.py"
        self.script.write_text(FAKE_CODE)
        self.log = tmp / "log"
        self.window = QWidget()
        self.addCleanup(self.window.deleteLater)
        self.boxes = []

    def tearDown(self):
        self._tmp.cleanup()

    def installer(self, command):
        installer = VSCodeExtensionInstaller(self.window, command)
        installer.inform = lambda text, info: self.boxes.append(("inform", text))
        installer.warn = lambda text, info: self.boxes.append(("warn", text))
        results = []
        installer.finished.connect(results.append)
        return installer, results

    def run_install(self, exit_code):
        installer, results = self.installer([sys.executable, str(self.script)])
        with mock.patch.dict("os.environ", {"FAKE_LOG": str(self.log),
                                            "FAKE_EXIT": str(exit_code)}):
            self.assertTrue(installer.install())
            self.assertTrue(installer.running)
            self.assertFalse(installer.install())  # one at a time
            self.assertTrue(wait_for(lambda: bool(results), 10_000))
        self.assertFalse(installer.running)
        return results

    def test_it_installs_the_packed_extension(self):
        self.assertEqual(self.run_install(0), [True])
        args = json.loads(self.log.read_text().splitlines()[0])
        self.assertEqual(args[0], "--install-extension")
        self.assertTrue(args[1].endswith("rzgit-vscode.vsix"))
        self.assertEqual(args[2:], ["--force"])
        self.assertFalse(Path(args[1]).exists())  # its temporary folder is gone
        self.assertEqual(self.boxes, [("inform", INSTALLED)])

    def test_a_failed_install_says_so(self):
        self.assertEqual(self.run_install(1), [False])
        self.assertEqual(self.boxes[0][0], "warn")

    def test_no_command(self):
        installer, results = self.installer(None)
        self.assertFalse(installer.install())
        self.assertEqual(self.boxes, [("warn", NO_COMMAND)])

    def test_the_settings_tools_page_has_its_button(self):
        window = SettingsWindow(Preferences())
        self.addCleanup(window.deleteLater)
        button = window.page_buttons["Tools: VS Code extension"]
        self.assertEqual(button.text(), "Install…")
        with mock.patch.object(VSCodeExtensionInstaller, "install") as install:
            button.click()
        install.assert_called_once()
        self.assertEqual(len(window.findChildren(VSCodeExtensionInstaller)), 1)


if __name__ == "__main__":
    unittest.main()
