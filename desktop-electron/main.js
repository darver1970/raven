const { app, BrowserWindow, WebContentsView, ipcMain, session, shell, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync, spawn } = require('node:child_process');
const { autoUpdater } = require('electron-updater');
const { resolveExecutableContext } = require('./install-paths');

const WINDOWS_POWERSHELL = path.join(
  process.env.SystemRoot || 'C:\\Windows',
  'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
);
const WINDOWS_POWERSHELL_ENV = { ...process.env };
delete WINDOWS_POWERSHELL_ENV.PSModulePath;
const INSTALL_CONFIG = path.join(process.env.LOCALAPPDATA || path.dirname(process.execPath), 'Raven', 'install-path.txt');
let SAVED_ROOT = '';
try { SAVED_ROOT = fs.readFileSync(INSTALL_CONFIG, 'utf8').trim(); } catch {}
const executableContext = resolveExecutableContext({
  isPackaged: app.isPackaged,
  execPath: process.execPath,
  portableExecutableDir: process.env.PORTABLE_EXECUTABLE_DIR,
  savedRoot: SAVED_ROOT
});
const {
  executableDirectory: EXECUTABLE_DIRECTORY,
  executableRoot: EXECUTABLE_ROOT,
  installedRoot: INSTALLED_ROOT,
  isNsisInstall: IS_NSIS_INSTALL,
  isPortableBuild: IS_PORTABLE_BUILD,
  savedRootIsValid: SAVED_ROOT_IS_VALID
} = executableContext;
const INSTALL_MARKER = path.join(INSTALLED_ROOT, '.raven-installing');
const BUNDLED_PROJECT = app.isPackaged ? path.join(process.resourcesPath, 'raven-project') : '';
let bootstrapInProgress = false;
let launcherDelegationInProgress = false;
let shutdownCleanupStarted = false;

