"""Install the RZGit VS Code extension (spec §8, §8.2, RZG-75).

Packs ``assets/vscode`` into a .vsix (``build_vsix``) in a temporary folder and
runs ``<VS Code command> --install-extension <file> --force`` in the background,
then says whether it worked. Settings → Tools has a button for it (declared
here), and VSCodeDefinition offers it when F12 finds no extension.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from rzgit.core.vsix_builder import build_vsix
from rzgit.ui.settings_window import PageButton, SettingsWindow, declare_page_button
from rzgit.ui.vscode_launcher import NO_COMMAND, VSCODE_COMMAND, find_vscode

EXTENSION_FOLDER = Path(__file__).resolve().parent / "assets" / "vscode"
TITLE = "Install VS Code Extension"
INSTALLED = "The RZGit extension is installed in VS Code."
RELOAD = ("Windows already open start it at once. If it was installed before, they "
          "use the new version after Developer: Reload Window.")


class VSCodeExtensionInstaller(QObject):
    finished = Signal(bool)  # installed

    def __init__(self, parent: QWidget, command: list[str] | None,
                 folder: Path = EXTENSION_FOLDER):
        """``command``: the VS Code command line (``find_vscode``); None: not found."""
        super().__init__(parent)
        self._window, self._command, self._folder = parent, command, folder
        self._process: QProcess | None = None
        self._tmp: tempfile.TemporaryDirectory | None = None

    @property
    def running(self) -> bool:
        return self._process is not None

    def install(self) -> bool:
        """Start installing; False if it can't start (and a box said why)."""
        if self.running:
            return False
        if self._command is None:
            self.warn(NO_COMMAND, "")
            return False
        self._tmp = tempfile.TemporaryDirectory(prefix="rzgit-vsix-")
        vsix = Path(self._tmp.name) / "rzgit-vscode.vsix"
        try:
            build_vsix(self._folder, vsix)
        except (OSError, ValueError, KeyError) as error:
            self._done()
            self.warn("Couldn't pack the extension.", str(error))
            return False
        process = self._process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.finished.connect(lambda code, status: self._on_finished(
            code == 0 and status == QProcess.ExitStatus.NormalExit))
        process.errorOccurred.connect(self._on_error)
        command = self._command
        process.start(command[0], [*command[1:], "--install-extension", str(vsix), "--force"])
        return True

    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error is QProcess.ProcessError.FailedToStart:
            self._on_finished(False)

    def _on_finished(self, ok: bool) -> None:
        if self._process is None:
            return
        output = bytes(self._process.readAll().data()).decode("utf-8", "replace").strip()
        self._process.deleteLater()
        self._done()
        if ok:
            self.inform(INSTALLED, RELOAD)
        else:
            self.warn("Couldn't install the RZGit extension in VS Code.",
                      output or "Check the VS Code command in Settings (Tools).")
        self.finished.emit(ok)

    def _done(self) -> None:
        self._process = None
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None

    def inform(self, text: str, info: str) -> None:
        self._box(QMessageBox.Icon.Information, text, info)

    def warn(self, text: str, info: str) -> None:
        self._box(QMessageBox.Icon.Warning, text, info)

    def _box(self, icon: QMessageBox.Icon, text: str, info: str) -> None:
        box = QMessageBox(icon, TITLE, text, parent=self._window)
        box.setInformativeText(info)
        box.exec()


def _install_from_settings(window: SettingsWindow) -> None:
    if any(i.running for i in window.findChildren(VSCodeExtensionInstaller)):
        return  # one at a time
    command = find_vscode(str(window.prefs.value(VSCODE_COMMAND)))
    VSCodeExtensionInstaller(window, command).install()  # the window keeps it


INSTALL_BUTTON = declare_page_button(PageButton(
    "Tools", "VS Code extension", "Install…", _install_from_settings,
    help="Installs the RZGit extension in VS Code, which Go to Definition in VS Code "
         "(F12 in the Diff panel) uses."))
