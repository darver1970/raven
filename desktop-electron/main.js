const { app, BrowserWindow, WebContentsView, ipcMain, session, shell, dialog } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync, spawn } = require('node:child_process');

const WINDOWS_POWERSHELL = path.join(
  process.env.SystemRoot || 'C:\\Windows',
  'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe'
);
const INSTALL_CONFIG = path.join(process.env.LOCALAPPDATA || path.dirname(process.execPath), 'Raven', 'install-path.txt');
let SAVED_ROOT = '';
try { SAVED_ROOT = fs.readFileSync(INSTALL_CONFIG, 'utf8').trim(); } catch {}
const EXECUTABLE_DIRECTORY = path.dirname(process.execPath);
const IS_NSIS_INSTALL = app.isPackaged
  && !process.env.PORTABLE_EXECUTABLE_DIR
  && path.basename(EXECUTABLE_DIRECTORY).toLowerCase() !== 'desktop';
const SAVED_ROOT_IS_VALID = SAVED_ROOT
  && path.isAbsolute(SAVED_ROOT)
  && fs.existsSync(path.join(path.resolve(SAVED_ROOT), 'raven_control.py'));
const EXECUTABLE_ROOT = IS_NSIS_INSTALL
  ? EXECUTABLE_DIRECTORY
  : app.isPackaged && path.basename(EXECUTABLE_DIRECTORY).toLowerCase() === 'desktop'
    ? path.resolve(EXECUTABLE_DIRECTORY, '..')
    : 'C:\\Raven';
const INSTALLED_ROOT = IS_NSIS_INSTALL
  ? path.resolve(EXECUTABLE_ROOT)
  : SAVED_ROOT_IS_VALID
    ? path.resolve(SAVED_ROOT)
    : path.resolve(EXECUTABLE_ROOT);
