// The RZGit VS Code extension's tests, under plain Node with a stub `vscode`
// (RZGit spec §8, RZG-75). tests/test_vscode_extension.py runs them.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const extension = require('../../src/rzgit/ui/assets/vscode/extension.js');

const ROOT = '/work/repo';

class Position {
  constructor(line, character) { this.line = line; this.character = character; }
}
class Range {
  constructor(start, end) { this.start = start; this.end = end; }
}

// A stub of the vscode API: `files` maps a path to its lines; `definitions` and
// `symbols` are what the providers return, call by call (the last one repeats),
// or `definitions` is a function of (uri, position); `declarations` likewise.
function stub({ files = {}, definitions = [[]], declarations = () => [], symbols = [[]] } = {}) {
  const calls = { commands: [], shown: [], picks: [], messages: [] };
  const take = (list) => (list.length > 1 ? list.shift() : list[0]);
  const vscode = {
    Position,
    Range,
    Uri: { file: (p) => ({ fsPath: p, scheme: 'file', toString: () => p }) },
    workspace: {
      workspaceFolders: [{ uri: { fsPath: ROOT } }],
      workspaceFile: undefined,
      asRelativePath: (uri) => path.relative(ROOT, uri.fsPath),
      openTextDocument: async (uri) => {
        const lines = files[uri.fsPath];
        if (!lines) throw new Error('no such file');
        return {
          getWordRangeAtPosition(pos) {
            const text = lines[pos.line] || '';
            for (const m of text.matchAll(/\w+/g)) {
              if (m.index <= pos.character && pos.character <= m.index + m[0].length) {
                return new Range(new Position(pos.line, m.index),
                  new Position(pos.line, m.index + m[0].length));
              }
            }
            return undefined;
          },
          getText(range) {
            return lines[range.start.line].slice(range.start.character, range.end.character);
          },
        };
      },
    },
    commands: {
      executeCommand: async (name, ...args) => {
        calls.commands.push([name, ...args]);
        if (name === 'vscode.executeDefinitionProvider') {
          return typeof definitions === 'function' ? definitions(...args) : take(definitions);
        }
        if (name === 'vscode.executeDeclarationProvider') return declarations(...args);
        if (name === 'vscode.executeWorkspaceSymbolProvider') return take(symbols);
        return undefined;
      },
    },
    window: {
      showTextDocument: async (uri, options) => { calls.shown.push([uri.fsPath, options]); },
      showQuickPick: async (items, options) => { calls.picks.push([items, options]); return items[1]; },
      showInformationMessage: (text) => { calls.messages.push(text); },
    },
  };
  return { vscode, calls };
}

const FILE = `${ROOT}/a.py`;
const LINES = ['def helper():', '    return 1', 'x = helper()'];
const uriOf = (file) => ({ fsPath: `${ROOT}/${file}`, toString: () => `${ROOT}/${file}` });
const where = (line, file = 'b.py') => ({ uri: uriOf(file), range: new Range(new Position(line, 4), new Position(line, 10)) });
const fast = { retryMs: 30, stepMs: 5 };
const ask = (extra) => ({ cmd: 'definition', symbol: 'helper', file: FILE, line: 3, col: 5, ...extra });
const names = (calls) => calls.commands.map((c) => c[0]);

test('the definition provider at the position, when the word there is the symbol', async () => {
  const { vscode, calls } = stub({ files: { [FILE]: LINES }, definitions: [[where(0)]] });
  const reply = await extension.createHandler(vscode, fast)(ask());
  assert.deepEqual(reply, { ok: true, how: 'definition', message: '' });
  assert.equal(calls.commands[0][1].fsPath, FILE);
  assert.deepEqual(calls.commands[0][2], new Position(2, 4));
  assert.deepEqual(names(calls), ['vscode.executeDefinitionProvider', 'vscode.executeDeclarationProvider']);
  assert.deepEqual(calls.shown, [[`${ROOT}/b.py`, { preview: false, selection: where(0).range }]]);
});

