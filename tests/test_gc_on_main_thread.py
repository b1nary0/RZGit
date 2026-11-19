import gc
import inspect
import threading
import time
import unittest
import weakref

from rzgit import app as app_module
from rzgit.core.gc_on_main_thread import GcOnMainThread
from tests.support import qt_app


class _Node:
    """Garbage only the cyclic collector frees: it refers to itself."""

    def __init__(self):
        self.me = self


def _cycle() -> weakref.ref:
    return weakref.ref(_Node())


class GcOnMainThreadTests(unittest.TestCase):
    """RZG-101 (spec §4.1): garbage is collected on the main thread only."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        thresholds = gc.get_threshold()
        self.addCleanup(gc.set_threshold, *thresholds)

    def test_installed_once_and_automatic_collection_is_off(self):
        first = GcOnMainThread.install(self.app)
        self.assertIs(GcOnMainThread.install(self.app), first)
        self.assertIs(first.parent(), self.app)
        self.assertFalse(gc.isenabled())

    def test_it_collects_only_over_a_threshold(self):
        collector = GcOnMainThread.install(self.app)
        gc.collect()
        gc.set_threshold(1_000_000, 10, 10)
        dead = _cycle()
        self.assertIsNone(collector.check())
        self.assertIsNotNone(dead())  # not over: left for later
        gc.set_threshold(1, 10, 10)
        _cycle()
        self.assertEqual(collector.check(), 0)
        self.assertIsNone(dead())

    def test_the_oldest_generation_over_its_threshold_is_collected(self):
        collector = GcOnMainThread.install(self.app)
        gc.collect()
        gc.set_threshold(1, 1, 1_000_000)
        for _ in range(3):  # each young collection counts one towards the next generation
            _cycle()
            gc.collect(0)
        _cycle()
        self.assertEqual(collector.check(), 1)
        self.assertEqual(gc.get_count()[1], 0)

    def test_garbage_made_on_a_worker_is_collected_on_the_main_thread(self):
        threads: list[str] = []

        def record(phase, _info):
            if phase == "start":
                threads.append(threading.current_thread().name)

        gc.callbacks.append(record)
        self.addCleanup(gc.callbacks.remove, record)
        collector = GcOnMainThread(interval_ms=5)
        self.addCleanup(collector.deleteLater)
        gc.set_threshold(100, 10, 10)  # a worker crosses it many times over
        refs: list[weakref.ref] = []
        worker = threading.Thread(target=lambda: refs.extend(_cycle() for _ in range(20_000)),
                                  name="worker")
        worker.start()
        worker.join()
        self.assertEqual(threads, [])  # nothing collected on the worker
        deadline = time.monotonic() + 5
        while any(r() is not None for r in refs) and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(all(r() is None for r in refs))
        self.assertEqual(set(threads), {threading.main_thread().name})

    def test_the_app_installs_it_before_anything_else(self):
        lines = [line.strip() for line in inspect.getsource(app_module.main).splitlines()]
        made = lines.index("app = QApplication(argv)")
        self.assertTrue(lines[made + 1].startswith("GcOnMainThread.install(app)"))


if __name__ == "__main__":
    unittest.main()
