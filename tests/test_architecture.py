"""The import rules of spec §2, checked on the source with ``ast`` (no imports run)."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

PACKAGE = "rzgitclient"
SRC = Path(__file__).resolve().parent.parent / "src" / PACKAGE

# Lower layers first; a module may import only its own layer or lower ones.
TOP_LAYERS = ("git", "core", "ui", "app")
UI_LAYERS = ("theme", "app_icon", "widgets", "app_actions", "panels", "layout", "branch_dialogs",
             "main_window", "app_windows")


# ---- collecting imports ------------------------------------------------------


def module_imports(source: str, module: str, is_package: bool = False) -> set[str]:
    """Absolute names ``module`` imports. ``from a import b`` gives ``a`` and ``a.b``
    (``b`` may be a submodule). Relative imports are resolved."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = module.split(".")
                parent = parts[: len(parts) - node.level + (1 if is_package else 0)]
                base = ".".join(filter(None, [*parent, base]))
            names.add(base)
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def package_imports(root: Path = SRC) -> dict[str, set[str]]:
    result = {}
    for path in sorted(root.rglob("*.py")):
        parts = [PACKAGE, *path.relative_to(root).with_suffix("").parts]
        is_package = parts[-1] == "__init__"
        if is_package:
            parts.pop()
        module = ".".join(parts)
        result[module] = module_imports(path.read_text(), module, is_package)
    return result


# ---- rules -------------------------------------------------------------------


def _within(name: str, prefix: str) -> bool:
    return name == prefix or name.startswith(prefix + ".")


def _layer(name: str) -> tuple[int, int] | None:
    """(top layer, ui layer) of one of our modules; None for anything else."""
    parts = name.split(".")
    if parts[0] != PACKAGE or len(parts) < 2 or parts[1] not in TOP_LAYERS:
        return None
    top = TOP_LAYERS.index(parts[1])
    if parts[1] == "ui" and len(parts) > 2 and parts[2] in UI_LAYERS:
        return top, UI_LAYERS.index(parts[2])
    return top, -1  # -1: not inside a ui sub-layer (or not in ui at all)


def _is_panel_module(name: str) -> bool:
    return _within(name, f"{PACKAGE}.ui.panels") and name.endswith("_panel")


def violations(modules: dict[str, set[str]]) -> list[str]:
    found = []
    for module, imports in sorted(modules.items()):
        def bad(rule: str, imported: str) -> None:
            found.append(f"{module} imports {imported}: {rule}")

        here = _layer(module)
        for name in sorted(imports):
            if _within(module, f"{PACKAGE}.git") and _within(name, "PySide6"):
                bad("git/ imports no Qt", name)
            if _within(module, f"{PACKAGE}.core") and (
                _within(name, "PySide6.QtWidgets") or _within(name, "PySide6.QtGui")
            ):
                bad("core/ imports no widgets or GUI", name)
            if module == f"{PACKAGE}.ui.layout.layout_model" and _within(name, "PySide6"):
                bad("layout_model imports no Qt", name)
            if _within(module, f"{PACKAGE}.ui.panels") and _within(name, f"{PACKAGE}.git.repo"):
                bad("panels never run git (contract rule 4)", name)
            if _is_panel_module(module) and _is_panel_module(name) and name != module:
                bad("a panel never imports another panel", name)

            there = _layer(name)
            if here is None or there is None:
                continue
            if there[0] > here[0]:
                bad(f"{TOP_LAYERS[there[0]]}/ is above {TOP_LAYERS[here[0]]}/", name)
            elif here[0] == there[0] and here[1] >= 0 and there[1] > here[1]:
                bad(f"ui {UI_LAYERS[there[1]]} is above ui {UI_LAYERS[here[1]]}", name)
    return found


# ---- tests -------------------------------------------------------------------


def _check(module: str, source: str) -> list[str]:
    return violations({module: module_imports(source, module)})


class ArchitectureTests(unittest.TestCase):
    def test_codebase_follows_the_rules(self):
        modules = package_imports()
        self.assertIn(f"{PACKAGE}.ui.main_window", modules)  # found the sources
        self.assertEqual(violations(modules), [])


