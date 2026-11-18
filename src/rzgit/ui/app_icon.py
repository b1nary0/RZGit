"""The app's icon, from the PNGs in ``assets/icon`` (spec §3).

app.py sets it on the QApplication, so every window and dialog shows it. Each size is
its own file, so Qt picks the one made for that size instead of scaling another: KDE's
title bar uses 20 px and its taskbar 22 px. The files up to 32 px have no
semi-transparent pixels, which KDE's title bar draws with colour fringes.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon

ASSETS_FOLDER = Path(__file__).resolve().parent / "assets"
ICON_FOLDER = ASSETS_FOLDER / "icon"
ICON_SIZES = (16, 20, 22, 24, 32, 48, 64, 128, 256, 512)


def icon_file(size: int) -> Path:
    """The PNG of the app icon drawn at ``size`` px."""
    return ICON_FOLDER / f"rzgit-{size}.png"


def app_icon() -> QIcon:
    icon = QIcon()
    for size in ICON_SIZES:
        icon.addFile(str(icon_file(size)), QSize(size, size))
    return icon