if (IS_NSIS_INSTALL && !process.env.RAVEN_HOME && fs.existsSync(path.join(BUNDLED_PROJECT, 'install.ps1')) && (!fs.existsSync(path.join(INSTALLED_ROOT, 'raven_control.py')) || fs.existsSync(INSTALL_MARKER))) {
  bootstrapInProgress = true;
  const installer = path.join(BUNDLED_PROJECT, 'install.ps1');
  const bootstrapLogDirectory = path.join(process.env.LOCALAPPDATA || INSTALLED_ROOT, 'Raven', 'logs');
  const bootstrapLog = path.join(bootstrapLogDirectory, 'bootstrap.log');
  fs.mkdirSync(bootstrapLogDirectory, { recursive: true });
  fs.appendFileSync(
    bootstrapLog,
    `${new Date().toISOString()} installer=${installer} target=${INSTALLED_ROOT}\n`,
    'utf8'
  );
  const quotePowerShell = value => `'${String(value).replace(/'/g, "''")}'`;
  const installerArguments = [
    `& ${quotePowerShell(installer)}`,
    `-InstallPath ${quotePowerShell(INSTALLED_ROOT)}`,
    '-NoLaunch'
  ];
  if (process.env.RAVEN_INSTALL_SKIP_MODEL === '1') installerArguments.push('-SkipModel');
  const installerInvocation = [
    "$ErrorActionPreference = 'Stop'",
    ...(process.env.RAVEN_INSTALL_NONINTERACTIVE ? ["$env:RAVEN_INSTALL_NONINTERACTIVE = '1'"] : []),
    installerArguments.join(' '),
    'exit $LASTEXITCODE'
  ].join('; ');
  const encodedInstallerInvocation = Buffer.from(installerInvocation, 'utf16le').toString('base64');
  const elevatedCommand = [
    `$installerArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', ${quotePowerShell(encodedInstallerInvocation)})`,
    `try { $installerProcess = Start-Process -FilePath ${quotePowerShell(WINDOWS_POWERSHELL)} -Verb RunAs -ArgumentList $installerArguments -PassThru; while (-not $installerProcess.HasExited) { Start-Sleep -Milliseconds 500; $installerProcess.Refresh() }; exit $installerProcess.ExitCode } catch { exit 1223 }`
  ].join('; ');
  const bootstrapArguments = process.env.RAVEN_INSTALL_NONINTERACTIVE === '1'
    ? ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', encodedInstallerInvocation]
    : ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', elevatedCommand];
  const child = spawn(WINDOWS_POWERSHELL, bootstrapArguments, {
    cwd: BUNDLED_PROJECT,
    detached: false,
    windowsHide: true,
    stdio: 'ignore',
    env: WINDOWS_POWERSHELL_ENV
  });
  child.once('spawn', () => {
    fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} installer_process_started pid=${child.pid}\n`, 'utf8');
  });
  child.once('close', code => {
    fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} installer_process_finished code=${code}\n`, 'utf8');
    if (code !== 0) {
      app.whenReady().then(() => {
        dialog.showErrorBox(
          code === 1223 ? 'Instalace Raven byla zrušena' : 'Instalace Raven selhala',
          `Instalace nebyla dokončena. Podrobnosti jsou v ${bootstrapLog}`
        );
        app.exit(1);
      });
      return;
    }
    const launcher = path.join(INSTALLED_ROOT, 'spustit-raven.ps1');
    if (!fs.existsSync(launcher)) {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_missing=${launcher}\n`, 'utf8');
      app.whenReady().then(() => dialog.showErrorBox(
        'Raven byl nainstalován neúplně',
        `Spouštěcí soubor nebyl nalezen. Podrobnosti jsou v ${bootstrapLog}`
      )).finally(() => app.exit(1));
      return;
    }
    const launcherProcess = spawn(
      WINDOWS_POWERSHELL,
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', launcher, '-NoDesktop'],
      { cwd: INSTALLED_ROOT, detached: false, windowsHide: true, stdio: 'ignore', env: WINDOWS_POWERSHELL_ENV }
    );
    launcherProcess.once('spawn', () => {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_started pid=${launcherProcess.pid}\n`, 'utf8');
    });
    launcherProcess.once('close', launcherCode => {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_finished code=${launcherCode}\n`, 'utf8');
      if (launcherCode === 0) {
        bootstrapInProgress = false;
        startDesktopApplication();
        return;
      }
      app.whenReady().then(() => dialog.showErrorBox(
        'Raven je nainstalován, ale spuštění selhalo',
        `Spouštěcí skript skončil kódem ${launcherCode}. Podrobnosti jsou v ${bootstrapLog}`
      )).finally(() => app.exit(1));
    });
    launcherProcess.once('error', error => {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_error=${error.stack || error}\n`, 'utf8');
      app.whenReady().then(() => dialog.showErrorBox(
        'Raven je nainstalován, ale nelze jej spustit',
        `Použijte zástupce Raven 1.2 na ploše. Podrobnosti jsou v ${bootstrapLog}`
      )).finally(() => app.exit(1));
    });
  });
  child.once('error', error => {
    fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} installer_process_error=${error.stack || error}\n`, 'utf8');
    app.whenReady().then(() => dialog.showErrorBox(
      'Instalaci Raven nelze spustit',
      `PowerShell instalátor se nepodařilo spustit. Podrobnosti jsou v ${bootstrapLog}`
    )).finally(() => app.exit(1));
  });
}

if (
  app.isPackaged
  && !bootstrapInProgress
  && process.env.RAVEN_SERVICES_READY !== '1'
  && fs.existsSync(path.join(INSTALLED_ROOT, 'raven_control.py'))
  && fs.existsSync(path.join(INSTALLED_ROOT, 'spustit-raven.ps1'))
) {
  launcherDelegationInProgress = true;
  const launcherLogDirectory = path.join(process.env.LOCALAPPDATA || INSTALLED_ROOT, 'Raven', 'logs');
  const launcherWrapperLog = path.join(launcherLogDirectory, 'wrapper.log');
  fs.mkdirSync(launcherLogDirectory, { recursive: true });
  const launcher = path.join(INSTALLED_ROOT, 'spustit-raven.ps1');
  const launcherProcess = spawn(
    WINDOWS_POWERSHELL,
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', launcher, '-NoDesktop'],
    { cwd: INSTALLED_ROOT, detached: false, windowsHide: true, stdio: 'ignore', env: WINDOWS_POWERSHELL_ENV }
  );
  launcherProcess.once('spawn', () => {
    fs.appendFileSync(launcherWrapperLog, `${new Date().toISOString()} launcher_started pid=${launcherProcess.pid}\n`, 'utf8');
  });
  launcherProcess.once('close', launcherCode => {
    fs.appendFileSync(launcherWrapperLog, `${new Date().toISOString()} launcher_finished code=${launcherCode}\n`, 'utf8');
    if (launcherCode === 0) {
      launcherDelegationInProgress = false;
      startDesktopApplication();
      return;
    }
    app.whenReady().then(() => dialog.showErrorBox(
      'Raven se nepodařilo spustit',
      `Spouštěcí skript skončil kódem ${launcherCode}. Podrobnosti jsou v ${launcherWrapperLog}`
    )).finally(() => app.exit(1));
  });
  launcherProcess.once('error', error => {
    fs.appendFileSync(launcherWrapperLog, `${new Date().toISOString()} launcher_error=${error.stack || error}\n`, 'utf8');
    app.whenReady().then(() => dialog.showErrorBox(
      'Raven nelze spustit',
      `Spouštěcí skript selhal. Podrobnosti jsou v ${launcherWrapperLog}`
    )).finally(() => app.exit(1));
  });
}

const ROOT = bootstrapInProgress
  ? INSTALLED_ROOT
  : process.env.RAVEN_HOME
  ? path.resolve(process.env.RAVEN_HOME)
  : app.isPackaged && fs.existsSync(path.join(INSTALLED_ROOT, 'raven_control.py'))
    ? INSTALLED_ROOT
    : app.isPackaged && IS_PORTABLE_BUILD
      ? EXECUTABLE_ROOT
      : path.resolve(__dirname, '..');
const RUNTIME = path.join(ROOT, 'runtime');
const ELECTRON_PROFILE_OVERRIDE = String(process.env.RAVEN_ELECTRON_PROFILE || '').trim();
const PROFILE = bootstrapInProgress
  ? path.join(process.env.LOCALAPPDATA || INSTALLED_ROOT, 'Raven', 'bootstrap-profile')
  : ELECTRON_PROFILE_OVERRIDE
  ? path.resolve(ELECTRON_PROFILE_OVERRIDE)
  : path.join(RUNTIME, 'electron-profile');
const QUARANTINE = path.join(RUNTIME, 'quarantine');
const SNAPSHOTS = path.join(RUNTIME, 'snapshots');
const TRASH = path.join(RUNTIME, 'trash');
const ARTIFACTS = path.join(RUNTIME, 'artifacts');
const BROWSER_STATE_PATH = path.join(RUNTIME, 'browser-tabs.json');
const HUD_URL = 'http://127.0.0.1:5174/?desktop=electron&hud_version=1.2&asset_revision=cortex-1';
const TEXT_EXTENSIONS = new Set(['.css', '.html', '.js', '.json', '.md', '.ps1', '.py', '.txt', '.yml', '.yaml', '.toml']);
const HIDDEN = new Set(['.git', '.venv', '__pycache__', 'node_modules', 'pyinstaller-build', 'pyinstaller-spec']);
let mainWindow;
let desktopApplicationStarted = false;
let updaterConfigured = false;
let updateState = { supported: false, status: 'idle', version: app.getVersion(), mode: 'none', message: 'Aktualizace zatím není nakonfigurovaná.' };
let browserVisible = false;
let browserBounds = { x: 0, y: 0, width: 0, height: 0 };
let activeTabId = '';
const tabs = new Map();
const browserEvents = [];
const terminals = new Map();
let terminalSequence = 0;

if (!bootstrapInProgress) fs.mkdirSync(PROFILE, { recursive: true });
if (!bootstrapInProgress) {
  fs.mkdirSync(QUARANTINE, { recursive: true });
  fs.mkdirSync(SNAPSHOTS, { recursive: true });
  fs.mkdirSync(TRASH, { recursive: true });
  fs.mkdirSync(ARTIFACTS, { recursive: true });
}
if (!bootstrapInProgress) app.setPath('userData', PROFILE);
app.setName('Raven 1.2');
app.setAppUserModelId('cz.raven.desktop');
const MAIN_LOG = path.join(RUNTIME, 'electron-main.log');
const writeLog = value => { try { fs.appendFileSync(MAIN_LOG, `${new Date().toISOString()} ${value}\n`, 'utf8'); } catch {} };
process.on('uncaughtException', error => writeLog(`uncaughtException ${error.stack || error}`));
process.on('unhandledRejection', error => writeLog(`unhandledRejection ${error?.stack || error}`));
writeLog(`start packaged=${app.isPackaged} root=${ROOT}`);

function publishUpdateState(values = {}) {
  updateState = { ...updateState, ...values, rollbackAvailable: fs.existsSync(path.join(RUNTIME, 'updates', 'last-result.json')), updatedAt: new Date().toISOString() };
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send('update:status', updateState);
  }
  return updateState;
}

function portablePython() {
  const base = path.join(RUNTIME, 'python');
  if (!fs.existsSync(base)) return '';
  for (const entry of fs.readdirSync(base, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const candidate = path.join(base, entry.name, 'python.exe');
    if (fs.existsSync(candidate)) return candidate;
  }
  return '';
}

function runPortableUpdater(args, timeout = 180000) {
  return new Promise((resolve, reject) => {
    const python = portablePython();
    const updater = path.join(ROOT, 'raven_updater.py');
    if (!python || !fs.existsSync(updater)) return reject(new Error('Portable aktualizátor nebo vlastní Python chybí.'));
    const child = spawn(python, [updater, ...args], { cwd: ROOT, windowsHide: true, env: WINDOWS_POWERSHELL_ENV });
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Portable aktualizátor překročil časový limit.')); }, timeout);
    child.stdout.on('data', value => { stdout += String(value); });
    child.stderr.on('data', value => { stderr += String(value); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('close', code => {
      clearTimeout(timer);
      const line = stdout.trim().split(/\r?\n/).filter(Boolean).at(-1) || '';
      try {
        const result = JSON.parse(line);
        if (code !== 0 || result.status === 'error') throw new Error(result.message || stderr || `Aktualizátor skončil kódem ${code}.`);
        resolve(result);
      } catch (error) {
        reject(error instanceof SyntaxError ? new Error(stderr || stdout || 'Aktualizátor nevrátil platný výsledek.') : error);
      }
    });
  });
}

let portableUpdatePromise = null;

function portableUpdatesOffline() {
  const settingsFile = path.join(RUNTIME, 'raven-1.2-settings.json');
  if (!fs.existsSync(settingsFile)) return false;
  const settings = JSON.parse(fs.readFileSync(settingsFile, 'utf8').replace(/^\uFEFF/, ''));
  return settings.offline_mode === true || settings.safe_mode === true;
}

function checkPortableUpdates() {
  if (portableUpdatePromise) return portableUpdatePromise;
  if (updateState.status === 'installing') return Promise.resolve(updateState);
  portableUpdatePromise = performPortableUpdateCheck().finally(() => { portableUpdatePromise = null; });
  return portableUpdatePromise;
}

async function performPortableUpdateCheck() {
  if (portableUpdatesOffline()) return publishUpdateState({ supported: true, mode: 'portable', status: 'offline', message: 'Kontrola aktualizací je v offline nebo bezpečném režimu vypnutá.' });
  publishUpdateState({ supported: true, mode: 'portable', status: 'checking', message: 'Kontroluji portable vydání na GitHubu…' });
  const result = await runPortableUpdater(['check', '--root', ROOT, '--current', app.getVersion()], 60000);
  if (result.status !== 'available') return publishUpdateState({ ...result, mode: 'portable' });
  if (portableUpdatesOffline()) return publishUpdateState({ status: 'offline', message: 'Režim se změnil na offline; aktualizace se nestáhne.' });
  const updateDirectory = path.join(RUNTIME, 'updates');
  fs.mkdirSync(updateDirectory, { recursive: true });
  const checkState = path.join(updateDirectory, 'check.json');
  fs.writeFileSync(checkState, JSON.stringify(result, null, 2), 'utf8');
  publishUpdateState({ ...result, mode: 'portable', status: 'downloading', message: `Stahuji a ověřuji Raven ${result.availableVersion}…` });
  const prepared = await runPortableUpdater(['prepare', '--root', ROOT, '--state', checkState], 900000);
  return publishUpdateState({ supported: true, mode: 'portable', status: 'ready', version: app.getVersion(), availableVersion: prepared.version, stage: prepared.stage, message: `Raven ${prepared.version} je ověřený a připravený k aktualizaci.` });
}

function configureAutoUpdates() {
  if (updaterConfigured) return;
  updaterConfigured = true;
  const nsisSupported = Boolean(app.isPackaged && IS_NSIS_INSTALL && !process.env.PORTABLE_EXECUTABLE_FILE);
  const portableSupported = Boolean(app.isPackaged && !IS_NSIS_INSTALL && portablePython() && fs.existsSync(path.join(ROOT, 'raven_updater.py')));
  if (portableSupported) {
    publishUpdateState({ supported: true, mode: 'portable', status: 'idle', message: 'Portable aktualizace je připravená.' });
    setTimeout(() => checkPortableUpdates().catch(error => {
      writeLog(`portable updater error ${error?.stack || error}`);
      publishUpdateState({ supported: true, mode: 'portable', status: 'error', message: `Kontrola portable aktualizace selhala: ${error?.message || error}` });
    }), 8000);
    return;
  }
  const supported = nsisSupported;
  publishUpdateState({ supported, mode: supported ? 'nsis' : 'none', status: supported ? 'checking' : 'unsupported', message: supported ? 'Kontroluji GitHub Releases…' : 'V této kopii není aktualizátor dostupný.' });
  if (!supported) return;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = false;
  autoUpdater.on('checking-for-update', () => publishUpdateState({ status: 'checking', message: 'Kontroluji GitHub Releases…' }));
  autoUpdater.on('update-available', info => publishUpdateState({ status: 'downloading', availableVersion: info.version, message: `Stahuji Raven ${info.version}…` }));
  autoUpdater.on('update-not-available', info => publishUpdateState({ status: 'current', availableVersion: info.version, message: 'Používáte nejnovější vydanou verzi.' }));
  autoUpdater.on('download-progress', progress => publishUpdateState({ status: 'downloading', percent: Math.round(progress.percent), message: `Stahuji aktualizaci · ${Math.round(progress.percent)} %` }));
  autoUpdater.on('update-downloaded', info => publishUpdateState({ status: 'ready', availableVersion: info.version, message: `Raven ${info.version} je připraven k instalaci.` }));
  autoUpdater.on('error', error => {
    writeLog(`updater error ${error?.stack || error}`);
    publishUpdateState({ status: 'error', message: `Kontrola aktualizace selhala: ${error?.message || error}` });
  });
  setTimeout(() => autoUpdater.checkForUpdates().catch(error => publishUpdateState({ status: 'error', message: `Kontrola aktualizace selhala: ${error.message}` })), 8000);
}

function stopRavenServices() {
  if (shutdownCleanupStarted || bootstrapInProgress || launcherDelegationInProgress) return;
  shutdownCleanupStarted = true;
  closeAllTerminals();
  const stopScript = path.join(ROOT, 'stop-raven.ps1');
  if (!fs.existsSync(stopScript)) {
    writeLog(`shutdown cleanup skipped missing=${stopScript}`);
    return;
  }
  try {
    execFileSync(
      WINDOWS_POWERSHELL,
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', stopScript, '-InstallRoot', ROOT, '-ExcludeProcessId', String(process.pid)],
      { cwd: ROOT, timeout: 30000, windowsHide: true, stdio: 'ignore', env: WINDOWS_POWERSHELL_ENV }
    );
    writeLog('shutdown cleanup passed');
  } catch (error) {
    writeLog(`shutdown cleanup warning ${error?.message || error}`);
  }
}

function terminateOwnedTree(child) {
  if (!child || !Number.isInteger(child.pid) || child.pid <= 0) return;
  try {
    spawn('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], {
      windowsHide: true,
      detached: false,
      stdio: 'ignore'
    }).unref();
  } catch (error) {
    writeLog(`terminal cleanup warning pid=${child.pid} ${error?.message || error}`);
  }
}

function terminalPayload() {
  return {
    terminals: [...terminals.values()].map(item => ({
      id: item.id,
      title: item.title,
      cwd: item.cwd,
      running: item.running,
      busy: Boolean(item.busy),
      exitCode: item.exitCode
    }))
  };
}

function emitTerminal(channel, payload) {
  for (const window of BrowserWindow.getAllWindows()) {
    if (!window.isDestroyed()) window.webContents.send(channel, payload);
  }
}

function createTerminal(cwdValue = ROOT) {
  const cwd = safeComputerPath(cwdValue) || ROOT;
  if (!fs.existsSync(cwd) || !fs.statSync(cwd).isDirectory()) throw new Error('Pracovní složka terminálu neexistuje.');
  const id = `terminal-${Date.now()}-${++terminalSequence}`;
  const entry = { id, title: `PowerShell ${terminalSequence}`, cwd, child: null, running: true, busy: false, exitCode: null };
  terminals.set(id, entry);
  setImmediate(() => emitTerminal('terminal:data', { id, stream: 'stdout', data: `Raven terminal · ${cwd}\r\nPS ${cwd}> ` }));
  return terminalPayload();
}

function closeTerminal(id) {
  const entry = terminals.get(String(id));
  if (!entry) return terminalPayload();
  if (entry.child) terminateOwnedTree(entry.child);
  entry.running = false;
  terminals.delete(entry.id);
  emitTerminal('terminal:exit', { id: entry.id, exitCode: entry.exitCode });
  return terminalPayload();
}

function closeAllTerminals() {
  for (const entry of terminals.values()) {
    if (entry.child) terminateOwnedTree(entry.child);
    entry.running = false;
  }
  terminals.clear();
}
if (!bootstrapInProgress) {
  const singleInstance = app.requestSingleInstanceLock();
  if (!singleInstance) app.quit();
  app.on('second-instance', () => { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); } });
}

function safeProjectPath(relative = '') {
  const resolved = path.resolve(ROOT, String(relative || ''));
  const rel = path.relative(ROOT, resolved);
  if (rel.startsWith('..') || path.isAbsolute(rel)) throw new Error('Cesta je mimo projekt Ravenu.');
  return resolved;
}

function safeComputerPath(value) {
  const text = String(value || '').trim();
  if (!text || text === '::drives') return null;
  if (text.includes('\0')) throw new Error('Cesta obsahuje nepovolený znak.');
  return path.resolve(text);
}

function requireDesktopPermission(value, action) {
  const mode = String(value?.permissionMode || 'denied');
  if (!['full', 'confirm', 'denied'].includes(mode)) throw new Error('Neplatná úroveň oprávnění.');
  if (mode === 'denied') throw new Error(`${action} je v režimu Zakázáno vypnuté.`);
  if (mode === 'confirm' && value?.confirmed !== true) throw new Error(`${action} vyžaduje potvrzení.`);
}

function normalizedFileName(value) {
  const name = String(value || '').trim();
  if (!name || name === '.' || name === '..' || /[<>:"/\\|?*\0]/.test(name)) throw new Error('Název souboru nebo složky není platný.');
  return name.slice(0, 220);
}

function isFilesystemRoot(value) {
  const resolved = path.resolve(String(value || ''));
  return resolved.toLowerCase() === path.parse(resolved).root.toLowerCase();
}

function createGitSnapshot(label = 'bod') {
  const clean = String(label).replace(/[^a-z0-9_-]/gi, '-').slice(0, 40) || 'bod';
  const target = path.join(SNAPSHOTS, `${new Date().toISOString().replace(/[:.]/g, '-')}-${clean}`);
  fs.mkdirSync(target, { recursive: true });
  for (const rel of git(['ls-files']).split(/\r?\n/).filter(Boolean)) {
    const src = safeProjectPath(rel);
    if (fs.existsSync(src) && fs.statSync(src).isFile()) {
      const dst = path.join(target, rel);
      fs.mkdirSync(path.dirname(dst), { recursive: true });
      fs.copyFileSync(src, dst);
    }
  }
  return { name: path.basename(target), path: target };
}

function safeSnapshotDirectory(name) {
  const clean = String(name || '');
  if (!clean || path.basename(clean) !== clean) throw new Error('Neplatný název snapshotu.');
  const target = path.resolve(SNAPSHOTS, clean);
  if (path.dirname(target).toLowerCase() !== path.resolve(SNAPSHOTS).toLowerCase() || !fs.existsSync(target) || !fs.statSync(target).isDirectory()) throw new Error('Snapshot nebyl nalezen.');
  return target;
}

function listSnapshotFiles(directory) {
  const values = [];
  const queue = [directory];
  while (queue.length && values.length < 5000) {
    const current = queue.shift();
    for (const item of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, item.name);
      if (item.isDirectory()) queue.push(full);
      else if (item.isFile()) values.push(path.relative(directory, full));
    }
  }
  return values;
}

function fileHash(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}

function isTextFile(file) {
  const extension = path.extname(file).toLowerCase();
  return TEXT_EXTENSIONS.has(extension) || extension === '';
}

function windowsDrives() {
  const entries = [];
  for (let code = 65; code <= 90; code += 1) {
    const drive = `${String.fromCharCode(code)}:\\`;
    try { if (fs.existsSync(drive)) entries.push({ name: `Disk ${drive}`, path: drive, kind: 'directory', size: 0, drive: true }); } catch {}
  }
  return entries;
}

function desktopOfflineEnabled() {
  try { return portableUpdatesOffline(); }
  catch { return true; }
}

function isLoopbackUrl(value) {
  try {
    const parsed = new URL(String(value));
    const host = parsed.hostname.toLowerCase().replace(/^\[|\]$/g, '');
    return ['http:', 'https:'].includes(parsed.protocol)
      && (host === 'localhost' || host === '::1' || /^127(?:\.\d{1,3}){3}$/.test(host));
  } catch { return false; }
}

function isOfflineTerminalCommand(value) {
  const text = String(value || '').toLowerCase();
  return /(^|[\s;|&])(curl(?:\.exe)?|wget(?:\.exe)?|ssh|scp|sftp|ftp|telnet|ping|tracert|nslookup|winget|choco)(?=\s|$)/i.test(text)
    || /\b(invoke-webrequest|invoke-restmethod|start-bitstransfer|system\.net|webclient|httpclient)\b/i.test(text)
    || /\b(git\s+(clone|fetch|pull|push)|ollama\s+pull|pip\s+install|npm\s+(install|update)|npx\s)\b/i.test(text);
}

function shouldBlockNetworkRequest(value) {
  if (!desktopOfflineEnabled()) return false;
  try {
    const parsed = new URL(String(value));
    return ['http:', 'https:'].includes(parsed.protocol) && !isLoopbackUrl(parsed.toString());
  } catch { return false; }
}

function installOfflineNetworkPolicy() {
  const sessions = [session.defaultSession, session.fromPartition('persist:raven-web')];
  for (const target of new Set(sessions)) {
    target.webRequest.onBeforeRequest((details, callback) => callback({ cancel: shouldBlockNetworkRequest(details.url) }));
  }
}

function safeUrl(value) {
  let text = String(value || '').trim();
  if (!text) text = desktopOfflineEnabled() ? 'about:blank' : 'https://github.com/';
  if (text === 'about:blank') return text;
  if (!/^https?:\/\//i.test(text)) text = `https://${text}`;
  const parsed = new URL(text);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Povoleny jsou pouze HTTP a HTTPS adresy.');
  if (desktopOfflineEnabled() && !isLoopbackUrl(parsed.toString())) throw new Error('Vzdálený web je v offline nebo bezpečném režimu zablokovaný.');
  return parsed.toString();
}

