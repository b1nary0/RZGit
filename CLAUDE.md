# RZGitClient

A PySide6 git client for Linux. See `README.md` for setup, running and tests.

- **Architecture:** follow `docs/ARCHITECTURE.md` (layering, the Panel contract, layout,
  AppTheme, naming). Change that document first when a design decision changes.
- **Naming:** a module is named after its main class, in snake_case, so the type is in the
  file name (`BranchesPanel` → `branches_panel.py`).
- **Work items:** Linear team `RZG`, project *App Architecture*. Issues name the spec
  sections they implement. Review each step with the user before starting it.
- **Tests:** `.venv/bin/python -m unittest discover -v -s tests -t .`
