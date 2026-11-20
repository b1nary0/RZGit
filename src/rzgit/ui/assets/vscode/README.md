# RZGit for VS Code

Lets RZGit's **Go to Definition in VS Code** (F12 in its Diff panel) open a symbol's
definition in this window, with VS Code's own language support.

Each window listens on a Unix socket that only you can open, named after its workspace
(`$XDG_RUNTIME_DIR/rzgit/vscode-<hash>.sock`). It accepts one request: go to the
definition of a symbol, in a file of this workspace. It has no settings or commands.

RZGit installs it from Settings → Tools.