function git(args) {
  return execFileSync('git.exe', ['-C', ROOT, '-c', 'core.safecrlf=false', ...args], { encoding: 'utf8', timeout: 12000, windowsHide: true, stdio: ['ignore', 'pipe', 'ignore'] });
}

function tabState() {
  return {
    activeTabId,
    tabs: [...tabs.values()].map(tab => ({ id: tab.id, title: tab.title, url: tab.url, loading: tab.loading, canGoBack: tab.view.webContents.navigationHistory.canGoBack(), canGoForward: tab.view.webContents.navigationHistory.canGoForward() }))
  };
}

function persistBrowserTabs() {
  if (bootstrapInProgress || launcherDelegationInProgress) return;
  const payload = {
    activeUrl: tabs.get(activeTabId)?.url || '',
    tabs: [...tabs.values()].map(tab => ({ url: tab.url, title: tab.title })).slice(0, 20)
  };
  try {
    const temporary = `${BROWSER_STATE_PATH}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(payload, null, 2), 'utf8');
    fs.renameSync(temporary, BROWSER_STATE_PATH);
  } catch (error) {
    writeLog(`browser state warning ${error?.message || error}`);
  }
}

function loadBrowserTabs() {
  try {
    const payload = JSON.parse(fs.readFileSync(BROWSER_STATE_PATH, 'utf8'));
    const urls = Array.isArray(payload.tabs) ? payload.tabs.map(item => safeUrl(item?.url)).slice(0, 20) : [];
    return { urls, activeUrl: String(payload.activeUrl || '') };
  } catch {
    return { urls: [], activeUrl: '' };
  }
}

function emitBrowserState() {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send('browser:state', tabState());
}

function applyBrowserLayout() {
  for (const tab of tabs.values()) {
    const active = browserVisible && tab.id === activeTabId && browserBounds.width > 20 && browserBounds.height > 20;
    if (active && !tab.attached) {
      mainWindow.contentView.addChildView(tab.view);
      tab.attached = true;
    } else if (!active && tab.attached) {
      mainWindow.contentView.removeChildView(tab.view);
      tab.attached = false;
    }
    if (active) tab.view.setBounds(browserBounds);
    tab.view.webContents.setAudioMuted(!active);
  }
}

function configureTab(tab) {
  const wc = tab.view.webContents;
  const update = () => {
    tab.url = wc.getURL() || tab.url;
    tab.title = wc.getTitle() || new URL(tab.url).hostname;
    emitBrowserState();
    persistBrowserTabs();
  };
  wc.on('console-message', (_event, level, message, line, source) => {
    browserEvents.push({ at: new Date().toISOString(), tabId: tab.id, type: 'console', level, message: String(message).slice(0, 2000), line, source: String(source).slice(0, 500) });
    if (browserEvents.length > 300) browserEvents.splice(0, browserEvents.length - 300);
  });
  wc.on('did-fail-load', (_event, code, description, url) => {
    browserEvents.push({ at: new Date().toISOString(), tabId: tab.id, type: 'load-error', code, description, url });
    if (browserEvents.length > 300) browserEvents.splice(0, browserEvents.length - 300);
  });
  wc.on('did-start-loading', () => { tab.loading = true; update(); });
  wc.on('did-stop-loading', () => { tab.loading = false; update(); });
  wc.on('page-title-updated', event => { event.preventDefault(); update(); });
  wc.on('did-navigate', update);
  wc.on('did-navigate-in-page', update);
  wc.setWindowOpenHandler(({ url }) => {
    try { createTab(url); } catch (error) { writeLog(`browser navigation blocked ${error?.message || error}`); }
    return { action: 'deny' };
  });
  wc.on('will-navigate', (event, url) => {
    try { safeUrl(url); } catch { event.preventDefault(); }
  });
}

function createTab(value = '') {
  const url = safeUrl(value);
  const id = `tab-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  const view = new WebContentsView({ webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, partition: 'persist:raven-web', backgroundThrottling: true } });
  const tab = { id, view, url, title: 'Nová karta', loading: true, attached: true };
  tabs.set(id, tab);
  mainWindow.contentView.addChildView(view);
  configureTab(tab);
  activeTabId = id;
  view.webContents.loadURL(url);
  applyBrowserLayout();
  emitBrowserState();
  persistBrowserTabs();
  return tabState();
}

