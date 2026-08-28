const { contextBridge, ipcRenderer } = require('electron');

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload);
contextBridge.exposeInMainWorld('ravenDesktop', {
  rootPath: () => invoke('app:root'),
  updater: {
    status: () => invoke('update:status'),
    check: () => invoke('update:check'),
    install: () => invoke('update:install'),
    onStatus: callback => {
      const listener = (_event, value) => callback(value);
      ipcRenderer.on('update:status', listener);
      return () => ipcRenderer.removeListener('update:status', listener);
    }
  },
  close: () => invoke('window:close'),
  minimize: () => invoke('window:minimize'),
  maximize: () => invoke('window:maximize'),
  newWindow: () => invoke('window:new'),
  openFolder: () => invoke('window:open-folder'),
  toggleFullscreen: () => invoke('window:fullscreen'),
  zoom: action => invoke('window:zoom', action),
  openTerminal: () => invoke('window:terminal'),
  openTelemetryWindow: () => invoke('window:aux', 'telemetry'),
  openAgentsWindow: () => invoke('window:aux', 'agents'),
  openWorkspaceWindow: workspace => invoke('window:aux', `workspace:${workspace}`),
  listFiles: value => invoke('files:list', value),
  readFile: path => invoke('files:read', path),
  writeFile: value => invoke('files:write', value),
  createFile: value => invoke('files:create', value),
  renameFile: value => invoke('files:rename', value),
  deleteFile: value => invoke('files:delete', value),
  searchFiles: value => invoke('files:search', value),
  gitStatus: () => invoke('git:status'),
  gitDiff: path => invoke('git:diff', path),
  gitSummary: () => invoke('git:summary'),
  createSnapshot: label => invoke('git:snapshot', label),
  gitChanges: () => invoke('git:changes'),
  gitStage: value => invoke('git:stage', value),
  gitUnstage: value => invoke('git:unstage', value),
  gitCommit: value => invoke('git:commit', value),
  gitDiscard: value => invoke('git:discard', value),
  gitBranches: () => invoke('git:branches'),
  gitSnapshots: () => invoke('git:snapshots'),
  gitSnapshotFiles: value => invoke('git:snapshot-files', value),
  gitSnapshotRestore: value => invoke('git:snapshot-restore', value),
  terminal: {
    list: () => invoke('terminal:list'),
    create: value => invoke('terminal:create', value),
    write: value => invoke('terminal:write', value),
    close: value => invoke('terminal:close', value),
    restart: value => invoke('terminal:restart', value)
  },
  browser: {
    list: () => invoke('browser:list'),
    create: url => invoke('browser:create', url),
    select: id => invoke('browser:select', id),
    close: id => invoke('browser:close', id),
    navigate: url => invoke('browser:navigate', url),
    action: action => invoke('browser:action', action),
    setBounds: bounds => invoke('browser:bounds', bounds),
    setVisible: visible => invoke('browser:visible', visible),
    diagnostics: () => invoke('browser:diagnostics'),
    capture: () => invoke('browser:capture')
  },
  onBrowserState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('browser:state', listener);
    return () => ipcRenderer.removeListener('browser:state', listener);
  },
  onTerminalData: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('terminal:data', listener);
    return () => ipcRenderer.removeListener('terminal:data', listener);
  },
  onTerminalExit: callback => {
    const listener = (_event, value) => callback(value);
    ipcRenderer.on('terminal:exit', listener);
    return () => ipcRenderer.removeListener('terminal:exit', listener);
  }
});
