"""Which VS Code window a worktree's files open in, and the command line (spec §8, RZG-96).

``code <file>`` alone opens the file in a window whose folder holds it, else in
the last active one. A lane's worktree is outside the main checkout, so its
files would land in some other project's window. The command line names the
worktree's own target first, which VS Code reuses if a window has it open, or
opens a new window for. Plain Python.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VSCodeTarget:
    """A worktree, and the tracked ``*.code-workspace`` files of its repository."""

    root: Path  # the worktree's folder
    workspace_files: tuple[str, ...] = ()  # relative to the root (session.workspace_files)

    @property
    def target(self) -> Path:
        """What picks the window: the workspace file when the repository tracks
        exactly one and this worktree has it (its folder paths are relative, so
        each worktree's copy opens that worktree); otherwise the worktree's folder."""
        if len(self.workspace_files) == 1:
            workspace = self.root / self.workspace_files[0]
            if workspace.is_file():
                return workspace
        return self.root

    def present(self, paths: Iterable[str]) -> tuple[str, ...]:
        """The ``paths`` (relative to the root) that are files in the working tree."""
        return tuple(p for p in paths if (self.root / p).is_file())

    def argv(self, command: Sequence[str], paths: Sequence[str] = (),
             line: int | None = None, col: int | None = None) -> list[str]:
        """``command`` with the target and ``paths``, made absolute (so none starts
        with ``-``); with ``line`` (and ``col``, RZG-75), the first path opens there
        (``--goto``). With no paths, the target alone: it raises or opens the window."""
        files = [str(self.root / p) for p in paths]
        if line is not None and files:
            goto = f"{files[0]}:{line}" + (f":{col}" if col is not None else "")
            return [*command, str(self.target), "--goto", goto, *files[1:]]
        return [*command, str(self.target), *files]
