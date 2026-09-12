"""Execute desktop helper logic without starting or closing the real app."""
import subprocess
from pathlib import Path


def test_desktop_quits_only_after_helper_process_starts():
    root = Path(__file__).resolve().parents[1]
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {EventEmitter} = require('node:events');
const text = fs.readFileSync('desktop-electron/main.js', 'utf8');
const source = text.slice(text.indexOf('function startPortableUpdateHelper('), text.indexOf('function installHandlers()'));
(async () => {
  for (const mode of ['failure', 'success', 'missing']) {
    let quit = 0;
    let spawned = 0;
    let argumentsSent;
    const context = {
      ROOT: 'C:/Raven', WINDOWS_POWERSHELL: 'powershell.exe', WINDOWS_POWERSHELL_ENV: {},
      path: require('node:path'), fs: {existsSync: () => mode !== 'missing'}, process: {pid: 123},
      setImmediate: callback => callback(), app: {quit: () => quit++},
      spawn: (_file, args) => {
        spawned++; argumentsSent=args;
        const child = new EventEmitter(); child.unref=()=>{};
        queueMicrotask(()=>mode==='failure' ? child.emit('error',new Error('spawn failed')) : child.emit('spawn'));
        return child;
      }
    };
    vm.createContext(context); vm.runInContext(source, context);
    if(mode==='success') {
      assert.equal(await context.startPortableUpdateHelper(['-Rollback']), true);
      assert.equal(quit, 1);
      assert(argumentsSent.includes('-Rollback'));
    } else {
      await assert.rejects(context.startPortableUpdateHelper());
      assert.equal(quit, 0);
      if(mode==='missing') assert.equal(spawned, 0);
    }
  }
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr


def test_portable_checks_respect_offline_and_share_one_operation():
    root = Path(__file__).resolve().parents[1]
    script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const text = fs.readFileSync('desktop-electron/main.js', 'utf8');
const source = text.slice(text.indexOf('let portableUpdatePromise ='), text.indexOf('function configureAutoUpdates()'));
(async () => {
  let settings = {offline_mode:true};
  let calls = 0;
  let finish;
  const context = {ROOT:'C:/Raven', RUNTIME:'C:/Raven/runtime', path:require('node:path'),
    fs:{existsSync:()=>true, readFileSync:()=>JSON.stringify(settings)},
    app:{getVersion:()=> '1.2.0'}, updateState:{status:'idle'},
    publishUpdateState:state=>state,
    runPortableUpdater:()=>{calls++; return new Promise(resolve=>{finish=resolve})}
  };
  vm.createContext(context); vm.runInContext(source, context);
  assert.equal((await context.checkPortableUpdates()).status,'offline');
  assert.equal(calls,0);
  settings={safe_mode:true};
  assert.equal((await context.checkPortableUpdates()).status,'offline');
  assert.equal(calls,0);
  settings={};
  const first=context.checkPortableUpdates();
  const second=context.checkPortableUpdates();
  assert.equal(first,second);
  assert.equal(calls,1);
  settings={offline_mode:true};
  finish({status:'available',availableVersion:'1.3.0'});
  assert.equal((await first).status,'offline');
  assert.equal(calls,1);
  context.fs.readFileSync=()=>'{broken';
  await assert.rejects(context.checkPortableUpdates());
  assert.equal(calls,1);
})().catch(error=>{console.error(error);process.exitCode=1});
'''
    result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
