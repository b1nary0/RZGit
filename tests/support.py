"""Shared test helpers (spec §10)."""

from __future__ import annotations

import os

# Must be set before Qt picks a platform plugin.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402


def qt_app() -> QApplication:
    """The one QApplication shared by every test in the run."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app
