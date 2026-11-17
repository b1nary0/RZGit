"""History's ref labels: which branches, tags and stashes each commit gets a
label for, and their text (spec §5.4, RZG-77). Plain Python, no Qt.

- Local branches, remote-tracking branches and tags come from the session's refs;
  ``<remote>/HEAD`` gets none. Stashes come from the stash list: a stash's label is
  on the commit it was made on (its first parent).
- A local branch and the remote-tracking branches of the same name on the same
  commit are one label, ``main ⇄ origin`` (nearly always a branch and its upstream).
- A detached HEAD has a ``HEAD`` label on its commit.
- Order, in three tiers (RZG-86): the main label (``main`` or ``origin/main``;
  ``master`` in a repository with neither), then labels with another ``origin/…``
  ref, then the rest. Within a tier: the current branch (or HEAD), other local
  branches, remote branches, tags, stashes (newest first); within a kind, by name.
  History shows the first label of a commit, and a count of the others.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from rzgitclient.git.git_models import Ref, Stash

HEADS, REMOTES, TAGS = "refs/heads/", "refs/remotes/", "refs/tags/"
UPSTREAM_MARK = " ⇄ "
ORIGIN = "origin/"
MAIN, MASTER = "main", "master"  # the main branch's name; master where there's no main


class RefKind(IntEnum):
    """A label's kind; the value is its place in a row (after ``current``)."""

    HEAD = 0  # a detached HEAD
    BRANCH = 1
    REMOTE = 2
    TAG = 3
    STASH = 4


@dataclass(frozen=True, slots=True)
class RefLabel:
    kind: RefKind
    text: str  # as shown: "main", "main ⇄ origin", "origin/feature", "v1.0", "stash@{0}", "HEAD"
    names: tuple[str, ...]  # the refs it stands for, short: ("main", "origin/main")
    current: bool = False  # HEAD's branch (or a detached HEAD): drawn bold
    message: str = ""  # a stash's

    @property
    def tooltip(self) -> str:
        """One line per ref it stands for."""
        if self.kind is RefKind.HEAD:
            return "HEAD (detached)"
        if self.kind is RefKind.STASH:
            return f"Stash {self.text}: {self.message}"
        if self.kind is RefKind.TAG:
            return f"Tag {self.text}"
        lines = []
        for name in self.names:
            if self.kind is RefKind.BRANCH and name == self.names[0]:
                lines.append(f"Branch {name}" + (" (checked out)" if self.current else ""))
            else:
                lines.append(f"Remote branch {name}")
        return "\n".join(lines)


def row_tooltip(labels: Iterable[RefLabel]) -> str:
    """Every ref of a row, one a line, in full."""
    return "\n".join(label.tooltip for label in labels)


def labels_by_commit(refs: Iterable[Ref], head: str | None, head_sha: str | None,
                     stashes: Iterable[Stash] = ()) -> dict[str, tuple[RefLabel, ...]]:
    """Each commit's labels, by sha, in their order. ``head`` is HEAD's branch (None
    when detached), ``head_sha`` its commit (None before the first commit)."""
    local: dict[str, list[str]] = {}  # sha -> branch names
    remote: dict[str, list[str]] = {}  # sha -> "origin/main"
    tags: dict[str, list[str]] = {}
    for ref in refs:
        name = ref.name
        if name.startswith(HEADS):
            local.setdefault(ref.sha, []).append(name[len(HEADS):])
        elif name.startswith(REMOTES):
            short = name[len(REMOTES):]
            if "/" in short and short.split("/", 1)[1] != "HEAD":
                remote.setdefault(ref.sha, []).append(short)
        elif name.startswith(TAGS):
            tags.setdefault(ref.sha, []).append(name[len(TAGS):])
    by_base: dict[str, list[Stash]] = {}
    for stash in stashes:
        if stash.parents:
            by_base.setdefault(stash.parents[0], []).append(stash)

    has_main = any(MAIN in names for names in local.values()) or any(
        ORIGIN + MAIN in names for names in remote.values())
    main = MAIN if has_main else MASTER

    shas = set(local) | set(remote) | set(tags) | set(by_base)
    if head is None and head_sha is not None:
        shas.add(head_sha)
    result: dict[str, tuple[RefLabel, ...]] = {}
    for sha in shas:
        labels: list[RefLabel] = []
        if head is None and sha == head_sha:
            labels.append(RefLabel(RefKind.HEAD, "HEAD", ("HEAD",), current=True))
        remotes = sorted(remote.get(sha, ()))
        for branch in sorted(local.get(sha, ()), key=lambda b: (b != head, b)):
            twins = [r for r in remotes if r.split("/", 1)[1] == branch]
            for twin in twins:
                remotes.remove(twin)
            text = branch
            if twins:
                text += UPSTREAM_MARK + ", ".join(t.split("/", 1)[0] for t in twins)
            labels.append(RefLabel(RefKind.BRANCH, text, (branch, *twins),
                                   current=branch == head))
        labels += [RefLabel(RefKind.REMOTE, r, (r,)) for r in remotes]
        labels += [RefLabel(RefKind.TAG, t, (t,)) for t in sorted(tags.get(sha, ()))]
        labels += [RefLabel(RefKind.STASH, s.ref, (s.ref,), message=s.message)
                   for s in sorted(by_base.get(sha, ()), key=lambda s: s.index)]
        result[sha] = tuple(sorted(labels, key=lambda label: _tier(label, main)))  # stable
    return result


def _tier(label: RefLabel, main: str) -> int:
    """0: the main label; 1: another with an ``origin/…`` ref; 2: the rest."""
    if label.kind is RefKind.BRANCH:
        local, remotes = label.names[0], label.names[1:]
    elif label.kind is RefKind.REMOTE:
        local, remotes = None, label.names
    else:
        return 2
    if local == main or ORIGIN + main in remotes:
        return 0
    return 1 if any(name.startswith(ORIGIN) for name in remotes) else 2
