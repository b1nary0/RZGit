"""Go to Definition in VS Code, through the RZGit extension's socket (spec §8, RZG-75).

The Diff panel asks with ``actions.go_to_definition`` / ``request_definition``;
this does it: ``code <target>`` raises or opens this worktree's window, then the
request goes to that window's extension (``core/vscode_socket.py``), which looks
the symbol up with VS Code's language support. Without the extension, the file
opens at the symbol instead, and the first time a box offers to install it.
MainWindow makes it, with its VSCodeLauncher. It runs no git.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QObject, QProcess, QTimer, Signal
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QMessageBox, QWidget

from rzgit.core.symbol_at import find_symbol_near
from rzgit.core.vscode_socket import (
    EXTENSION_ID,
    Reply,
    definition_request,
    parse_reply,
    socket_path,
)
from rzgit.ui.app_actions import AppActions
from rzgit.ui.vscode_extension_installer import VSCodeExtensionInstaller
from rzgit.ui.vscode_launcher import NO_COMMAND, NO_REPO, VSCodeLauncher

log = logging.getLogger(__name__)

CONNECT_MS = 10_000  # how long a window that is opening may take to listen
RETRY_MS = 250
REPLY_MS = 20_000  # the extension retries for a while (a language server starting)
LIST_MS = 10_000  # code --list-extensions
MAX_FILE = 8 * 1024 * 1024  # larger working-tree files aren't read for the position
OFFER = "Install the RZGit extension in VS Code?"
OFFER_INFO = ("With it, F12 goes straight to the definition. Without it, VS Code opens "
              "the file at the symbol, and F12 there goes to its definition.")


@dataclass(frozen=True)
class DefinitionRequest:
    """What goes to the extension, and where the file opens without it."""

    symbol: str
    file: Path | None  # absolute, in the working tree; None: by name only
    line: int | None  # 1-based
    col: int | None  # 1-based, UTF-16 units; None: the symbol isn't on the line

    def data(self) -> bytes:
        return definition_request(self.symbol, self.file, self.line, self.col)


def read_lines(file: Path) -> list[str]:
    """The working-tree file's lines; none if it can't be read, or is too large."""
    try:
        if file.stat().st_size > MAX_FILE:
            return []
        return file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


class VSCodeDefinition(QObject):
    finished = Signal(object)  # Reply | None: the extension's, or None (it opened the file)

    def __init__(self, window: QWidget, launcher: VSCodeLauncher, actions: AppActions,
                 env: Mapping[str, str] | None = None):
        """``env``: where the sockets are (``$XDG_RUNTIME_DIR``); os.environ if None."""
        super().__init__(window)
        self._window, self._launcher, self._env = window, launcher, env
        self._installed = False  # found once: not asked again
        self._offered = False  # the install box, once per session
        self._installer: VSCodeExtensionInstaller | None = None
        actions.set_definition_check(self.problem)
        actions.definition_requested.connect(self.go)

    def problem(self) -> str | None:
        """Why Go to Definition in VS Code can't run, or None."""
        if self._launcher.target() is None:
            return NO_REPO
        return NO_COMMAND if self._launcher.command() is None else None

    def request(self, symbol: str, path: str | None, line: int | None) -> DefinitionRequest:
        """The request for ``symbol``, seen in ``path`` (relative to the root) at
        the diff's ``line``: where it is in the working-tree file near that line."""
        target = self._launcher.target()
        file = target.root / path if target is not None and path else None
        if file is None or not file.is_file():
            return DefinitionRequest(symbol, None, None, None)
        found = find_symbol_near(read_lines(file), line, symbol)
        if found is None:
            return DefinitionRequest(symbol, file, line, None)
        return DefinitionRequest(symbol, file, *found)

    def go(self, symbol: str, path: str | None, line: int | None) -> None:
        if self.problem() is not None:
            return
        request = self.request(symbol, path, line)
        if self._installed:
            self._send(request)
        else:
            self.list_extensions(lambda installed: self._after_list(request, installed))

    def _after_list(self, request: DefinitionRequest, installed: bool | None) -> None:
        """``installed``: None if it couldn't be told (then the socket is tried)."""
        if installed is False:
            self._open_at(request)
            self.finished.emit(None)
            self._offer_install()
            return
        self._installed = bool(installed)
        self._send(request)

    # ---- the extension ---------------------------------------------------------

    def _send(self, request: DefinitionRequest) -> None:
        target, argv = self._launcher.target(), self._launcher.command()
        if target is None or argv is None:
            return
        self._launcher.start_detached(target.argv(argv), str(target.root))  # raise or open
        _Exchange(self, socket_path(target.target, self._env), request,
                  lambda reply: self._on_reply(request, reply))

    def _on_reply(self, request: DefinitionRequest, reply: Reply | None) -> None:
        if reply is None:  # no window listened, or no answer
            self._open_at(request)
        elif not reply.ok:
            log.warning("VS Code extension: %s", reply.message)
        self.finished.emit(reply)

    # ---- without it --------------------------------------------------------------

    def _open_at(self, request: DefinitionRequest) -> None:
        """The file at the symbol (``--goto``), so F12 in VS Code finds it."""
        target, argv = self._launcher.target(), self._launcher.command()
        if target is None or argv is None:
            return
        if request.file is None:
            self._launcher.start_detached(target.argv(argv), str(target.root))
            return
        relative = str(request.file.relative_to(target.root))
        self._launcher.start_detached(
            target.argv(argv, (relative,), request.line or 1, request.col), str(target.root))

    def _offer_install(self) -> None:
        if self._offered:
            return
        self._offered = True
        if self.ask_install():
            self._installer = VSCodeExtensionInstaller(self._window, self._launcher.command())
            self._installer.finished.connect(lambda ok: ok and self._set_installed())
            self._installer.install()

    def _set_installed(self) -> None:
        self._installed = True

    def ask_install(self) -> bool:
        box = QMessageBox(QMessageBox.Icon.Question, "Go to Definition in VS Code", OFFER,
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                          self._window)
        box.setInformativeText(OFFER_INFO)
        box.setDefaultButton(QMessageBox.StandardButton.Yes)
        return box.exec() == QMessageBox.StandardButton.Yes

    def list_extensions(self, done) -> None:
        """``done(installed)``, from ``code --list-extensions`` in the background:
        True or False, or None if it couldn't run."""
        argv = self._launcher.command()
        if argv is None:
            done(None)
            return
        process = QProcess(self)
        timer = QTimer(process)
        timer.setSingleShot(True)
        answered = []

        def answer(installed: bool | None) -> None:
            if answered:
                return
            answered.append(installed)
            timer.stop()
            if process.state() is not QProcess.ProcessState.NotRunning:
                process.kill()
            process.deleteLater()
            done(installed)

        def on_finished(code: int, status: QProcess.ExitStatus) -> None:
            if code != 0 or status is not QProcess.ExitStatus.NormalExit:
                answer(None)
                return
            out = bytes(process.readAllStandardOutput().data()).decode("utf-8", "replace")
            answer(EXTENSION_ID in out.lower().split())

        process.finished.connect(on_finished)
        process.errorOccurred.connect(
            lambda error: error is QProcess.ProcessError.FailedToStart and answer(None))
        timer.timeout.connect(lambda: answer(None))
        timer.start(LIST_MS)
        process.start(argv[0], [*argv[1:], "--list-extensions"])


