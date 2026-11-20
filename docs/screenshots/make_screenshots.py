"""Make the screenshots of docs/USAGE.md (RZG-91).

Run it from the repository root after a change to the UI:

    .venv/bin/python docs/screenshots/make_screenshots.py

It clones this repository into a fresh temporary folder and sets up some
work there: a few changed files, one of them staged, a stash, a branch and a
second worktree. It then opens RZGit on the clone offscreen, in the Light
theme, with settings in another fresh temporary folder, so your own settings
and repositories are never read or changed. Each picture is the window (or a
part of it) with its subject outlined in the theme's accent colour, the rest
dimmed slightly, and numbered badges that the guide refers to. The PNG files
are written next to this script.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"  # before Qt picks a platform

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))  # this checkout's code, not an installed copy

_SCRATCH = Path(tempfile.mkdtemp(prefix="rzgit-screenshots-"))
os.environ["XDG_CONFIG_HOME"] = str(_SCRATCH / "config")
os.environ["XDG_DATA_HOME"] = str(_SCRATCH / "data")

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import (QAbstractItemView, QAbstractScrollArea, QApplication, QMenu,  # noqa: E402
                               QTabWidget, QToolButton, QWidget)  # noqa: E402

from outline_shape import outline_path, overlap_groups  # noqa: E402  (beside this script)
from rzgit.core.app_settings import AppSettings  # noqa: E402
from rzgit.core.column_prefs import ColumnPrefs  # noqa: E402
from rzgit.core.commit_drafts import CommitDrafts  # noqa: E402
from rzgit.core.preferences import Preferences  # noqa: E402
from rzgit.core.recent_repos import RecentRepos  # noqa: E402
from rzgit.core.selection import FileSelection, FileSource  # noqa: E402
from rzgit.core.sort_prefs import SortPrefs  # noqa: E402
from rzgit.ui.app_windows import AppWindows  # noqa: E402
from rzgit.ui.layout.panel_frame import PanelFrame  # noqa: E402
from rzgit.ui.main_window import MainWindow  # noqa: E402
from rzgit.ui.panels.panel import Panel  # noqa: E402
from rzgit.ui.settings_window import SettingsWindow  # noqa: E402
from rzgit.ui.theme.app_theme import LIGHT_THEME  # noqa: E402
from rzgit.ui.theme.theme_manager import ThemeManager, ThemeMode  # noqa: E402

WINDOW_SIZE = QSize(1280, 860)  # big enough for every panel, small enough to read
DIM_ALPHA = 80  # of 255: how much the parts that aren't the subject are dimmed
OUTLINE_WIDTH = 3
OUTLINE_RADIUS = 6
BADGE_SIZE = 24
MARGIN = 12  # around a part of the window shown alone
ICON_THEME = "breeze"  # the light Breeze icons, as on a KDE desktop


# ---- the repository -------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout


def make_clone() -> Path:
    """A clone of this repository's main branch with some work in progress."""
    repo = _SCRATCH / "RZGit"
    subprocess.run(["git", "clone", "-q", "--no-hardlinks", "--branch", "main", str(ROOT),
                    str(repo)], check=True)
    git(repo, "config", "user.name", "RZGit Docs")
    git(repo, "config", "user.email", "docs@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "branch", "feature/usage-guide")
    git(repo, "branch", "experiment/sidebar")
    # A stash, made from a change to the README.
    readme = repo / "README.md"
    readme.write_text(readme.read_text() + "\nA note to come back to.\n")
    git(repo, "stash", "push", "-q", "-m", "Try a shorter README introduction")
    # A second worktree, so the Worktrees panel shows.
    git(repo, "worktree", "add", "-q", str(_SCRATCH / "RZGit-usage-guide"),
        "feature/usage-guide")
    # Work in progress: a staged change, unstaged changes and a new file.
    edit(repo / "src/rzgit/ui/panels/find_bar.py", "Match case", "Match upper and lower case")
    git(repo, "add", "src/rzgit/ui/panels/find_bar.py")
    edit(repo / "src/rzgit/ui/panels/commit_panel.py", '"Commit summary"', '"Summary"')
    edit(repo / "src/rzgit/ui/panels/commit_panel.py", '"Description"',
         '"Description (optional)"')
    edit(repo / "README.md", "A Qt (PySide6) git client for Linux.",
         "A fast Qt (PySide6) git client for Linux.")
    (repo / "notes.txt").write_text("Screenshots for the usage guide.\n")
    return repo


def edit(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if old not in text:
        raise SystemExit(f"{path.name}: {old!r} not found; update make_clone()")
    path.write_text(text.replace(old, new, 1))


# ---- the window -----------------------------------------------------------------


class App:
    def __init__(self, repo: Path):
        self.qt = QApplication.instance() or QApplication([])
        self.qt.setApplicationName("RZGit")
        self.qt.setOrganizationName("RZGit")
        QIcon.setThemeName(ICON_THEME)  # offscreen has no desktop to ask for one
        settings = AppSettings(_SCRATCH / "settings.ini")
        self.prefs = Preferences(settings)
        self.theme = ThemeManager(self.qt, LIGHT_THEME, mode=ThemeMode.LIGHT, prefs=self.prefs)
        self.windows = AppWindows(self.qt, settings, self.theme, CommitDrafts(),
                                  ColumnPrefs(settings), SortPrefs(settings), prefs=self.prefs,
                                  recent=RecentRepos(settings))
        self.window: MainWindow = self.windows.new_window()
        self.window.resize(WINDOW_SIZE)
        self.window.show()
        self.window.session.open(repo)
        self.settle()
        self.repo = repo

    def settle(self, rounds: int = 3) -> None:
        """Let git commands finish and the window lay itself out."""
        from PySide6.QtCore import QThread
        for _ in range(rounds):
            for _ in range(2000):
                self.qt.processEvents()
                if not self.window.session.busy and not any(
                        w.session.busy for w in self.window.commit_windows):
                    break
                QThread.msleep(2)
            for _ in range(30):
                self.qt.processEvents()
                QThread.msleep(5)

    def panel(self, panel_id: str) -> Panel:
        return self.window.layout_host.panels[panel_id]

    @property
    def accent(self) -> QColor:
        return QColor(self.theme.current.colors.accent)

    @property
    def accent_text(self) -> QColor:
        return QColor(self.theme.current.colors.accent_text)

    @property
    def dim(self) -> QColor:
        colour = QColor(self.theme.current.colors.text)
        colour.setAlpha(DIM_ALPHA)
        return colour

    # ---- selecting things --------------------------------------------------------

    def select_file(self, path: str, source: FileSource) -> None:
        selection = self.window.ctx.selection
        selection.commit = None
        file = FileSelection(path, source)
        selection.select((file,), file)
        self.settle()

    def select_commit(self, index: int = 0) -> None:
        self.window.ctx.selection.commit = self.window.session.history[index]
        self.settle()

    def uncommitted(self) -> None:
        self.window.ctx.selection.commit = None
        self.settle()


def pane(widget: QWidget) -> QWidget:
    """What shows a panel: its frame in a split, or its tab group."""
    w: QWidget | None = widget
    while w is not None:
        if isinstance(w, (PanelFrame, QTabWidget)):
            return w
        w = w.parentWidget()
    return widget


# ---- drawing --------------------------------------------------------------------


@dataclass
class Picture:
    """An image of the window, with what to draw on it, in window coordinates."""

    app: App
    image: QPixmap
    subjects: list[QRect] = field(default_factory=list)
    badges: list[tuple[int, QPoint]] = field(default_factory=list)
    overlays: list[tuple[QPixmap, QPoint]] = field(default_factory=list)  # menus

    def rect_of(self, widget: QWidget) -> QRect:
        return QRect(widget.mapTo(self.app.window, QPoint(0, 0)), widget.size())

    def outline(self, *targets: QWidget | QRect) -> None:
        for t in targets:
            self.subjects.append(t if isinstance(t, QRect) else self.rect_of(t))

    def badge(self, number: int, target: QWidget | QRect, where: str) -> None:
        """A numbered badge by ``target``, placed so it covers no text:
        ``left``/``right`` just outside that edge, ``above``/``below`` just
        outside that edge at the middle, ``inside-right`` within its right end,
        ``header`` in a panel's section header, left of its ⋯ button, and
        ``center`` on its middle."""
        r = target if isinstance(target, QRect) else self.rect_of(target)
        half, gap = BADGE_SIZE // 2, 4
        mid_x, mid_y = r.center().x(), r.center().y()
        if where == "header" and isinstance(target, PanelFrame):
            r = self.rect_of(target.header)
            mid_y = r.center().y()
        centre = {
            "left": QPoint(r.left() - half - gap, mid_y),
            "right": QPoint(r.right() + half + gap, mid_y),
            "above": QPoint(mid_x, r.top() - half - gap),
            "below": QPoint(mid_x, r.bottom() + half + gap),
            "inside-right": QPoint(r.right() - half - gap, mid_y),
            "header": QPoint(r.right() - 2 * BADGE_SIZE - gap, mid_y),
            "center": QPoint(mid_x, mid_y),
        }[where]
        self.badges.append((number, centre))

    def menu(self, menu: QMenu, at: QPoint) -> QRect:
        """Draw ``menu`` with its top-left at ``at`` (window coordinates), outlined."""
        menu.ensurePolished()
        menu.adjustSize()
        image = menu.grab()
        rect = QRect(at, image.deviceIndependentSize().toSize())
        self.overlays.append((image, at))
        self.subjects.append(rect)
        return rect

    def render(self, crop: QRect | None = None, dim: bool = True) -> QPixmap:
        """The finished image; ``crop`` (window coordinates) shows only that part
        (with the menus and badges drawn by it)."""
        half = BADGE_SIZE // 2 + 2
        circles = [QRect(c.x() - half, c.y() - half, 2 * half, 2 * half) for _, c in self.badges]
        menus = [QRect(at, image.deviceIndependentSize().toSize()) for image, at in self.overlays]
        if crop is None:
            bounds = QRect(QPoint(0, 0), self.app.window.size())
            for extra in menus:
                bounds = bounds.united(extra)
        else:
            bounds = crop
            for extra in menus + circles:
                bounds = bounds.united(extra)
            bounds = bounds.adjusted(-MARGIN, -MARGIN, MARGIN, MARGIN)
        canvas = QPixmap(bounds.size())
        canvas.fill(QColor(self.app.theme.current.colors.sidebar_bg))
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(-bounds.topLeft())
        painter.drawPixmap(QPoint(0, 0), self.image)
        for image, at in self.overlays:
            painter.drawPixmap(at, image)
        # Overlapping subjects (a menu over its panel) share one outline (RZG-95).
        groups = overlap_groups(self.subjects)
        if dim and self.subjects:
            path = QPainterPath()
            path.addRect(QRectF(bounds))
            for group in groups:
                hole = QPainterPath()
                if len(group) == 1:
                    hole.addRoundedRect(QRectF(group[0]), OUTLINE_RADIUS, OUTLINE_RADIUS)
                else:
                    hole = outline_path(group, OUTLINE_RADIUS)
                path = path.subtracted(hole)
            painter.fillPath(path, self.app.dim)
        painter.setPen(QPen(self.app.accent, OUTLINE_WIDTH))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        inset = OUTLINE_WIDTH / 2
        for group in groups:
            if len(group) == 1:
                painter.drawRoundedRect(QRectF(group[0]).adjusted(inset, inset, -inset, -inset),
                                        OUTLINE_RADIUS, OUTLINE_RADIUS)
                continue
            # The same edges as a single subject's: a stroke centred on the shape
            # inset by half its width is a doubled stroke of the shape, clipped to it.
            shape = outline_path(group, OUTLINE_RADIUS + inset)
            painter.save()
            painter.setClipPath(shape)
            painter.setPen(QPen(self.app.accent, 2 * OUTLINE_WIDTH))
            painter.drawPath(shape)
            painter.restore()
        font = QFont(self.app.qt.font())
        font.setBold(True)
        font.setPixelSize(13)
        painter.setFont(font)
        r = BADGE_SIZE / 2
        for number, centre in self.badges:
            # Kept inside the image.
            cx = min(max(centre.x(), bounds.left() + r + 2), bounds.right() - r - 2)
            cy = min(max(centre.y(), bounds.top() + r + 2), bounds.bottom() - r - 2)
            circle = QRectF(cx - r, cy - r, BADGE_SIZE, BADGE_SIZE)
            painter.setPen(QPen(self.app.accent_text, 2))
            painter.setBrush(self.app.accent)
            painter.drawEllipse(circle)
            painter.setPen(self.app.accent_text)
            painter.drawText(circle, Qt.AlignmentFlag.AlignCenter, str(number))
        painter.end()
        return canvas


def picture(app: App) -> Picture:
    app.settle()
    return Picture(app, app.window.grab())


def save(image: QPixmap, name: str) -> None:
    path = HERE / f"{name}.png"
    image.save(str(path))
    print(f"wrote {path.relative_to(ROOT)} ({image.width()}×{image.height()})")


def item_rect(app: App, view: QAbstractItemView, rect: QRect) -> QRect:
    """``rect`` of ``view``'s viewport, in window coordinates."""
    return QRect(view.viewport().mapTo(app.window, rect.topLeft()), rect.size())


def tabs(app: App, panel_id: str) -> QRect:
    """The tabs of ``panel_id``'s tab group, in window coordinates."""
    bar = pane(app.panel(panel_id)).tabBar()
    rect = bar.tabRect(0).united(bar.tabRect(bar.count() - 1))
    return QRect(bar.mapTo(app.window, rect.topLeft()), rect.size())


def grow(app: App, panel_id: str, by: int) -> None:
    """Make ``panel_id``'s pane taller by ``by`` px, from the tallest pane beside it."""
    frame = pane(app.panel(panel_id))
    splitter = frame.parentWidget()
    sizes = splitter.sizes()
    index = splitter.indexOf(frame)
    # The donor: the tallest pane that can spare it.
    donors = [i for i in range(len(sizes)) if i != index
              and sizes[i] - by >= splitter.widget(i).minimumSizeHint().height()]
    if not donors:
        return
    donor = max(donors, key=lambda i: sizes[i])
    sizes[index] += by
    sizes[donor] -= by
    splitter.setSizes(sizes)
    app.settle()


# ---- the shots ------------------------------------------------------------------


def shot_overview(app: App) -> None:
    """The window as it opens, its parts numbered."""
    app.uncommitted()
    app.select_file("README.md", FileSource.WORKTREE)
    app.panel("history").request_activation()
    p = picture(app)
    p.badge(1, app.panel("controls"), "center")
    for number, panel_id in enumerate(("branches", "worktrees", "stash", "files", "staged",
                                       "commit"), start=2):
        p.badge(number, pane(app.panel(panel_id)), "header")
    p.badge(8, tabs(app, "history"), "right")
    p.badge(9, app.window.statusBar(), "inside-right")
    save(p.render(dim=False), "overview")


def shot_menus(app: App) -> None:
    """Each menu of the menu bar, alone."""
    for action in app.window.menuBar().actions():
        menu = action.menu()
        menu.aboutToShow.emit()
        menu.ensurePolished()
        menu.adjustSize()
        save(menu.grab(), "menu-" + action.text().replace("&", "").lower())


def shot_controls(app: App) -> None:
    """The Controls panel alone, its buttons numbered."""
    controls = app.panel("controls")
    p = picture(app)
    buttons = [b for b in controls.findChildren(QToolButton)
               if b.isVisible() and b.parentWidget() is controls]
    for number, button in enumerate(buttons, start=1):
        p.badge(number, button, "below")
    p.outline(controls)
    save(p.render(crop=p.rect_of(controls)), "controls")


def shot_history(app: App) -> None:
    app.uncommitted()
    history = app.panel("history")
    history.request_activation()
    p = picture(app)
    view = history._view
    model = view.model()
    p.outline(pane(history))
    p.badge(1, item_rect(app, view, view.visualRect(model.index(0, 1))), "inside-right")
    p.badge(2, item_rect(app, view, view.visualRect(model.index(1, 0))), "left")
    header = view.horizontalHeader()
    subject = QRect(header.sectionViewportPosition(1), 0, header.sectionSize(1), header.height())
    p.badge(3, QRect(header.mapTo(app.window, subject.topLeft()), subject.size()), "center")
    save(p.render(), "history")


def shot_changes(app: App) -> None:
    app.select_file("README.md", FileSource.WORKTREE)
    app.panel("diff").request_activation()
    p = picture(app)
    for number, panel_id in enumerate(("files", "staged", "commit"), start=1):
        target = pane(app.panel(panel_id))
        p.outline(target)
        p.badge(number, target, "header")
    p.outline(pane(app.panel("diff")))
    p.badge(4, tabs(app, "diff"), "right")
    save(p.render(), "changes")


def context_shot(app: App, panel_id: str, menu: QMenu, row: QRect, name: str) -> None:
    """The window with ``panel_id``'s pane and ``menu`` outlined, the menu opened
    on ``row`` (window coordinates)."""
    p = picture(app)
    p.outline(pane(app.panel(panel_id)))
    p.menu(menu, QPoint(row.left() + min(row.width() // 2, 120), row.bottom() + 2))
    save(p.render(), name)


def shot_files(app: App) -> None:
    """Unstaged Files with its context menu."""
    files = app.panel("files")
    app.select_file("README.md", FileSource.WORKTREE)
    tree = files._list._tree
    item = tree.currentItem() or tree.topLevelItem(0)
    context_shot(app, "files", files._list._menu(),
                 item_rect(app, tree, tree.visualItemRect(item)), "files")


def shot_diff(app: App) -> None:
    """The Diff panel alone, with the find bar open."""
    app.select_file("src/rzgit/ui/panels/commit_panel.py", FileSource.WORKTREE)
    diff = app.panel("diff")
    diff.request_activation()
    app.settle()
    diff.open_find()
    diff.find_bar.field.setText("Description")
    diff.find_bar.flush()
    app.settle()
    p = picture(app)
    area = pane(diff)
    p.outline(area)
    path = p.rect_of(diff._path)
    text = diff._path.fontMetrics().horizontalAdvance(diff._path.text())
    p.badge(1, QRect(path.left(), path.top(), text, path.height()), "right")
    toggles = {b.defaultAction(): b for b in diff.findChildren(QToolButton)}
    actions = app.window.ctx.actions
    p.badge(2, toggles[actions.whole_file_diff], "above")
    p.badge(3, toggles[actions.side_by_side_diff], "above")
    view = next(v for v in diff.findChildren(QAbstractScrollArea) if v.isVisible()
                and type(v).__name__ == "DiffView")
    # The first hunk header's Stage Hunk link, at the right end of the top row.
    metrics = view.fontMetrics()
    link = QRect(0, 0, metrics.horizontalAdvance("Stage Hunk") + 8, metrics.height())
    link.moveTopRight(view.viewport().rect().topRight())
    p.badge(4, item_rect(app, view, link), "left")
    p.badge(5, diff.find_bar, "above")
    save(p.render(crop=p.rect_of(area)), "diff")


def shot_commit_details(app: App) -> None:
    app.select_commit(0)
    app.panel("history").request_activation()
    p = picture(app)
    for number, panel_id in ((1, "history"), (2, "commit_details"), (3, "commit_files")):
        target = pane(app.panel(panel_id))
        p.outline(target)
    p.badge(1, tabs(app, "history"), "right")
    p.badge(2, pane(app.panel("commit_details")), "header")
    p.badge(3, pane(app.panel("commit_files")), "header")
    save(p.render(), "commit-details")
    app.uncommitted()


def shot_commit_window(app: App) -> None:
    """A commit in a window of its own (RZG-84), its first file's diff shown."""
    window = app.window.open_commit_window(app.window.session.history[0])
    window.resize(WINDOW_SIZE)
    app.settle()
    files = window.session.commit_files or ()
    if files:
        file = FileSelection(files[0].path, FileSource.COMMIT, window.commit.sha)
        window.selection.select((file,), file)
        app.settle()
    save(window.grab(), "commit-window")
    window.close()
    app.settle()


def shot_branches(app: App) -> None:
    branches = app.panel("branches")
    lst = branches._list
    item = next(i for i in lst.rows() if i.text(0).startswith("feature/usage-guide"))
    lst.setCurrentItem(item)
    context_shot(app, "branches", branches.menu_for("feature/usage-guide"),
                 item_rect(app, lst, lst.visualItemRect(item)), "branches")


def shot_stashes(app: App) -> None:
    stash = app.panel("stash")
    tree = stash._tree
    item = tree.topLevelItem(0)
    tree.setCurrentItem(item)
    app.settle()
    context_shot(app, "stash", stash.menu_for(stash.ctx.session.stashes[0]),
                 item_rect(app, tree, tree.visualItemRect(item)), "stashes")
    app.uncommitted()


def shot_worktrees(app: App) -> None:
    worktrees = app.panel("worktrees")
    grow(app, "worktrees", 40)
    tree = worktrees._tree
    item = tree.topLevelItem(1)
    tree.setCurrentItem(item)
    context_shot(app, "worktrees", worktrees.menu_for(worktrees.ctx.worktrees.worktrees[1]),
                 item_rect(app, tree, tree.visualItemRect(item)), "worktrees")


def shot_settings(app: App) -> None:
    window = SettingsWindow(app.prefs, app.theme)
    window.resize(720, 460)
    window.show()
    app.settle()
    save(window.grab(), "settings")
    window.close()


SHOTS: list[Callable[[App], None]] = [
    shot_overview, shot_menus, shot_controls, shot_history, shot_changes, shot_files,
    shot_diff, shot_commit_details, shot_commit_window, shot_branches, shot_stashes, shot_worktrees,
    shot_settings,
]


def main() -> int:
    print(f"scratch folder: {_SCRATCH}")
    app = App(make_clone())
    for shot in SHOTS:
        shot(app)
    app.window.close()
    app.windows.shutdown()
    print(f"done; the scratch folder {_SCRATCH} can be removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
