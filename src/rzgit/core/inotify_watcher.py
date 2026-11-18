"""Linux inotify on a QSocketNotifier (spec §4.3).

One watch per folder. Unlike QFileSystemWatcher, a folder's watch sees files
changed in place, and each event names the file. QtCore only.
"""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import struct
from dataclasses import dataclass

from PySide6.QtCore import QObject, QSocketNotifier, Signal

log = logging.getLogger(__name__)

# <sys/inotify.h>
IN_MODIFY = 0x00000002
IN_ATTRIB = 0x00000004
IN_CLOSE_WRITE = 0x00000008
IN_MOVED_FROM = 0x00000040
IN_MOVED_TO = 0x00000080
IN_CREATE = 0x00000100
IN_DELETE = 0x00000200
IN_DELETE_SELF = 0x00000400
IN_MOVE_SELF = 0x00000800
IN_Q_OVERFLOW = 0x00004000
IN_IGNORED = 0x00008000
IN_ONLYDIR = 0x01000000
IN_DONT_FOLLOW = 0x02000000
IN_EXCL_UNLINK = 0x04000000
IN_ISDIR = 0x40000000
_IN_NONBLOCK = os.O_NONBLOCK
_IN_CLOEXEC = os.O_CLOEXEC

_MASK = (IN_MODIFY | IN_ATTRIB | IN_CLOSE_WRITE | IN_MOVED_FROM | IN_MOVED_TO
         | IN_CREATE | IN_DELETE | IN_DELETE_SELF | IN_MOVE_SELF
         | IN_ONLYDIR | IN_DONT_FOLLOW | IN_EXCL_UNLINK)
_HEADER = struct.Struct("iIII")  # wd, mask, cookie, len


@dataclass(frozen=True)
class InotifyEvent:
    folder: str  # the watched folder, as it was added
    name: str  # the entry inside it; "" for the folder itself
    mask: int

    @property
    def is_dir(self) -> bool:
        return bool(self.mask & IN_ISDIR)

    @property
    def appeared(self) -> bool:
        return bool(self.mask & (IN_CREATE | IN_MOVED_TO))

    @property
    def vanished(self) -> bool:
        return bool(self.mask & (IN_DELETE | IN_MOVED_FROM))


def _libc():
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.inotify_init1  # noqa: B018 (raises AttributeError off Linux)
    except (OSError, AttributeError):
        return None
    libc.inotify_init1.argtypes = [ctypes.c_int]
    libc.inotify_add_watch.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_uint32]
    libc.inotify_rm_watch.argtypes = [ctypes.c_int, ctypes.c_int]
    return libc


class InotifyWatcher(QObject):
    """Watches folders; emits what changed inside them, read in batches."""

    events_read = Signal(object)  # tuple[InotifyEvent, ...]
    overflowed = Signal()  # the kernel dropped events: anything may have changed

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._wds: dict[str, int] = {}
        self._folders: dict[int, str] = {}
        self._fd = -1
        self._notifier: QSocketNotifier | None = None
        self.last_error = 0  # errno of the last add() that failed
        self._libc = _libc()
        if self._libc is None:
            return
        fd = self._libc.inotify_init1(_IN_NONBLOCK | _IN_CLOEXEC)
        if fd < 0:
            log.warning("inotify unavailable: %s", os.strerror(ctypes.get_errno()))
            return
        self._fd = fd
        self._notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self.read_now)

    @property
    def available(self) -> bool:
        return self._fd >= 0

    @property
    def folders(self) -> set[str]:
        return set(self._wds)

    def add(self, folder: str) -> bool:
        """Watch ``folder``. False if it can't be (gone, or out of watches:
        then ``errno.ENOSPC`` is in ``last_error``)."""
        self.last_error = 0
        if not self.available:
            return False
        if folder in self._wds:
            return True
        wd = self._libc.inotify_add_watch(self._fd, os.fsencode(folder), _MASK)
        if wd < 0:
            self.last_error = ctypes.get_errno()
            if self.last_error not in (errno.ENOENT, errno.ENOTDIR):
                log.warning("can't watch %s: %s", folder, os.strerror(self.last_error))
            return False
        old = self._folders.get(wd)  # the same folder under another name
        if old is not None:
            del self._wds[old]
        self._wds[folder] = wd
        self._folders[wd] = folder
        return True

    def remove(self, folder: str) -> None:
        wd = self._wds.pop(folder, None)
        if wd is None:
            return
        del self._folders[wd]
        self._libc.inotify_rm_watch(self._fd, wd)  # fails harmlessly if it's gone

    def clear(self) -> None:
        for folder in list(self._wds):
            self.remove(folder)

    def close(self) -> None:
        self.clear()
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier = None
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def read_now(self) -> None:
        """Read the events waiting in the kernel and emit them now."""
        if self._fd < 0:
            return
        events: list[InotifyEvent] = []
        overflow = False
        while True:
            try:
                data = os.read(self._fd, 65536)
            except BlockingIOError:
                break
            except OSError as exc:
                log.warning("inotify read failed: %s", exc)
                break
            if not data:
                break
            offset = 0
            while offset + _HEADER.size <= len(data):
                wd, mask, _cookie, size = _HEADER.unpack_from(data, offset)
                offset += _HEADER.size
                raw = data[offset:offset + size].split(b"\0", 1)[0]
                offset += size
                if mask & IN_Q_OVERFLOW:
                    overflow = True
                    continue
                folder = self._folders.get(wd)
                if folder is None:  # removed meanwhile
                    continue
                if mask & IN_IGNORED:  # the folder is gone; the kernel dropped its watch
                    del self._folders[wd]
                    del self._wds[folder]
                    continue
                events.append(InotifyEvent(folder, os.fsdecode(raw), mask))
        if events:
            self.events_read.emit(tuple(events))
        if overflow:
            self.overflowed.emit()