const INSTALL_MARKER = path.join(INSTALLED_ROOT, '.raven-installing');
const BUNDLED_PROJECT = app.isPackaged ? path.join(process.resourcesPath, 'raven-project') : '';
let bootstrapInProgress = false;
let launcherDelegationInProgress = false;

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
    installerArguments.join(' '),
    'exit $LASTEXITCODE'
  ].join('; ');
  const encodedInstallerInvocation = Buffer.from(installerInvocation, 'utf16le').toString('base64');
  const elevatedCommand = [
    `$installerArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-EncodedCommand', ${quotePowerShell(encodedInstallerInvocation)})`,
    `try { $installerProcess = Start-Process -FilePath ${quotePowerShell(WINDOWS_POWERSHELL)} -Verb RunAs -ArgumentList $installerArguments -Wait -PassThru; exit $installerProcess.ExitCode } catch { exit 1223 }`
  ].join('; ');
  const child = spawn(WINDOWS_POWERSHELL, ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', elevatedCommand], {
    cwd: BUNDLED_PROJECT,
    detached: false,
    windowsHide: true,
    stdio: 'ignore'
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
      ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', launcher],
      { cwd: INSTALLED_ROOT, detached: false, windowsHide: true, stdio: 'ignore' }
    );
    launcherProcess.once('spawn', () => {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_started pid=${launcherProcess.pid}\n`, 'utf8');
    });
    launcherProcess.once('close', launcherCode => {
      fs.appendFileSync(bootstrapLog, `${new Date().toISOString()} launcher_finished code=${launcherCode}\n`, 'utf8');
      if (launcherCode === 0) {
        app.exit(0);
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
        `Použijte zástupce Raven 1.0 na ploše. Podrobnosti jsou v ${bootstrapLog}`
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
  IS_NSIS_INSTALL
  && !bootstrapInProgress
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
    ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', launcher],
    { cwd: INSTALLED_ROOT, detached: false, windowsHide: true, stdio: 'ignore' }
  );
  launcherProcess.once('spawn', () => {
    fs.appendFileSync(launcherWrapperLog, `${new Date().toISOString()} launcher_started pid=${launcherProcess.pid}\n`, 'utf8');
  });
  launcherProcess.once('close', launcherCode => {
    fs.appendFileSync(launcherWrapperLog, `${new Date().toISOString()} launcher_finished code=${launcherCode}\n`, 'utf8');
    if (launcherCode === 0) {
      app.exit(0);
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
    : app.isPackaged && path.basename(path.dirname(process.execPath)).toLowerCase() === 'desktop'
      ? path.resolve(path.dirname(process.execPath), '..')
      : path.resolve(__dirname, '..');
const RUNTIME = path.join(ROOT, 'runtime');
const PROFILE = bootstrapInProgress
  ? path.join(process.env.LOCALAPPDATA || INSTALLED_ROOT, 'Raven', 'bootstrap-profile')
  : path.join(RUNTIME, 'electron-profile');
const QUARANTINE = path.join(RUNTIME, 'quarantine');
const SNAPSHOTS = path.join(RUNTIME, 'snapshots');
const HUD_URL = 'http://127.0.0.1:5174/?desktop=electron&hud_version=1.0&asset_revision=raven-8';
const TEXT_EXTENSIONS = new Set(['.css', '.html', '.js', '.json', '.md', '.ps1', '.py', '.txt', '.yml', '.yaml', '.toml']);
const HIDDEN = new Set(['.git', '.venv', '__pycache__', 'node_modules', 'pyinstaller-build', 'pyinstaller-spec']);
let mainWindow;
let browserVisible = false;
let browserBounds = { x: 0, y: 0, width: 0, height: 0 };
let activeTabId = '';
const tabs = new Map();

if (!launcherDelegationInProgress) fs.mkdirSync(PROFILE, { recursive: true });
if (!bootstrapInProgress && !launcherDelegationInProgress) {
  fs.mkdirSync(QUARANTINE, { recursive: true });
  fs.mkdirSync(SNAPSHOTS, { recursive: true });
}
if (!launcherDelegationInProgress) app.setPath('userData', PROFILE);
app.setName('Raven 1.0');
app.setAppUserModelId('cz.raven.desktop');
const MAIN_LOG = path.join(RUNTIME, 'electron-main.log');
const writeLog = value => { try { fs.appendFileSync(MAIN_LOG, `${new Date().toISOString()} ${value}\n`, 'utf8'); } catch {} };
process.on('uncaughtException', error => writeLog(`uncaughtException ${error.stack || error}`));
process.on('unhandledRejection', error => writeLog(`unhandledRejection ${error?.stack || error}`));
writeLog(`start packaged=${app.isPackaged} root=${ROOT}`);
if (!bootstrapInProgress && !launcherDelegationInProgress) {
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

function safeUrl(value) {
  let text = String(value || '').trim();
  if (!text) text = 'https://github.com/';
  if (!/^https?:\/\//i.test(text)) text = `https://${text}`;
  const parsed = new URL(text);
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Povoleny jsou pouze HTTP a HTTPS adresy.');
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
  };
  wc.on('did-start-loading', () => { tab.loading = true; update(); });
  wc.on('did-stop-loading', () => { tab.loading = false; update(); });
  wc.on('page-title-updated', event => { event.preventDefault(); update(); });
  wc.on('did-navigate', update);
  wc.on('did-navigate-in-page', update);
  wc.setWindowOpenHandler(({ url }) => { createTab(url); return { action: 'deny' }; });
  wc.on('will-navigate', (event, url) => {
    try { safeUrl(url); } catch { event.preventDefault(); }
  });
}

function createTab(value = 'https://github.com/') {
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
  return tabState();
}

function selectTab(id) {
  if (!tabs.has(id)) throw new Error('Karta neexistuje.');
  activeTabId = id;
  applyBrowserLayout();
  emitBrowserState();
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
  return tabState();
}

function activeTab() {
  const tab = tabs.get(activeTabId);
  if (!tab) throw new Error('Není otevřená webová karta.');
  return tab;
}