class RuleTests(unittest.TestCase):
    """Each rule fires on a source that breaks it."""

    def assertBreaks(self, module, source, rule):
        found = _check(module, source)
        self.assertTrue(any(rule in v for v in found), f"{rule!r} not in {found}")

    def test_clean_sources_pass(self):
        self.assertEqual(_check("rzgitclient.ui.panels.files_panel", (
            "from PySide6.QtWidgets import QTreeWidget\n"
            "from rzgitclient.git.git_models import FileStatus\n"
            "from rzgitclient.ui.panels.panel import Panel\n"
            "from rzgitclient.ui.app_actions import AppActions\n"
        )), [])
        self.assertEqual(_check("rzgitclient.app", "from rzgitclient.ui.main_window import MainWindow"), [])

    def test_git_imports_no_qt(self):
        self.assertBreaks("rzgitclient.git.repo", "import PySide6.QtCore", "git/ imports no Qt")

    def test_core_imports_no_widgets_or_gui(self):
        self.assertBreaks("rzgitclient.core.selection", "from PySide6.QtGui import QColor",
                          "core/ imports no widgets")
        self.assertBreaks("rzgitclient.core.selection", "from PySide6 import QtWidgets",
                          "core/ imports no widgets")
        self.assertEqual(_check("rzgitclient.core.selection", "from PySide6.QtCore import QObject"), [])

    def test_layout_model_imports_no_qt(self):
        self.assertBreaks("rzgitclient.ui.layout.layout_model", "from PySide6.QtCore import Qt",
                          "layout_model imports no Qt")

    def test_panels_never_import_repo(self):
        self.assertBreaks("rzgitclient.ui.panels.history_panel", "from rzgitclient.git.repo import Repo",
                          "panels never run git")
        self.assertBreaks("rzgitclient.ui.panels.panel", "from rzgitclient.git import repo",
                          "panels never run git")

    def test_panel_imports_no_other_panel(self):
        self.assertBreaks("rzgitclient.ui.panels.history_panel",
                          "from rzgitclient.ui.panels.files_panel import FilesPanel",
                          "a panel never imports another panel")

    def test_lower_top_layer_imports_no_higher_one(self):
        self.assertBreaks("rzgitclient.git.repo", "from rzgitclient.core.repo_session import Refresh",
                          "core/ is above git/")
        self.assertBreaks("rzgitclient.core.selection", "from rzgitclient.ui.panels.panel import Panel",
                          "ui/ is above core/")

    def test_ui_layer_order(self):
        self.assertBreaks("rzgitclient.ui.panels.panel", "from rzgitclient.ui.layout.panel_frame import PanelFrame",
                          "ui layout is above ui panels")
        self.assertBreaks("rzgitclient.ui.theme.app_theme", "from rzgitclient.ui.widgets import section_header",
                          "ui widgets is above ui theme")
        self.assertBreaks("rzgitclient.ui.app_actions", "from rzgitclient.ui.panels.panel import Panel",
                          "ui panels is above ui app_actions")
        self.assertBreaks("rzgitclient.ui.layout.panel_frame", "from rzgitclient.ui.main_window import MainWindow",
                          "ui main_window is above ui layout")
        self.assertBreaks("rzgitclient.ui.main_window", "from rzgitclient.ui.app_windows import AppWindows",
                          "ui app_windows is above ui main_window")

    def test_relative_imports_are_resolved(self):
        self.assertEqual(
            module_imports("from ..layout import panel_frame", "rzgitclient.ui.panels.panel"),
            {"rzgitclient.ui.layout", "rzgitclient.ui.layout.panel_frame"},
        )
        self.assertBreaks("rzgitclient.ui.panels.panel", "from ..layout.panel_frame import PanelFrame",
                          "ui layout is above ui panels")



# ---- colours live in the theme (spec §7) -------------------------------------

_HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_QT_COLOURS = {"white", "black", "red", "green", "blue", "cyan", "magenta", "yellow",
               "gray", "darkGray", "lightGray", "darkRed", "darkGreen", "darkBlue",
               "darkCyan", "darkMagenta", "darkYellow", "GlobalColor"}


def hard_coded_colours(source: str) -> list[str]:
    """Colours written into ``source``: "#rrggbb" strings and Qt's named colours."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _HEX_COLOUR.match(node.value):
                found.append(node.value)
        elif (isinstance(node, ast.Attribute) and node.attr in _QT_COLOURS
              and isinstance(node.value, ast.Name) and node.value.id == "Qt"):
            found.append(f"Qt.{node.attr}")
    return found


class ColourRuleTests(unittest.TestCase):
    def test_no_colours_outside_the_theme(self):
        theme = SRC / "ui" / "theme"
        offenders = {
            str(path.relative_to(SRC)): colours
            for path in sorted(SRC.rglob("*.py"))
            if theme not in path.parents
            and (colours := hard_coded_colours(path.read_text()))
        }
        self.assertEqual(offenders, {})

    def test_the_rule_fires(self):
        self.assertEqual(hard_coded_colours('c = QColor("#ff0000")'), ["#ff0000"])
        self.assertEqual(hard_coded_colours("c = Qt.white"), ["Qt.white"])
        self.assertEqual(hard_coded_colours('c = Qt.GlobalColor.red'), ["Qt.GlobalColor"])
        self.assertEqual(hard_coded_colours('s = "#1 item"; t = "#abc1"'), [])


if __name__ == "__main__":
    unittest.main()
