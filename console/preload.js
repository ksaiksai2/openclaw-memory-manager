// preload — IPC 桥
const { ipcRenderer, contextBridge } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  getConfig: () => ipcRenderer.invoke('get-config'),
  saveConfig: (config) => ipcRenderer.invoke('save-config', config),
  getProviders: () => ipcRenderer.invoke('get-providers'),
  detectModel: (config) => ipcRenderer.invoke('detect-model', config),
  readWorkspaceFile: (filename) => ipcRenderer.invoke('read-workspace-file', filename),
  saveWorkspaceFile: (filename, content) => ipcRenderer.invoke('save-workspace-file', filename, content),
  listTaskLogs: () => ipcRenderer.invoke('list-task-logs'),
  readTaskLog: (filename) => ipcRenderer.invoke('read-task-log', filename),
  readLatestStatus: () => ipcRenderer.invoke('read-latest-status'),
  runTask: (dryRun) => ipcRenderer.invoke('run-task', dryRun),
});