function selectTab(id) {
  if (!tabs.has(id)) throw new Error('Karta neexistuje.');
  activeTabId = id;
  applyBrowserLayout();
  emitBrowserState();
  persistBrowserTabs();
  return tabState();
}

function closeTab(id) {
  const tab = tabs.get(id);
  if (!tab) return tabState();
  if (tab.attached) mainWindow.contentView.removeChildView(tab.view);
  tab.view.webContents.close();
  tabs.delete(id);
  if (!tabs.size) return createTab();
  if (activeTabId === id) activeTabId = [...tabs.keys()].at(-1);
  applyBrowserLayout();
  emitBrowserState();
  persistBrowserTabs();
  return tabState();
}

function activeTab() {
  const tab = tabs.get(activeTabId);
  if (!tab) throw new Error('Není otevřená webová karta.');
  return tab;
}

function startPortableUpdateHelper(extraArgs = []) {
  const script = path.join(ROOT, 'apply-portable-update.ps1');
  if (!fs.existsSync(script)) return Promise.reject(new Error('Skript portable aktualizace chybí.'));
  return new Promise((resolve, reject) => {
    const child = spawn(WINDOWS_POWERSHELL, [
      '-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', script,
      '-Root', ROOT, '-WaitForProcessId', String(process.pid), ...extraArgs, '-Relaunch'
    ], { cwd: ROOT, detached: true, windowsHide: true, stdio: 'ignore', env: WINDOWS_POWERSHELL_ENV });
    child.once('error', reject);
    child.once('spawn', () => {
      child.unref();
      setImmediate(() => app.quit());
      resolve(true);
    });
  });
}

