"""Changed files grouped by folder, for the file panels' tree (spec §5.4).

Plain Python, no Qt, so it is tested on its own. ``FileList`` turns the
tree into rows.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass
class FileNode(Generic[T]):
    name: str  # the file's name
    path: str  # its full path in the repository
    data: T  # what the caller gave with the path


@dataclass
class FolderNode(Generic[T]):
    name: str  # what the row shows: one folder, or a chain ("src/rzgitclient/ui")
    path: str  # the full folder path ("" for the root); what collapsing is remembered by
    folders: list[FolderNode[T]] = field(default_factory=list)
    files: list[FileNode[T]] = field(default_factory=list)

    def all_files(self) -> Iterator[FileNode[T]]:
        """Every file under this folder, in tree order."""
        for folder in self.folders:
            yield from folder.all_files()
        yield from self.files


def build_tree(entries: Iterable[tuple[str, T]]) -> FolderNode[T]:
    """The root folder of ``entries`` (path, data). Folders come first, then
    files, each by name; a folder holding only one folder is merged into it."""
    root: FolderNode[T] = FolderNode("", "")
    folders: dict[str, FolderNode[T]] = {"": root}

    def folder(path: str) -> FolderNode[T]:
        node = folders.get(path)
        if node is None:
            parent_path, name = posixpath.split(path)
            node = folders[path] = FolderNode(name, path)
            folder(parent_path).folders.append(node)
        return node

    for path, data in entries:
        parent_path, name = posixpath.split(path)
        folder(parent_path).files.append(FileNode(name, path, data))
    _tidy(root)
    return root


def _tidy(node: FolderNode[T]) -> None:
    for i, child in enumerate(node.folders):
        while not child.files and len(child.folders) == 1:
            only = child.folders[0]
            child = FolderNode(f"{child.name}/{only.name}", only.path, only.folders, only.files)
        node.folders[i] = child
        _tidy(child)
    node.folders.sort(key=lambda f: (f.name.casefold(), f.name))
    node.files.sort(key=lambda f: (f.name.casefold(), f.name))


def sort_tree(node: FolderNode[T],
              files: Callable[[list[FileNode[T]]], list[FileNode[T]]],
              folders_reversed: bool = False) -> None:
    """Sort inside each folder (RZG-43): ``files`` orders a folder's files (they
    come by name); its folders stay first, by name, reversed when asked."""
    node.files = files(node.files)
    if folders_reversed:
        node.folders.reverse()
    for folder in node.folders:
        sort_tree(folder, files, folders_reversed)


def file_label(path: str, orig_path: str | None = None) -> str:
    """A file row's text in the tree: its name, and for a rename the old name
    too (the old path, if the folder changed)."""
    folder, name = posixpath.split(path)
    if not orig_path:
        return name
    orig_folder, orig_name = posixpath.split(orig_path)
    return f"{orig_name if orig_folder == folder else orig_path} → {name}"