test('always the definition: never a declaration or a list to choose from', async () => {
  // A C++ function: declared in a header, defined in a .cpp; the provider gives both.
  const decl = where(3, 'x.h');
  const def = where(40, 'x.cpp');
  const { vscode, calls } = stub({
    files: { [FILE]: LINES }, definitions: [[decl, def]], declarations: () => [decl],
  });
  assert.equal((await extension.createHandler(vscode, fast)(ask())).how, 'definition');
  assert.deepEqual(calls.shown, [[`${ROOT}/x.cpp`, { preview: false, selection: def.range }]]);
  assert.deepEqual(calls.picks, []);
});

test('not a declaration, outside a header too', async () => {
  // A forward declaration in another source file: only the declaration provider tells.
  const decl = where(3, 'y.cpp');
  const def = where(40, 'x.cpp');
  const { vscode, calls } = stub({
    files: { [FILE]: LINES }, definitions: [[decl, def]], declarations: () => [decl],
  });
  await extension.createHandler(vscode, fast)(ask());
  assert.deepEqual(calls.shown, [[`${ROOT}/x.cpp`, { preview: false, selection: def.range }]]);
});

test('without a declaration provider, not the header', async () => {
  const link = { targetUri: uriOf('x.cpp'), targetRange: where(39, 'x.cpp').range, targetSelectionRange: where(40, 'x.cpp').range };
  const { vscode, calls } = stub({ files: { [FILE]: LINES }, definitions: [[where(3, 'x.hpp'), link]] });
  await extension.createHandler(vscode, fast)(ask());
  assert.deepEqual(calls.shown, [[`${ROOT}/x.cpp`, { preview: false, selection: link.targetSelectionRange }]]);
});

test('it retries while the language server starts', async () => {
  const { vscode, calls } = stub({ files: { [FILE]: LINES }, definitions: [[], [], [where(0)]] });
  const reply = await extension.createHandler(vscode, { retryMs: 1000, stepMs: 1 })(ask());
  assert.equal(reply.how, 'definition');
  assert.equal(names(calls).filter((n) => n === 'vscode.executeDefinitionProvider').length, 3);
});

test('a moved word: the workspace symbol of that name', async () => {
  const { vscode, calls } = stub({
    files: { [FILE]: LINES },
    symbols: [[{ name: 'helper', location: where(7) }, { name: 'helpers', location: where(9) }]],
  });
  const reply = await extension.createHandler(vscode, fast)(ask({ line: 2 }));
  assert.equal(reply.how, 'symbol');
  assert.equal(names(calls)[0], 'vscode.executeWorkspaceSymbolProvider'); // not at the position
  assert.deepEqual(calls.shown, [[`${ROOT}/b.py`, { preview: false, selection: where(7).range }]]);
});

test('no column or no file: by name only', async () => {
  for (const request of [ask({ col: null }), ask({ file: null, line: null, col: null })]) {
    const { vscode, calls } = stub({ files: { [FILE]: LINES }, symbols: [[{ name: 'helper()', location: where(1) }]] });
    assert.equal((await extension.createHandler(vscode, fast)(request)).how, 'symbol');
    assert.equal(names(calls)[0], 'vscode.executeWorkspaceSymbolProvider');
  }
});

test('by name: a declaration and its definition are one place', async () => {
  const decl = { name: 'helper', location: where(3, 'x.h') };
  const def = { name: 'helper', location: where(40, 'x.cpp') };
  const { vscode, calls } = stub({
    files: { [`${ROOT}/x.h`]: ['', '', '', 'int helper();'], [`${ROOT}/x.cpp`]: [] },
    symbols: [[decl, def]],
    // At the declaration's name (col 4), its definition; elsewhere nothing.
    definitions: (uri, pos) => (uri.fsPath.endsWith('x.h') && pos.character === 4 ? [def.location] : []),
  });
  assert.equal((await extension.createHandler(vscode, fast)(ask({ file: null }))).how, 'symbol');
  assert.deepEqual(calls.shown, [[`${ROOT}/x.cpp`, { preview: false, selection: def.location.range }]]);
  assert.deepEqual(calls.picks, []);
});