function installHandlers() {
  ipcMain.handle('app:root', () => ROOT);
  ipcMain.handle('update:status', () => updateState);
  ipcMain.handle('update:check', async () => {
    if (!updateState.supported) return updateState;
    if (updateState.mode === 'portable') {
      try { return await checkPortableUpdates(); }
      catch (error) {
        writeLog(`portable updater error ${error?.stack || error}`);
        return publishUpdateState({ status: 'error', message: `Kontrola portable aktualizace selhala: ${error.message}` });
      }
    }
    publishUpdateState({ status: 'checking', message: 'Kontroluji GitHub Releases…' });
    await autoUpdater.checkForUpdates();
    return updateState;
  });
  ipcMain.handle('update:install', async () => {
    if (!updateState.supported || updateState.status !== 'ready') throw new Error('Aktualizace ještě není připravená k instalaci.');
    if (updateState.mode === 'portable') {
      const script = path.join(ROOT, 'apply-portable-update.ps1');
      if (!fs.existsSync(script)) throw new Error('Aplikační skript portable aktualizace chybí.');
      publishUpdateState({ status: 'installing', message: 'Ukončuji Raven, instaluji ověřenou aktualizaci a znovu jej spustím…' });
      try { return await startPortableUpdateHelper(); }
      catch (error) { publishUpdateState({ status: 'ready', message: `Aktualizace se nespustila: ${error.message}` }); throw error; }
    }
    fs.writeFileSync(INSTALL_MARKER, 'Raven update pending', 'utf8');
    publishUpdateState({ status: 'installing', message: 'Instaluji aktualizaci a restartuji Raven…' });
    setImmediate(() => autoUpdater.quitAndInstall(false, true));
    return true;
  });
  ipcMain.handle('update:rollback', async () => {
    if (updateState.mode !== 'portable' || !fs.existsSync(path.join(RUNTIME, 'updates', 'last-result.json'))) {
      throw new Error('Není dostupná žádná portable aktualizace k návratu.');
    }
    publishUpdateState({ status: 'installing', message: 'Obnovuji předchozí portable verzi a restartuji Raven…' });
    try { return await startPortableUpdateHelper(['-Rollback']); }
    catch (error) { publishUpdateState({ status: 'error', message: `Návrat se nespustil: ${error.message}` }); throw error; }
  });
  ipcMain.handle('window:close', event => BrowserWindow.fromWebContents(event.sender)?.close());
  ipcMain.handle('window:minimize', event => BrowserWindow.fromWebContents(event.sender)?.minimize());
  ipcMain.handle('window:maximize', event => { const win = BrowserWindow.fromWebContents(event.sender); if (win) win.isMaximized() ? win.unmaximize() : win.maximize(); });
  ipcMain.handle('window:new', () => { createAuxWindow(''); return true; });
  ipcMain.handle('window:open-folder', async event => { const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), { title: 'Otevřít složku', properties: ['openDirectory'] }); return result.canceled ? '' : result.filePaths[0] || ''; });
  ipcMain.handle('window:fullscreen', event => { const win = BrowserWindow.fromWebContents(event.sender); if (!win) return false; win.setFullScreen(!win.isFullScreen()); return win.isFullScreen(); });
  ipcMain.handle('window:zoom', (event, action) => {
    const wc = event.sender;
    const current = wc.getZoomFactor();
    const requested = typeof action === 'number' && Number.isFinite(action) ? action : null;
    const next = requested !== null
      ? Math.max(.75, Math.min(1.5, requested))
      : action === 'in' ? Math.min(1.5, current + .1) : action === 'out' ? Math.max(.75, current - .1) : 1;
    wc.setZoomFactor(next);
    return next;
  });
  ipcMain.handle('window:terminal', () => createTerminal(ROOT));
  ipcMain.handle('terminal:list', () => terminalPayload());
  ipcMain.handle('terminal:create', (_event, value = {}) => createTerminal(value?.cwd || ROOT));
  ipcMain.handle('terminal:write', (_event, value = {}) => {
    const entry = terminals.get(String(value?.id || ''));
    if (!entry || !entry.running) throw new Error('Terminál neběží.');
    if (entry.busy) throw new Error('Předchozí příkaz terminálu ještě běží.');
    const data = String(value?.data || '');
    if (!data || data.length > 20000) throw new Error('Příkaz terminálu má neplatnou délku.');
    if (desktopOfflineEnabled() && isOfflineTerminalCommand(data)) {
      throw new Error('Síťový příkaz je v offline nebo bezpečném režimu zablokovaný.');
    }
    requireDesktopPermission(value, 'Spuštění příkazu');
    const executionDirectory = path.join(RUNTIME, 'executions');
    fs.mkdirSync(executionDirectory, { recursive: true });
    const cwdFile = path.join(executionDirectory, `terminal-${entry.id}-cwd.txt`);
    try { fs.rmSync(cwdFile, { force: true }); } catch {}
    const environment = {
      ...WINDOWS_POWERSHELL_ENV,
      RAVEN_TERMINAL_COMMAND: data,
      RAVEN_TERMINAL_CWD_FILE: cwdFile
    };
    if (desktopOfflineEnabled()) {
      environment.HTTP_PROXY = 'http://127.0.0.1:9';
      environment.HTTPS_PROXY = 'http://127.0.0.1:9';
      environment.ALL_PROXY = 'http://127.0.0.1:9';
      environment.NO_PROXY = 'localhost,127.0.0.1,::1';
    }
    const runner = [
      "$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new()",
      "$global:LASTEXITCODE = 0",
      "try { & ([ScriptBlock]::Create($env:RAVEN_TERMINAL_COMMAND)) } catch { [Console]::Error.WriteLine($_.Exception.Message); $global:LASTEXITCODE = 1 } finally { (Get-Location).Path | Set-Content -LiteralPath $env:RAVEN_TERMINAL_CWD_FILE -Encoding utf8 }",
      "exit $(if ($null -eq $LASTEXITCODE) { 0 } else { $LASTEXITCODE })"
    ].join('; ');
    const child = spawn(WINDOWS_POWERSHELL, ['-NoLogo', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', runner], {
      cwd: entry.cwd,
      windowsHide: true,
      detached: false,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: environment
    });
    entry.child = child;
    entry.busy = true;
    entry.exitCode = null;
    writeLog(`terminal command started id=${entry.id} pid=${child.pid} cwd=${entry.cwd}`);
    const publish = (stream, chunk) => emitTerminal('terminal:data', { id: entry.id, stream, data: String(chunk) });
    child.stdout.on('data', chunk => publish('stdout', chunk));
    child.stderr.on('data', chunk => publish('stderr', chunk));
    child.once('error', error => publish('stderr', `\r\n${error.message}\r\n`));
    child.once('close', code => {
      entry.child = null;
      entry.busy = false;
      entry.exitCode = Number.isInteger(code) ? code : -1;
      try {
        const nextCwd = fs.readFileSync(cwdFile, 'utf8').replace(/^\uFEFF/, '').trim();
        if (nextCwd && fs.existsSync(nextCwd) && fs.statSync(nextCwd).isDirectory()) entry.cwd = nextCwd;
        fs.rmSync(cwdFile, { force: true });
      } catch {}
      writeLog(`terminal command finished id=${entry.id} code=${entry.exitCode} cwd=${entry.cwd}`);
      emitTerminal('terminal:data', { id: entry.id, stream: 'stdout', data: `\r\n[exit ${entry.exitCode}]\r\nPS ${entry.cwd}> ` });
    });
    return { id: entry.id, accepted: true };
  });
  ipcMain.handle('terminal:close', (_event, value = {}) => closeTerminal(value?.id));
  ipcMain.handle('terminal:restart', (_event, value = {}) => {
    const entry = terminals.get(String(value?.id || ''));
    const cwd = entry?.cwd || value?.cwd || ROOT;
    closeTerminal(value?.id);
    return createTerminal(cwd);
  });
  ipcMain.handle('window:aux', (_e, display) => { createAuxWindow(String(display || 'telemetry')); return true; });
  ipcMain.handle('files:list', (_e, value = {}) => {
    const requested = typeof value === 'string' ? value : value?.path;
    const dir = safeComputerPath(requested);
    if (!dir) return { path: '::drives', displayPath: 'Tento počítač', parent: null, entries: windowsDrives() };
    if (!fs.statSync(dir).isDirectory()) throw new Error('Vybraná cesta není složka.');
    const entries = fs.readdirSync(dir, { withFileTypes: true }).slice(0, 700).map(x => {
      const full = path.join(dir, x.name);
      let size = 0; try { size = x.isFile() ? fs.statSync(full).size : 0; } catch {}
      return { name: x.name, path: full, kind: x.isDirectory() ? 'directory' : 'file', size };
    }).sort((a, b) => a.kind === b.kind ? a.name.localeCompare(b.name, 'cs') : a.kind === 'directory' ? -1 : 1);
    const root = path.parse(dir).root;
    return { path: dir, displayPath: dir, parent: dir === root ? '::drives' : path.dirname(dir), entries };
  });
  ipcMain.handle('files:read', (_e, value) => {
    const file = safeComputerPath(value);
    if (!file) throw new Error('Vyberte soubor.');
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > 1_500_000 || !isTextFile(file)) throw new Error('Soubor nelze zobrazit jako text.');
    return { path: file, content: fs.readFileSync(file, 'utf8'), language: path.extname(file).slice(1) || 'text' };
  });
  ipcMain.handle('files:write', (_e, value) => {
    requireDesktopPermission(value, 'Zápis do souboru');
    const file = safeComputerPath(value?.path);
    if (!file) throw new Error('Vyberte soubor.');
    if (!fs.existsSync(file) || !fs.statSync(file).isFile() || !isTextFile(file)) throw new Error('Tento soubor nelze bezpečně upravit.');
    const content = String(value?.content ?? '');
    if (Buffer.byteLength(content, 'utf8') > 1_500_000) throw new Error('Soubor je příliš velký.');
    const relative = path.relative(ROOT, file);
    const backupName = relative.startsWith('..') || path.isAbsolute(relative) ? file.replace(/[:\\/]/g, '_') : relative;
    const backup = path.join(SNAPSHOTS, `edit-${Date.now()}`, backupName);
    fs.mkdirSync(path.dirname(backup), { recursive: true });
    fs.copyFileSync(file, backup);
    const temporary = `${file}.raven-tmp`;
    fs.writeFileSync(temporary, content, 'utf8');
    fs.renameSync(temporary, file);
    return { path: relative, bytes: Buffer.byteLength(content, 'utf8') };
  });
  ipcMain.handle('files:create', (_event, value = {}) => {
    requireDesktopPermission(value, 'Vytvoření souboru nebo složky');
    const directory = safeComputerPath(value?.directory);
    if (!directory || !fs.existsSync(directory) || !fs.statSync(directory).isDirectory()) throw new Error('Cílová složka neexistuje.');
    const target = path.join(directory, normalizedFileName(value?.name));
    if (fs.existsSync(target)) throw new Error('Soubor nebo složka už existuje.');
    if (value?.kind === 'directory') fs.mkdirSync(target);
    else fs.writeFileSync(target, String(value?.content || ''), { encoding: 'utf8', flag: 'wx' });
    return { path: target, kind: value?.kind === 'directory' ? 'directory' : 'file' };
  });
  ipcMain.handle('files:rename', (_event, value = {}) => {
    requireDesktopPermission(value, 'Přejmenování');
    const source = safeComputerPath(value?.path);
    if (!source || !fs.existsSync(source)) throw new Error('Původní cesta neexistuje.');
    if (isFilesystemRoot(source)) throw new Error('Kořen disku nelze přejmenovat.');
    const target = path.join(path.dirname(source), normalizedFileName(value?.name));
    if (fs.existsSync(target)) throw new Error('Cílový název už existuje.');
    fs.renameSync(source, target);
    return { from: source, path: target };
  });
  ipcMain.handle('files:delete', async (_event, value = {}) => {
    requireDesktopPermission(value, 'Odstranění');
    const source = safeComputerPath(value?.path);
    if (!source || !fs.existsSync(source)) throw new Error('Odstraňovaná cesta neexistuje.');
    if (isFilesystemRoot(source)) throw new Error('Celý disk nelze odstranit.');
    await shell.trashItem(source);
    return { recoverable: true, originalPath: source, trashPath: 'Windows Koš' };
  });
  ipcMain.handle('files:search', async (_event, value = {}) => {
    const root = safeComputerPath(value?.root);
    const query = String(value?.query || '').trim().toLocaleLowerCase('cs');
    if (!root || !fs.existsSync(root) || !fs.statSync(root).isDirectory()) throw new Error('Prohledávaná složka neexistuje.');
    if (query.length < 2) throw new Error('Hledaný text musí mít alespoň dva znaky.');
    const results = [];
    const queue = [root];
    let visited = 0;
    while (queue.length && results.length < 300 && visited < 10000) {
      const directory = queue.shift();
      let entries = [];
      try { entries = await fs.promises.readdir(directory, { withFileTypes: true }); } catch { continue; }
      for (const entry of entries) {
        if (HIDDEN.has(entry.name)) continue;
        const full = path.join(directory, entry.name);
        visited += 1;
        if (entry.name.toLocaleLowerCase('cs').includes(query)) results.push({ name: entry.name, path: full, kind: entry.isDirectory() ? 'directory' : 'file' });
        if (entry.isDirectory() && queue.length < 2000) queue.push(full);
        if (results.length >= 300 || visited >= 10000) break;
      }
    }
    return { root, query, results, visited, truncated: results.length >= 300 || visited >= 10000 };
  });
  ipcMain.handle('git:status', () => ({ entries: git(['status', '--short']).split(/\r?\n/).filter(Boolean) }));
  ipcMain.handle('git:diff', (_e, relative = '') => ({ path: relative, content: git(['diff', '--', String(relative)]) || 'Žádné neuložené změny.' }));
  ipcMain.handle('git:summary', () => {
    const status = git(['status', '--short']).split(/\r?\n/).filter(Boolean);
    const numstat = git(['diff', '--numstat']).split(/\r?\n/).filter(Boolean);
    let added = 0, removed = 0;
    for (const row of numstat) { const [a, d] = row.split('\t'); added += Number(a) || 0; removed += Number(d) || 0; }
    return { count: status.length, added, removed, entries: status };
  });
  ipcMain.handle('git:snapshot', (_e, label = 'bod') => {
    return createGitSnapshot(label);
  });
  ipcMain.handle('git:changes', () => {
    const rows = git(['status', '--porcelain=v1']).split(/\r?\n/).filter(Boolean).map(line => ({ index: line[0], worktree: line[1], path: line.slice(3) }));
    return { rows, branch: git(['branch', '--show-current']).trim(), head: git(['rev-parse', '--short', 'HEAD']).trim() };
  });
  ipcMain.handle('git:stage', (_event, value = {}) => {
    requireDesktopPermission(value, 'Přidání změny do stage');
    const relative = String(value?.path || '').trim();
    safeProjectPath(relative);
    git(['add', '--', relative]);
    return true;
  });
  ipcMain.handle('git:unstage', (_event, value = {}) => {
    requireDesktopPermission(value, 'Odebrání změny ze stage');
    const relative = String(value?.path || '').trim();
    safeProjectPath(relative);
    git(['restore', '--staged', '--', relative]);
    return true;
  });
  ipcMain.handle('git:commit', (_event, value = {}) => {
    requireDesktopPermission(value, 'Vytvoření commitu');
    const message = String(value?.message || '').trim();
    if (!message || message.length > 200) throw new Error('Commit musí mít zprávu dlouhou 1 až 200 znaků.');
    return { output: git(['commit', '-m', message]).trim() };
  });
  ipcMain.handle('git:discard', (_event, value = {}) => {
    requireDesktopPermission(value, 'Vrácení pracovní změny');
    const relative = String(value?.path || '').trim();
    safeProjectPath(relative);
    const safety = createGitSnapshot('pred-vracenim');
    git(['restore', '--worktree', '--', relative]);
    return { restored: relative, safetySnapshot: safety.name };
  });
  ipcMain.handle('git:branches', () => ({ current: git(['branch', '--show-current']).trim(), branches: git(['for-each-ref', '--format=%(refname:short)', 'refs/heads']).split(/\r?\n/).filter(Boolean) }));
  ipcMain.handle('git:snapshots', () => ({ snapshots: fs.existsSync(SNAPSHOTS) ? fs.readdirSync(SNAPSHOTS, { withFileTypes: true }).filter(item => item.isDirectory()).map(item => item.name).sort().reverse() : [] }));
  ipcMain.handle('git:snapshot-files', (_event, value = {}) => {
    const directory = safeSnapshotDirectory(value?.name);
    const files = listSnapshotFiles(directory).map(relative => {
      const stored = path.join(directory, relative);
      const current = safeProjectPath(relative);
      const exists = fs.existsSync(current) && fs.statSync(current).isFile();
      return { path: relative, status: !exists ? 'missing' : fileHash(stored) === fileHash(current) ? 'same' : 'changed' };
    });
    return { name: path.basename(directory), files };
  });
  ipcMain.handle('git:snapshot-restore', (_event, value = {}) => {
    requireDesktopPermission(value, 'Obnovení souboru ze snapshotu');
    const directory = safeSnapshotDirectory(value?.name);
    const relative = String(value?.path || '').trim();
    const stored = path.resolve(directory, relative);
    if (path.relative(directory, stored).startsWith('..') || !fs.existsSync(stored) || !fs.statSync(stored).isFile()) throw new Error('Soubor ve snapshotu nebyl nalezen.');
    const current = safeProjectPath(relative);
    const safety = createGitSnapshot('pred-obnovenim');
    fs.mkdirSync(path.dirname(current), { recursive: true });
    fs.copyFileSync(stored, current);
    return { restored: relative, safetySnapshot: safety.name };
  });
  ipcMain.handle('browser:list', () => tabState());
  ipcMain.handle('browser:create', (_e, url) => createTab(url));
  ipcMain.handle('browser:select', (_e, id) => selectTab(id));
  ipcMain.handle('browser:close', (_e, id) => closeTab(id));
  ipcMain.handle('browser:navigate', (_e, url) => { const tab = activeTab(); tab.view.webContents.loadURL(safeUrl(url)); return tabState(); });
  ipcMain.handle('browser:action', (_e, action) => {
    const wc = activeTab().view.webContents;
    if (desktopOfflineEnabled() && !isLoopbackUrl(wc.getURL()) && wc.getURL() !== 'about:blank') wc.loadURL('about:blank');
    else if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    else if (action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
    else if (action === 'reload') wc.reload();
    else if (action === 'stop') wc.stop();
    else if (action === 'devtools') wc.openDevTools({ mode: 'detach' });
    return tabState();
  });
  ipcMain.handle('browser:diagnostics', () => ({ events: browserEvents.slice(-300) }));
  ipcMain.handle('browser:capture', async () => {
    const tab = activeTab();
    const image = await tab.view.webContents.capturePage();
    const target = path.join(ARTIFACTS, `browser-${new Date().toISOString().replace(/[:.]/g, '-')}.png`);
    await fs.promises.writeFile(target, image.toPNG());
    return { path: target, bytes: image.toPNG().length };
  });
  ipcMain.handle('browser:bounds', (_e, value) => {
    const [x, y, width, height] = ['x', 'y', 'width', 'height'].map(key => Math.max(0, Math.round(Number(value?.[key]) || 0)));
    browserBounds = { x, y, width, height }; applyBrowserLayout(); return true;
  });
  ipcMain.handle('browser:visible', (_e, visible) => { browserVisible = Boolean(visible); applyBrowserLayout(); return true; });
}

