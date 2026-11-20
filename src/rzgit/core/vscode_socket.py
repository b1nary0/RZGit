"""Talking to the RZGit VS Code extension (spec §8, RZG-75).

Each VS Code window's extension listens on a Unix socket named after its
workspace (``VSCodeTarget.target``). The extension's ``socketPath`` computes the
same path: keep the two in step (tests/test_vscode_extension.py runs both).
One JSON line goes each way. Plain Python.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

EXTENSION_ID = "rzgit.rzgit-vscode"  # publisher.name, in assets/vscode/package.json
HOW = ("definition", "symbol", "pick", "none")  # how the extension found it


def socket_folder(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_RUNTIME_DIR/rzgit``, else ``<tmp>/rzgit-<uid>``."""
    env = os.environ if env is None else env
    runtime = env.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "rzgit"
    return Path(tempfile.gettempdir()) / f"rzgit-{os.getuid()}"


def socket_path(target: Path | str, env: Mapping[str, str] | None = None) -> Path:
    """The socket of the VS Code window whose workspace is ``target``."""
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:16]
    return socket_folder(env) / f"vscode-{digest}.sock"


def definition_request(symbol: str, file: Path | str | None = None,
                       line: int | None = None, col: int | None = None) -> bytes:
    """The request line: go to ``symbol``'s definition, looked up at ``file``,
    ``line`` and ``col`` (1-based; col in UTF-16 units) if given."""
    request = {"cmd": "definition", "symbol": symbol,
               "file": str(file) if file is not None else None, "line": line, "col": col}
    return (json.dumps(request) + "\n").encode("utf-8")


@dataclass(frozen=True)
class Reply:
    ok: bool
    how: str = ""  # one of HOW when ok
    message: str = ""


def parse_reply(data: bytes) -> Reply | None:
    """The extension's reply line; None if it isn't one."""
    try:
        reply = json.loads(data.decode("utf-8").splitlines()[0])
    except (UnicodeDecodeError, IndexError, ValueError):
        return None
    if not isinstance(reply, dict) or not isinstance(reply.get("ok"), bool):
        return None
    return Reply(reply["ok"], str(reply.get("how") or ""), str(reply.get("message") or ""))
