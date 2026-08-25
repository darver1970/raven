const { contextBridge, ipcRenderer } = require('electron');

const invoke = (channel, payload) => ipcRenderer.invoke(channel, payload);
contextBridge.exposeInMainWorld('ravenDesktop', {
  rootPath: () => invoke('app:root'),
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
  gitStatus: () => invoke('git:status'),
  gitDiff: path => invoke('git:diff', path),
  gitSummary: () => invoke('git:summary'),
  createSnapshot: label => invoke('git:snapshot', label),
  browser: {
    list: () => invoke('browser:list'),
    create: url => invoke('browser:create', url),
    select: id => invoke('browser:select', id),
    close: id => invoke('browser:close', id),
    navigate: url => invoke('browser:navigate', url),
    action: action => invoke('browser:action', action),
    setBounds: bounds => invoke('browser:bounds', bounds),
    setVisible: visible => invoke('browser:visible', visible)
  },
  onBrowserState: callback => {
    const listener = (_event, state) => callback(state);
    ipcRenderer.on('browser:state', listener);
    return () => ipcRenderer.removeListener('browser:state', listener);
  }
});
