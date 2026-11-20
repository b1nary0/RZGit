// The RZGit VS Code extension: Go to Definition in VS Code (RZGit spec §8, RZG-75).
//
// Each window listens on a Unix socket named after its workspace. RZGit sends one
// JSON line, {"cmd": "definition", "symbol", "file", "line", "col"}, and gets one
// back, {"ok", "how", "message"}. socketPath must match RZGit's
// core/vscode_socket.py (tests/test_vscode_extension.py runs both).
// Plain JavaScript: no build step, no dependencies. `vscode` is only required in
// activate(), so the rest runs under plain Node for the tests.
'use strict';

const crypto = require('crypto');
const fs = require('fs');
const net = require('net');
const os = require('os');
const path = require('path');

const SYMBOL = /^[\p{L}\p{M}\p{N}_]{1,256}$/u;
const WORD = '[\\p{L}\\p{M}\\p{N}_]';
// Declarations rather than definitions, when a provider gives both.
const HEADER = /\.(h|hh|hpp|hxx|h\+\+|pyi)$/i;
const MAX_CANDIDATES = 20; // workspace symbols resolved to their definitions
const MAX_REQUEST = 64 * 1024;
const RETRY_MS = 5000; // a language server that is starting finds nothing yet
const STEP_MS = 500;

function socketFolder(env = process.env) {
  if (env.XDG_RUNTIME_DIR) {
    return path.join(env.XDG_RUNTIME_DIR, 'rzgit');
  }
  return path.join(os.tmpdir(), `rzgit-${process.getuid()}`);
}

function socketPath(target, env = process.env) {
  const digest = crypto.createHash('sha256').update(target, 'utf8').digest('hex');
  return path.join(socketFolder(env), `vscode-${digest.slice(0, 16)}.sock`);
}

// The path RZGit names the window by: its workspace file, else its first folder.
function workspaceTarget(vscode) {
  const file = vscode.workspace.workspaceFile;
  if (file && file.scheme === 'file') {
    return file.fsPath;
  }
  const folders = vscode.workspace.workspaceFolders;
  return folders && folders.length ? folders[0].uri.fsPath : null;
}

