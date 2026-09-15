import subprocess
from pathlib import Path


def test_desktop_offline_policy_blocks_remote_browser_and_network_commands() -> None:
    root = Path(__file__).resolve().parents[1]
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const text = fs.readFileSync('desktop-electron/main.js', 'utf8');
const source = text.slice(text.indexOf('function desktopOfflineEnabled()'), text.indexOf('function git(args)'));
let settings = {offline_mode:true};
const context = {
  URL, RUNTIME:'C:/Raven/runtime', path:require('node:path'),
  fs:{existsSync:()=>true,readFileSync:()=>JSON.stringify(settings)},
  portableUpdatesOffline:()=>settings.offline_mode === true || settings.safe_mode === true
};
vm.createContext(context); vm.runInContext(source, context);
assert.equal(context.safeUrl(''), 'about:blank');
assert.equal(context.safeUrl('http://127.0.0.1:8126/health'), 'http://127.0.0.1:8126/health');
assert.throws(()=>context.safeUrl('https://github.com/'), /offline/);
assert.equal(context.shouldBlockNetworkRequest('https://example.com/data'), true);
assert.equal(context.shouldBlockNetworkRequest('http://localhost:8126/health'), false);
assert.equal(context.isOfflineTerminalCommand('Get-ChildItem .'), false);
assert.equal(context.isOfflineTerminalCommand('Invoke-WebRequest https://example.com'), true);
assert.equal(context.isOfflineTerminalCommand('git fetch origin'), true);
settings={};
assert.equal(context.safeUrl('github.com'), 'https://github.com/');
''';
    result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_browser_content_stays_sandboxed_when_portable_hud_uses_preload() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "desktop-electron" / "main.js").read_text(encoding="utf-8")
    assert "WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false" in source
    assert "preload: path.join(__dirname, 'preload.js'), sandbox: false, contextIsolation: true, nodeIntegration: false" in source
