"""Python's garbage collection, on the main thread only (spec §4.1).

The cyclic collector runs on whichever thread is allocating when a threshold is
crossed, the session's worker too. A cycle holding main-thread widgets would then
be destroyed there, and a widget's timer can't be stopped from a thread without
an event dispatcher: it fires later at freed memory. So automatic collection is
turned off, and a main-thread timer collects instead.
"""

from __future__ import annotations

import gc

from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer

INTERVAL_MS = 500


class GcOnMainThread(QObject):
    """Turns automatic collection off and collects from a main-thread timer,
    only the generations over their thresholds."""

    def __init__(self, parent: QObject | None = None, interval_ms: int = INTERVAL_MS) -> None:
        super().__init__(parent)
        gc.disable()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.check)
        self._timer.start(interval_ms)

    @classmethod
    def install(cls, app: QCoreApplication) -> GcOnMainThread:
        """The app's one collector, made on its first call (on the main thread)."""
        if QThread.currentThread() is not app.thread():
            raise RuntimeError("GcOnMainThread must be installed on the main thread")
        existing = app.findChild(cls)
        return existing if existing is not None else cls(app)

    def check(self) -> int | None:
        """Collect the oldest generation over its threshold (and the younger ones
        with it); the generation collected, or None when none is over."""
        counts, thresholds = gc.get_count(), gc.get_threshold()
        over = [g for g in range(3) if counts[g] > thresholds[g]]
        if not over:
            return None
        gc.collect(over[-1])
        return over[-1]