function createAuxWindow(display = '') {
  const win = new BrowserWindow({ width: 1450, height: 900, backgroundColor: '#171715', title: 'Raven 1.2', icon: path.join(ROOT, 'desktop', 'raven.ico'), autoHideMenuBar: true, titleBarStyle: 'hidden', titleBarOverlay: { color: '#191917', symbolColor: '#bdbdb7', height: 30 }, webPreferences: { preload: path.join(__dirname, 'preload.js'), sandbox: false, contextIsolation: true, nodeIntegration: false } });
  win.loadURL(display ? `${HUD_URL}&display=${encodeURIComponent(display)}` : HUD_URL);
  return win;
}

function createMainWindow() {
  // The HUD is trusted loopback content. Electron's renderer sandbox can fail
  // before preload initialization when the packaged app runs from removable
  // NTFS media (startupData is null). Keep context isolation and Node disabled;
  // untrusted browser tabs remain in their separate sandboxed WebContentsView.
  mainWindow = new BrowserWindow({ width: 1600, height: 980, minWidth: 900, minHeight: 600, show: false, backgroundColor: '#171715', title: 'Raven 1.2', icon: path.join(ROOT, 'desktop', 'raven.ico'), autoHideMenuBar: true, titleBarStyle: 'hidden', titleBarOverlay: { color: '#191917', symbolColor: '#bdbdb7', height: 30 }, webPreferences: { preload: path.join(__dirname, 'preload.js'), sandbox: false, contextIsolation: true, nodeIntegration: false } });
  let windowRevealed = false;
  const revealMainWindow = reason => {
    if (!mainWindow || mainWindow.isDestroyed()) return;
    try {
      mainWindow.center();
      mainWindow.maximize();
      mainWindow.setAlwaysOnTop(true);
      mainWindow.show();
      mainWindow.focus();
      mainWindow.moveTop();
      windowRevealed = true;
      writeLog(`main window shown reason=${reason} visible=${mainWindow.isVisible()} focused=${mainWindow.isFocused()} bounds=${JSON.stringify(mainWindow.getBounds())}`);
      setTimeout(() => {
        if (mainWindow && !mainWindow.isDestroyed()) mainWindow.setAlwaysOnTop(false);
      }, 1500);
    } catch (error) {
      writeLog(`main window show failed reason=${reason} error=${error.stack || error}`);
    }
  };
  mainWindow.once('ready-to-show', () => revealMainWindow('ready-to-show'));
  mainWindow.webContents.once('did-finish-load', () => revealMainWindow('did-finish-load'));
  setTimeout(() => { if (!windowRevealed) revealMainWindow('fallback-timeout'); }, 12000);
  mainWindow.webContents.on('render-process-gone', (_event, details) => writeLog(`renderer gone reason=${details.reason} code=${details.exitCode}`));
  mainWindow.webContents.on('did-fail-load', (_event, code, description, url) => writeLog(`load failed code=${code} description=${description} url=${url}`));
  mainWindow.webContents.on('console-message', (_event, level, message, line, source) => { if (level >= 2) writeLog(`renderer console level=${level} ${message} at ${source}:${line}`); });
  mainWindow.loadURL(HUD_URL);
  mainWindow.on('resize', applyBrowserLayout);
  mainWindow.on('closed', () => {
    writeLog('main window closed');
    browserVisible = false;
    for (const tab of tabs.values()) {
      try {
        const contents = tab?.view?.webContents;
        if (contents && !contents.isDestroyed()) contents.close();
      } catch (error) {
        writeLog(`tab close warning ${error?.message || error}`);
      }
    }
    tabs.clear();
    activeTabId = '';
    mainWindow = null;
  });
  const savedBrowser = loadBrowserTabs();
  if (savedBrowser.urls.length) {
    for (const url of savedBrowser.urls) createTab(url);
    const preferred = [...tabs.values()].find(tab => tab.url === savedBrowser.activeUrl);
    if (preferred) selectTab(preferred.id);
  } else {
    createTab();
  }
}

function startDesktopApplication() {
  if (desktopApplicationStarted || bootstrapInProgress) return;
  desktopApplicationStarted = true;
  app.whenReady().then(() => {
    installOfflineNetworkPolicy();
    session.fromPartition('persist:raven-web').on('will-download', (_event, item) => {
      const safeName = path.basename(item.getFilename()).replace(/[^a-z0-9._-]/gi, '_');
      item.setSavePath(path.join(QUARANTINE, `${Date.now()}-${safeName}`));
    });
    installHandlers(); createMainWindow();
    configureAutoUpdates();
    writeLog('main window created');
  }).catch(error => { writeLog(`ready failed ${error.stack || error}`); app.quit(); });
  app.on('before-quit', stopRavenServices);
  app.on('will-quit', closeAllTerminals);
  app.on('window-all-closed', () => app.quit());
}

if (!bootstrapInProgress && !launcherDelegationInProgress) startDesktopApplication();