function installHandlers() {
  ipcMain.handle('app:root', () => ROOT);
  ipcMain.handle('window:close', event => BrowserWindow.fromWebContents(event.sender)?.close());
  ipcMain.handle('window:minimize', event => BrowserWindow.fromWebContents(event.sender)?.minimize());
  ipcMain.handle('window:maximize', event => { const win = BrowserWindow.fromWebContents(event.sender); if (win) win.isMaximized() ? win.unmaximize() : win.maximize(); });
  ipcMain.handle('window:new', () => { createAuxWindow(''); return true; });
  ipcMain.handle('window:open-folder', async event => { const result = await dialog.showOpenDialog(BrowserWindow.fromWebContents(event.sender), { title: 'Otevřít složku', properties: ['openDirectory'] }); return result.canceled ? '' : result.filePaths[0] || ''; });
  ipcMain.handle('window:fullscreen', event => { const win = BrowserWindow.fromWebContents(event.sender); if (!win) return false; win.setFullScreen(!win.isFullScreen()); return win.isFullScreen(); });
  ipcMain.handle('window:zoom', (event, action) => { const wc = event.sender; const current = wc.getZoomFactor(); const next = action === 'in' ? Math.min(2, current + .1) : action === 'out' ? Math.max(.6, current - .1) : 1; wc.setZoomFactor(next); return next; });
  ipcMain.handle('window:terminal', () => { const child = spawn('powershell.exe', ['-NoExit', '-Command', `Set-Location -LiteralPath '${ROOT.replace(/'/g, "''")}'`], { cwd: ROOT, detached: true, windowsHide: false, stdio: 'ignore' }); child.unref(); return true; });
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
    const mode = String(value?.permissionMode || 'denied');
    if (mode === 'denied' || (mode === 'confirm' && value?.confirmed !== true)) throw new Error('Zápis není povolen zvolenou úrovní přístupu.');
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
    const clean = String(label).replace(/[^a-z0-9_-]/gi, '-').slice(0, 40) || 'bod';
    const target = path.join(SNAPSHOTS, `${new Date().toISOString().replace(/[:.]/g, '-')}-${clean}`);
    fs.mkdirSync(target, { recursive: true });
    for (const rel of git(['ls-files']).split(/\r?\n/).filter(Boolean)) { const src = safeProjectPath(rel); if (fs.existsSync(src) && fs.statSync(src).isFile()) { const dst = path.join(target, rel); fs.mkdirSync(path.dirname(dst), { recursive: true }); fs.copyFileSync(src, dst); } }
    return { name: path.basename(target), path: target };
  });
  ipcMain.handle('browser:list', () => tabState());
  ipcMain.handle('browser:create', (_e, url) => createTab(url));
  ipcMain.handle('browser:select', (_e, id) => selectTab(id));
  ipcMain.handle('browser:close', (_e, id) => closeTab(id));
  ipcMain.handle('browser:navigate', (_e, url) => { const tab = activeTab(); tab.view.webContents.loadURL(safeUrl(url)); return tabState(); });
  ipcMain.handle('browser:action', (_e, action) => {
    const wc = activeTab().view.webContents;
    if (action === 'back' && wc.navigationHistory.canGoBack()) wc.navigationHistory.goBack();
    else if (action === 'forward' && wc.navigationHistory.canGoForward()) wc.navigationHistory.goForward();
    else if (action === 'reload') wc.reload();
    else if (action === 'stop') wc.stop();
    else if (action === 'devtools') wc.openDevTools({ mode: 'detach' });
    return tabState();
  });
  ipcMain.handle('browser:bounds', (_e, value) => {
    const [x, y, width, height] = ['x', 'y', 'width', 'height'].map(key => Math.max(0, Math.round(Number(value?.[key]) || 0)));
    browserBounds = { x, y, width, height }; applyBrowserLayout(); return true;
  });
  ipcMain.handle('browser:visible', (_e, visible) => { browserVisible = Boolean(visible); applyBrowserLayout(); return true; });
}

function createAuxWindow(display = '') {
  const win = new BrowserWindow({ width: 1450, height: 900, backgroundColor: '#171715', title: 'Raven 1.0', icon: path.join(ROOT, 'desktop', 'raven.ico'), autoHideMenuBar: true, titleBarStyle: 'hidden', titleBarOverlay: { color: '#191917', symbolColor: '#bdbdb7', height: 30 }, webPreferences: { preload: path.join(__dirname, 'preload.js'), sandbox: true, contextIsolation: true } });
  win.loadURL(display ? `${HUD_URL}&display=${encodeURIComponent(display)}` : HUD_URL);
  return win;
}

function createMainWindow() {
  mainWindow = new BrowserWindow({ width: 1600, height: 980, minWidth: 1100, minHeight: 700, backgroundColor: '#171715', title: 'Raven 1.0', icon: path.join(ROOT, 'desktop', 'raven.ico'), autoHideMenuBar: true, titleBarStyle: 'hidden', titleBarOverlay: { color: '#191917', symbolColor: '#bdbdb7', height: 30 }, webPreferences: { preload: path.join(__dirname, 'preload.js'), sandbox: true, contextIsolation: true, nodeIntegration: false } });
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
  createTab();
}

if (!bootstrapInProgress && !launcherDelegationInProgress) {
  app.whenReady().then(() => {
    session.fromPartition('persist:raven-web').on('will-download', (_event, item) => {
      const safeName = path.basename(item.getFilename()).replace(/[^a-z0-9._-]/gi, '_');
      item.setSavePath(path.join(QUARANTINE, `${Date.now()}-${safeName}`));
    });
    installHandlers(); createMainWindow();
    writeLog('main window created');
  }).catch(error => { writeLog(`ready failed ${error.stack || error}`); app.quit(); });
  app.on('window-all-closed', () => app.quit());
}