class _Exchange(QObject):
    """One request to a window's extension: connect (retrying while the window
    opens), send the line, read the reply line. ``done(reply)`` once; None if no
    window listened or it didn't answer."""

    def __init__(self, parent: QObject, path: Path, request: DefinitionRequest, done):
        super().__init__(parent)
        self._path, self._data, self._done = str(path), request.data(), done
        self._connect_ms, self._buffer = CONNECT_MS, b""
        self._socket = QLocalSocket(self)
        self._socket.connected.connect(self._on_connected)
        self._socket.errorOccurred.connect(self._on_error)
        self._socket.readyRead.connect(self._on_ready_read)
        self._socket.disconnected.connect(self._on_disconnected)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._sent = False
        self._reply_timer = QTimer(self)
        self._reply_timer.setSingleShot(True)
        self._reply_timer.setInterval(REPLY_MS)
        self._reply_timer.timeout.connect(lambda: self._finish(None))
        self._connect()

    def _connect(self) -> None:
        if self._done is None:
            return
        self._socket.abort()
        self._socket.connectToServer(self._path)

    def _on_error(self, _error: QLocalSocket.LocalSocketError) -> None:
        if self._sent:
            self._finish(parse_reply(self._buffer))
        elif self._clock.elapsed() + RETRY_MS < self._connect_ms:
            QTimer.singleShot(RETRY_MS, self, self._connect)
        else:
            self._finish(None)

    def _on_connected(self) -> None:
        self._sent = True
        self._socket.write(self._data)
        self._socket.flush()
        self._reply_timer.start()

    def _on_ready_read(self) -> None:
        self._buffer += bytes(self._socket.readAll().data())
        if b"\n" in self._buffer:
            self._finish(parse_reply(self._buffer))

    def _on_disconnected(self) -> None:
        if self._sent:
            self._finish(parse_reply(self._buffer))

    def _finish(self, reply: Reply | None) -> None:
        done, self._done = self._done, None
        if done is None:
            return
        self._reply_timer.stop()
        self._socket.abort()
        self.deleteLater()
        done(reply)
