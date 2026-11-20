"""History's filter: a search of every commit from HEAD, on a worker of its own
(spec §4.1, "History search", RZG-88).

Reading the whole history takes a second or more on a big repository, so it
doesn't run on the session's worker: loads and diffs go on meanwhile. One
search runs at a time; a new one stops the last, whose results are then
dropped. Results are emitted on the main thread.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThreadPool, Signal

from rzgit.git.git_models import Commit, HistoryOrder
from rzgit.git.repo import GitError, Repo

MAX_MATCHES = 10_000  # the search stops here
BATCH_INTERVAL = 0.1  # s: at most this often the matches so far go out

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class HistoryMatches:
    """The commits a search found so far, newest first. Each emit's ``commits``
    starts with the same objects as the last one's. ``capped``: it stopped at
    ``MAX_MATCHES``."""

    search_id: int
    commits: tuple[Commit, ...]
    done: bool
    capped: bool = False


def commit_matches(patterns: Sequence[re.Pattern[str]], sha_prefix: str | None,
                   commit: Commit, email: str, refs: str, message: str) -> bool:
    """Whether a commit matches: every one of ``patterns`` in its message, author,
    email or refs (each in any of them), or its SHA starting with ``sha_prefix``
    (lowercase)."""
    if sha_prefix and commit.sha.startswith(sha_prefix):
        return True
    if not patterns:
        return False
    texts = [text for text in (message, commit.author, email, refs) if text]
    return all(any(pattern.search(text) for text in texts) for pattern in patterns)


class _Token:
    """One search: its id, and the flag that stops it."""

    def __init__(self, search_id: int) -> None:
        self.search_id = search_id
        self.stopped = threading.Event()


class HistorySearch(QObject):
    matches = Signal(object)  # HistoryMatches
    error_occurred = Signal(str)

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._ids = 0
        self._token: _Token | None = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        # No parent: a search still running when this is deleted can emit on it safely.
        self._relay = _Relay()
        self._relay.found.connect(self._on_found)
        self._relay.failed.connect(self._on_failed)

    def start(self, repo: Repo | None, order: HistoryOrder,
              patterns: Sequence[re.Pattern[str]], sha_prefix: str | None) -> int:
        """Stop the running search and start one for ``patterns`` / ``sha_prefix``;
        its id. With neither, nothing is started."""
        self.stop()
        self._ids += 1
        patterns = tuple(patterns)
        if repo is None or (not patterns and not sha_prefix):
            return self._ids
        token = self._token = _Token(self._ids)
        relay = self._relay
        self._pool.start(lambda: _search(repo, order, patterns, sha_prefix, token, relay))
        return token.search_id

    def stop(self) -> None:
        """Stop the running search; nothing more of it is emitted."""
        if self._token is not None:
            self._token.stopped.set()
            self._token = None

    def shutdown(self) -> None:
        self.stop()
        self._pool.waitForDone()

    def _on_found(self, token: _Token, result: HistoryMatches) -> None:
        if token is self._token and not token.stopped.is_set():
            if result.done:
                self._token = None
            self.matches.emit(result)

    def _on_failed(self, token: _Token, message: str) -> None:
        if token is self._token and not token.stopped.is_set():
            self.error_occurred.emit(message)  # its last matches follow


class _Relay(QObject):
    """Carries results back to the main thread (a queued connection)."""

    found = Signal(object, object)  # _Token, HistoryMatches
    failed = Signal(object, str)  # _Token, the error


def _search(repo: Repo, order: HistoryOrder, patterns: tuple[re.Pattern[str], ...],
            sha_prefix: str | None, token: _Token, relay: _Relay) -> None:
    """The worker side: match every commit, sending the matches so far now and then."""
    found: list[Commit] = []
    sent = 0
    last = time.monotonic()
    capped = False
    error: str | None = None
    records = repo.search_log(order)
    try:
        for commit, email, refs, message in records:
            if token.stopped.is_set():
                return
            if commit_matches(patterns, sha_prefix, commit, email, refs, message):
                if len(found) == MAX_MATCHES:
                    capped = True
                    break
                found.append(commit)
            now = time.monotonic()
            if len(found) > sent and now - last >= BATCH_INTERVAL:
                relay.found.emit(token, HistoryMatches(token.search_id, tuple(found), False))
                sent, last = len(found), now
    except GitError as exc:
        error = str(exc)
    except Exception as exc:  # a bug must not leave History "Searching…" forever
        log.exception("Searching history failed", exc_info=exc)
        error = f"Unexpected error: {exc}"
    finally:
        records.close()  # kills git if it stopped early
    if token.stopped.is_set():
        return
    if error is not None:
        relay.failed.emit(token, error)
    relay.found.emit(token, HistoryMatches(token.search_id, tuple(found), True, capped))