function createHandler(vscode, { retryMs = RETRY_MS, stepMs = STEP_MS } = {}) {
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  async function retry(find) {
    const end = Date.now() + retryMs;
    for (;;) {
      let found = [];
      try {
        found = (await find()) || [];
      } catch (_error) {
        found = [];
      }
      if (found.length || Date.now() >= end) {
        return found;
      }
      await sleep(stepMs);
    }
  }

  function inWorkspace(file) {
    const folders = vscode.workspace.workspaceFolders || [];
    return folders.some((folder) => {
      const relative = path.relative(folder.uri.fsPath, path.resolve(file));
      return relative !== '' && !relative.startsWith('..') && !path.isAbsolute(relative);
    });
  }

  function named(symbol) {
    return (s) => s.name === symbol || s.name.startsWith(`${symbol}(`);
  }

  // A provider's Location or LocationLink, as { uri, range (to open), full (to compare) }.
  function place(found) {
    if (found.targetUri) {
      const range = found.targetSelectionRange || found.targetRange;
      return { uri: found.targetUri, range, full: found.targetRange || range };
    }
    return { uri: found.uri, range: found.range, full: found.range };
  }

  const key = (p) => `${p.uri.toString()}:${p.range.start.line}:${p.range.start.character}`;
  const filePath = (p) => p.uri.fsPath || p.uri.path || '';

  function overlaps(a, b) {
    return a.uri.toString() === b.uri.toString()
      && a.full.start.line <= b.full.end.line && b.full.start.line <= a.full.end.line;
  }

  function unique(places) {
    const seen = new Set();
    return places.filter((p) => !seen.has(key(p)) && seen.add(key(p)));
  }

  // The definitions to go to: not the declarations, and not headers when
  // something else is left (a C++ function is declared in one, defined elsewhere).
  function definitionsOnly(definitions, declarations) {
    let chosen = unique(definitions);
    const notDeclared = chosen.filter((d) => !declarations.some((x) => overlaps(d, x)));
    if (notDeclared.length) chosen = notDeclared;
    const notHeaders = chosen.filter((d) => !HEADER.test(filePath(d)));
    return notHeaders.length ? notHeaders : chosen;
  }

  async function provided(command, uri, position) {
    try {
      return ((await vscode.commands.executeCommand(command, uri, position)) || []).map(place);
    } catch (_error) {
      return [];
    }
  }

  // The definitions of what is at ``position``; ``wait``: retry while none.
  async function definitionsAt(uri, position, wait) {
    const definitions = wait
      ? (await retry(() => vscode.commands.executeCommand(
        'vscode.executeDefinitionProvider', uri, position))).map(place)
      : await provided('vscode.executeDefinitionProvider', uri, position);
    if (!definitions.length) {
      return [];
    }
    const declarations = await provided('vscode.executeDeclarationProvider', uri, position);
    return definitionsOnly(definitions, declarations);
  }

  // Where ``symbol`` is in a workspace symbol's range (which may start before it).
  async function namePosition(location, symbol) {
    try {
      const doc = await vscode.workspace.openTextDocument(location.uri);
      const text = doc.getText(location.range);
      const found = text.search(new RegExp(`(?<!${WORD})${symbol}(?!${WORD})`, 'u'));
      if (found >= 0) {
        const before = text.slice(0, found).split('\n');
        const line = location.range.start.line + before.length - 1;
        const character = before.length > 1
          ? before[before.length - 1].length
          : location.range.start.character + found;
        return new vscode.Position(line, character);
      }
    } catch (_error) {
      // unreadable: its start
    }
    return location.range.start;
  }

  // Each symbol of that name, as its definition: a declaration and its
  // definition become one place; different symbols stay apart.
  async function resolved(symbol, symbols) {
    const places = [];
    for (const s of symbols.slice(0, MAX_CANDIDATES)) {
      const position = await namePosition(s.location, symbol);
      const definitions = await definitionsAt(s.location.uri, position, false);
      const own = { uri: s.location.uri, range: s.location.range, full: s.location.range };
      places.push({ symbol: s, place: definitions.length ? definitions[0] : own });
    }
    const seen = new Set();
    return places.filter((p) => !seen.has(key(p.place)) && seen.add(key(p.place)));
  }

  function show(uri, range) {
    const options = { preview: false };
    if (range) {
      options.selection = range;
    }
    return vscode.window.showTextDocument(uri, options);
  }

  function pick(symbol, found) {
    const items = found.map(({ symbol: s, place: p }) => ({
      label: s.name,
      description: s.containerName || '',
      detail: `${vscode.workspace.asRelativePath(p.uri)}:${p.range.start.line + 1}`,
      place: p,
    }));
    Promise.resolve(vscode.window.showQuickPick(items, { placeHolder: `Definitions of ${symbol}` }))
      .then((chosen) => chosen && show(chosen.place.uri, chosen.place.range))
      .catch(() => {});
  }

  return async function handle(request) {
    if (!request || typeof request !== 'object' || request.cmd !== 'definition') {
      return { ok: false, how: '', message: 'Unknown request.' };
    }
    const symbol = request.symbol;
    if (typeof symbol !== 'string' || !SYMBOL.test(symbol)) {
      return { ok: false, how: '', message: 'Not a symbol.' };
    }
    let uri = null;
    let position = null;
    if (request.file !== null && request.file !== undefined) {
      if (typeof request.file !== 'string' || !path.isAbsolute(request.file)
          || !inWorkspace(request.file)) {
        return { ok: false, how: '', message: 'Not a file of this workspace.' };
      }
      uri = vscode.Uri.file(request.file);
      if (Number.isInteger(request.line) && request.line >= 1) {
        const col = Number.isInteger(request.col) && request.col >= 1 ? request.col - 1 : 0;
        position = new vscode.Position(request.line - 1, col);
      }
    }

    // The symbol where RZGit found it: the definition provider there.
    if (position && Number.isInteger(request.col)) {
      try {
        const doc = await vscode.workspace.openTextDocument(uri);
        const word = doc.getWordRangeAtPosition(position);
        if (word && doc.getText(word) === symbol) {
          const found = await definitionsAt(uri, position, true);
          if (found.length) { // the first: never a list to choose from
            await show(found[0].uri, found[0].range);
            return { ok: true, how: 'definition', message: '' };
          }
        }
      } catch (_error) {
        // unreadable here: look for it by name
      }
    }

    // Otherwise by name, in the workspace's symbols.
    const symbols = await retry(async () => ((await vscode.commands.executeCommand(
      'vscode.executeWorkspaceSymbolProvider', symbol)) || []).filter(named(symbol)));
    const found = symbols.length ? await resolved(symbol, symbols) : [];
    if (found.length === 1) {
      await show(found[0].place.uri, found[0].place.range);
      return { ok: true, how: 'symbol', message: '' };
    }
    if (found.length > 1) { // different symbols of that name
      pick(symbol, found);
      return { ok: true, how: 'pick', message: `${found.length} definitions` };
    }
    if (uri) {
      try {
        await show(uri, position ? new vscode.Range(position, position) : null);
      } catch (_error) {
        // gone: the message says enough
      }
    }
    vscode.window.showInformationMessage(`RZGit: no definition of ${symbol} found.`);
    return { ok: true, how: 'none', message: '' };
  };
}

// Listen on the window's socket; one request per connection.
function serve(handle, target, env = process.env) {
  const file = socketPath(target, env);
  const folder = path.dirname(file);
  fs.mkdirSync(folder, { recursive: true, mode: 0o700 });
  fs.chmodSync(folder, 0o700);
  try {
    fs.unlinkSync(file); // a window that didn't close cleanly
  } catch (_error) {
    // there was none
  }
  const server = net.createServer((conn) => {
    let data = '';
    let done = false;
    conn.setEncoding('utf8');
    conn.on('error', () => {});
    conn.on('data', (chunk) => {
      if (done) {
        return;
      }
      data += chunk;
      if (data.length > MAX_REQUEST) {
        done = true;
        conn.destroy();
        return;
      }
      const end = data.indexOf('\n');
      if (end < 0) {
        return;
      }
      done = true;
      let request = null;
      try {
        request = JSON.parse(data.slice(0, end));
      } catch (_error) {
        request = null;
      }
      Promise.resolve()
        .then(() => handle(request))
        .catch((error) => ({ ok: false, how: '', message: String((error && error.message) || error) }))
        .then((reply) => conn.end(`${JSON.stringify(reply)}\n`));
    });
  });
  server.on('error', () => {});
  server.listen(file, () => {
    try {
      fs.chmodSync(file, 0o600);
    } catch (_error) {
      // closed already
    }
  });
  return {
    file,
    server,
    dispose() {
      server.close();
      try {
        fs.unlinkSync(file);
      } catch (_error) {
        // gone already
      }
    },
  };
}

function activate(context) {
  const vscode = require('vscode'); // eslint-disable-line global-require
  const target = workspaceTarget(vscode);
  if (target) {
    context.subscriptions.push(serve(createHandler(vscode), target));
  }
}

function deactivate() {}

module.exports = {
  activate, deactivate, createHandler, serve, socketFolder, socketPath, workspaceTarget,
};
