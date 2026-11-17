# RZGitClient

A PySide6 git client for Linux. See `README.md` for setup, running and tests.

- **Architecture:** follow `docs/ARCHITECTURE.md` (layering, the Panel contract, layout,
  AppTheme, naming). Change that document first when a design decision changes.
- **Naming:** a module is named after its main class, in snake_case, so the type is in the
  file name (`BranchesPanel` → `branches_panel.py`).
- **Work items:** Linear team `RZG`. Features are in project *Core Workflow*, whose
  description holds the per-issue workflow; architecture work is in *App Architecture*.
  Issues name the spec sections they implement. Review each step with the user before
  starting it.
- **User guide:** `docs/USAGE.md` describes every feature for users. An issue that adds or
  changes a feature updates it, and reruns `docs/screenshots/make_screenshots.py` when the
  UI changes. `tests/test_usage_guide.py` checks its shortcut table against the app.
- **Tests:** `.venv/bin/python -m unittest discover -v -s tests -t .`