test('several: a QuickPick', async () => {
  const { vscode, calls } = stub({
    symbols: [[{ name: 'helper', location: where(1), containerName: 'A' },
      { name: 'helper', location: where(2), containerName: 'B' }]],
  });
  const reply = await extension.createHandler(vscode, fast)(ask({ file: null }));
  assert.deepEqual(reply, { ok: true, how: 'pick', message: '2 definitions' });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.picks.length, 1);
  assert.deepEqual(calls.picks[0][0].map((i) => [i.label, i.description, i.detail]),
    [['helper', 'A', 'b.py:2'], ['helper', 'B', 'b.py:3']]);
  assert.deepEqual(calls.shown, [[`${ROOT}/b.py`, { preview: false, selection: where(2).range }]]);
});

test('nothing found: a message, and the file at the position', async () => {
  const { vscode, calls } = stub({ files: { [FILE]: LINES } });
  const reply = await extension.createHandler(vscode, fast)(ask());
  assert.deepEqual(reply, { ok: true, how: 'none', message: '' });
  assert.deepEqual(calls.messages, ['RZGit: no definition of helper found.']);
  assert.equal(calls.shown[0][0], FILE);
});

test('it accepts only a definition request, for a file of the workspace', async () => {
  const { vscode, calls } = stub({ files: { [FILE]: LINES } });
  const handle = extension.createHandler(vscode, fast);
  for (const bad of [null, 'x', {}, { cmd: 'run', symbol: 'helper' },
    ask({ symbol: 'rm -rf' }), ask({ symbol: '' }), ask({ symbol: 7 }),
    ask({ file: '/etc/passwd' }), ask({ file: `${ROOT}/../other/a.py` }),
    ask({ file: 'a.py' }), ask({ file: ROOT })]) {
    const reply = await handle(bad);
    assert.equal(reply.ok, false, JSON.stringify(bad));
  }
  assert.deepEqual(calls.commands, []);
});

test('the window is named by its workspace file, else its first folder', () => {
  const { vscode } = stub();
  assert.equal(extension.workspaceTarget(vscode), ROOT);
  vscode.workspace.workspaceFile = { scheme: 'file', fsPath: `${ROOT}/r.code-workspace` };
  assert.equal(extension.workspaceTarget(vscode), `${ROOT}/r.code-workspace`);
  vscode.workspace.workspaceFile = { scheme: 'untitled', fsPath: 'Untitled-1' };
  assert.equal(extension.workspaceTarget(vscode), ROOT);
  vscode.workspace.workspaceFolders = undefined;
  assert.equal(extension.workspaceTarget(vscode), null);
});

function send(file, data) {
  return new Promise((resolve, reject) => {
    const conn = net.createConnection(file, () => conn.write(data));
    let reply = '';
    conn.setEncoding('utf8');
    conn.on('data', (chunk) => { reply += chunk; });
    conn.on('end', () => resolve(reply));
    conn.on('error', reject);
  });
}

test('the socket: one request and one reply per connection, only for its user', async () => {
  const runtime = fs.mkdtempSync(path.join(os.tmpdir(), 'rzgit-ext-test-'));
  const env = { XDG_RUNTIME_DIR: runtime };
  const file = extension.socketPath(ROOT, env);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, 'stale'); // a window that didn't close cleanly
  const served = extension.serve(async (request) => ({ ok: true, how: 'none', message: request.symbol }), ROOT, env);
  await new Promise((resolve) => served.server.once('listening', resolve));
  try {
    assert.equal(served.file, file);
    assert.equal(fs.statSync(path.dirname(file)).mode & 0o777, 0o700);
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
    assert.deepEqual(JSON.parse(await send(file, '{"cmd":"definition","symbol":"a"}\n{"x":1}\n')),
      { ok: true, how: 'none', message: 'a' });
    // Not JSON: the handler gets null, and its error is the reply.
    assert.equal(JSON.parse(await send(file, 'not json\n')).ok, false);
  } finally {
    served.dispose();
  }
  assert.equal(fs.existsSync(file), false);
  fs.rmdirSync(path.dirname(file));
  fs.rmdirSync(runtime);
});
