"""packaging/linux/install-desktop.sh, run into a temporary data folder (spec §3)."""

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from rzgit.ui.app_icon import ICON_SIZES, icon_file

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "linux" / "install-desktop.sh"
TEMPLATE = ROOT / "packaging" / "linux" / "rzgit.desktop"


class InstallDesktopTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.data = base / "data"
        self.exec_path = base / "bin dir" / "rzgit"  # a space, which Exec must quote
        self.exec_path.parent.mkdir()
        self.exec_path.write_text("#!/bin/sh\n")
        self.exec_path.chmod(self.exec_path.stat().st_mode | stat.S_IXUSR)

    def run_script(self, *args, exec_path=None):
        env = dict(os.environ, XDG_DATA_HOME=str(self.data), RZGIT_NO_CACHE_REFRESH="1",
                   RZGIT_EXEC=str(exec_path or self.exec_path))
        return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True,
                              text=True)

    @property
    def entry(self):
        return self.data / "applications" / "rzgit.desktop"

    def icon(self, size):
        return self.data / "icons" / "hicolor" / f"{size}x{size}" / "apps" / "rzgit.png"

    def installed_files(self):
        return sorted(p for p in self.data.rglob("*") if p.is_file())

    def test_install_writes_the_entry_and_every_icon(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        text = self.entry.read_text()
        self.assertIn(f'Exec="{self.exec_path}" %f\n', text)
        self.assertIn("Icon=rzgit\n", text)
        self.assertNotIn("@EXEC@", text)
        for size in ICON_SIZES:
            self.assertEqual(self.icon(size).read_bytes(), icon_file(size).read_bytes(), size)

    def test_reinstall_is_clean(self):
        self.run_script()
        first = self.installed_files()
        # A file an older install listed (a size since dropped) goes; Exec is rewritten.
        stale = self.icon(18)
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"old")
        manifest = self.data / "applications" / ".rzgit-installed"
        manifest.write_text(manifest.read_text() + f"{stale}\n")
        moved = self.exec_path.parent / "rzgit-moved"
        shutil.copy2(self.exec_path, moved)
        result = self.run_script(exec_path=moved)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertEqual(self.installed_files(), first)
        self.assertIn(f'Exec="{moved}" %f\n', self.entry.read_text())

    def test_uninstall_removes_everything_it_installed(self):
        self.run_script()
        keep = self.data / "applications" / "other.desktop"  # not ours: left alone
        keep.write_text("[Desktop Entry]\n")
        result = self.run_script("--uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.installed_files(), [keep])

    def test_install_removes_the_old_names_install(self):
        # An install from when the app was RZGitClient (RZG-59): its entry and icons go,
        # through its own manifest, and other files are left alone.
        apps = self.data / "applications"
        old_entry = apps / "rzgitclient.desktop"
        old_icon = self.data / "icons" / "hicolor" / "64x64" / "apps" / "rzgitclient.png"
        keep = apps / "other.desktop"
        old_icon.parent.mkdir(parents=True)
        apps.mkdir()
        for path in (old_entry, old_icon, keep):
            path.write_text("old")
        (apps / ".rzgitclient-installed").write_text(f"{old_entry}\n{old_icon}\n{keep}\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old_entry.exists())
        self.assertFalse(old_icon.exists())
        self.assertFalse((apps / ".rzgitclient-installed").exists())
        self.assertTrue(keep.exists())
        self.assertTrue(self.entry.exists())

    def test_uninstall_with_nothing_installed(self):
        self.assertEqual(self.run_script("--uninstall").returncode, 0)

    def test_manifest_cannot_delete_other_files(self):
        self.run_script()
        other = self.data / "applications" / "other.desktop"
        other.write_text("[Desktop Entry]\n")
        manifest = self.data / "applications" / ".rzgit-installed"
        manifest.write_text(manifest.read_text() + f"{other}\n")
        self.run_script("--uninstall")
        self.assertTrue(other.exists())

    def test_refuses_a_missing_program(self):
        result = self.run_script(exec_path=self.exec_path.parent / "missing")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.data.exists())

    @unittest.skipUnless(shutil.which("desktop-file-validate"), "desktop-file-validate not installed")
    def test_entries_validate(self):
        self.run_script()
        for path in (TEMPLATE, self.entry):
            result = subprocess.run(["desktop-file-validate", str(path)], capture_output=True,
                                    text=True)
            self.assertEqual((result.returncode, result.stdout), (0, ""), path)


if __name__ == "__main__":
    unittest.main()
