// OMM 控制台 — OpenClaw Memory Manager 桌面封装
const { app, BrowserWindow, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');

// ===================== 配置 =====================
const CONFIG = {
  name: 'OMM',
  title: 'OMM控制台',
  icon: path.join(__dirname, 'assets', 'icon_256.png'),
};
// ================================================

const pkg = JSON.parse(fs.readFileSync(path.join(__dirname, 'package.json'), 'utf8'));
const WINDOW_TITLE = `${CONFIG.title} (v${pkg.version})`;

// ===================== LLM Provider 预设（与 scripts/common/utils.py 对齐） =====================
const LLM_PROVIDERS = {
  xiaomi: { name: 'Xiaomi MiMo', apiUrl: 'https://api.xiaomimimo.com/v1/chat/completions', model: 'mimo-v2.5-pro' },
  deepseek: { name: 'DeepSeek', apiUrl: 'https://api.deepseek.com/v1/chat/completions', model: 'deepseek-v4-flash' },
  zhipu: { name: '智谱 GLM', apiUrl: 'https://open.bigmodel.cn/api/paas/v4/chat/completions', model: 'glm-4.7-flash' },
  openai: { name: 'OpenAI', apiUrl: 'https://api.openai.com/v1/chat/completions', model: 'gpt-4o-mini' },
  moonshot: { name: 'Moonshot (Kimi)', apiUrl: 'https://api.moonshot.cn/v1/chat/completions', model: 'moonshot-v1-8k' },
  qwen: { name: '通义千问', apiUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions', model: 'qwen-plus' },
  siliconflow: { name: 'SiliconFlow', apiUrl: 'https://api.siliconflow.cn/v1/chat/completions', model: 'deepseek-ai/DeepSeek-V3' },
};
// =====================================================================================

let win = null;

// ---------- 窗口 ----------
function createWindow() {
  win = new BrowserWindow({
    width: 1200,
    height: 850,
    minWidth: 1000,
    minHeight: 720,
    maxWidth: 1680,
    maxHeight: 1250,
    icon: CONFIG.icon,
    autoHideMenuBar: true,
    title: WINDOW_TITLE,
    backgroundColor: '#08080f',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  // 标题栏版本标注（防页面 <title> 覆盖）
  win.on('page-title-updated', (e) => { e.preventDefault(); win.setTitle(WINDOW_TITLE); });
  win.once('ready-to-show', () => win.show());

  // 外部链接一律不开
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));

  win.loadFile('renderer/index.html');
}

// ---------- IPC：会话读取 ----------
const AGENTS_DIR = path.join(process.env.USERPROFILE || process.env.HOME, '.openclaw', 'agents');
const OPENCLAW_CONFIG_PATH = path.join(process.env.USERPROFILE || process.env.HOME, '.openclaw', 'openclaw.json');

// 探测主 agent：openclaw.json 第一个有 sessions 的 agent → agents 目录第一个 → main
function detectMainAgent() {
  try {
    if (fs.existsSync(OPENCLAW_CONFIG_PATH)) {
      const cfg = JSON.parse(fs.readFileSync(OPENCLAW_CONFIG_PATH, 'utf-8'));
      const agents = cfg.agents?.list || [];
      for (const a of agents) {
        const id = a.id || '';
        if (id && fs.existsSync(path.join(AGENTS_DIR, id, 'sessions'))) return id;
      }
    }
  } catch (_) {}
  try {
    if (fs.existsSync(AGENTS_DIR)) {
      const dirs = fs.readdirSync(AGENTS_DIR).filter(d => fs.existsSync(path.join(AGENTS_DIR, d, 'sessions')));
      if (dirs.length) return dirs[0];
    }
  } catch (_) {}
  return 'main';
}

// 主 agent 的 workspace 目录：openclaw.json 配置的 workspace → 默认 ~/.openclaw/workspace
function detectMainWorkspace() {
  try {
    if (fs.existsSync(OPENCLAW_CONFIG_PATH)) {
      const cfg = JSON.parse(fs.readFileSync(OPENCLAW_CONFIG_PATH, 'utf-8'));
      const agents = cfg.agents?.list || [];
      for (const a of agents) {
        if (a.workspace && fs.existsSync(a.workspace)) return a.workspace;
      }
    }
  } catch (_) {}
  const ws = path.join(process.env.USERPROFILE || process.env.HOME, '.openclaw', 'workspace');
  return ws;
}

function listSessions() {
  const result = [];
  try {
    if (!fs.existsSync(AGENTS_DIR)) return result;
    for (const agentId of fs.readdirSync(AGENTS_DIR)) {
      if (!fs.existsSync(path.join(AGENTS_DIR, agentId, 'sessions'))) continue;
      const sessionsJsonPath = path.join(AGENTS_DIR, agentId, 'sessions', 'sessions.json');
      if (!fs.existsSync(sessionsJsonPath)) continue;
      try {
        const data = JSON.parse(fs.readFileSync(sessionsJsonPath, 'utf-8'));
        for (const [key, meta] of Object.entries(data)) {
          if (!meta.sessionFile || !fs.existsSync(meta.sessionFile)) continue;
          result.push({
            key, sessionId: meta.sessionId, agentName: key.split(':')[1] || agentId,
            totalTokens: meta.totalTokens || 0, updatedAt: meta.updatedAt, sessionFile: meta.sessionFile,
          });
        }
      } catch (_) {}
    }
  } catch (_) {}
  return result;
}

function readSession(sessionFile) {
  try {
    const lines = fs.readFileSync(sessionFile, 'utf-8').split('\n').filter(l => l.trim());
    const messages = [];
    for (const line of lines) {
      try {
        const obj = JSON.parse(line);
        if (obj.type !== 'message') continue;
        const msg = obj.message;
        if (!msg || !['user', 'assistant'].includes(msg.role)) continue;
        let text = typeof msg.content === 'string' ? msg.content :
          Array.isArray(msg.content) ? msg.content.filter(i => i.type === 'text').map(i => i.text || '').join('\n') :
          JSON.stringify(msg.content);
        if (!text.trim() || text.includes('[assistant turn failed before producing content]')) continue;
        messages.push({ role: msg.role, content: text, timestamp: obj.timestamp });
      } catch (_) {}
    }
    return messages;
  } catch (_) { return []; }
}

// ---------- IPC：配置管理 ----------
const PROJECT_ROOT = path.resolve(__dirname, '..');
const CONFIG_PATH = path.join(__dirname, 'config.json');

function loadConfig() {
  try {
    if (fs.existsSync(CONFIG_PATH)) {
      const cfg = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf-8'));
      if (!cfg.provider) cfg.provider = detectProvider(cfg);
      return cfg;
    }
  } catch (_) {}
  return { apiUrl: '', apiKey: '', model: '', temperature: 0.1, topP: 0.8, thinkingLevel: 'off', provider: '' };
}

// 从 apiUrl 反查 provider（旧配置兼容）
function detectProvider(config) {
  const url = (config.apiUrl || '').toLowerCase();
  for (const [key, p] of Object.entries(LLM_PROVIDERS)) {
    try { if (url.includes(new URL(p.apiUrl).hostname)) return key; } catch (_) {}
  }
  return '';
}

// 规范化：选预设后 apiUrl 缺 chat/completions 路径时用预设完整地址；模型为空补预设
function normalizeConfig(config) {
  const preset = LLM_PROVIDERS[config.provider];
  if (preset) {
    const url = (config.apiUrl || '').trim();
    if (!url || !url.toLowerCase().includes('chat/completions')) config.apiUrl = preset.apiUrl;
    if (!config.model) config.model = preset.model;
  }
  return config;
}

// 写 User 级环境变量（注册表 + WM_SETTINGCHANGE 广播，新进程生效）
function setUserEnvVars(vars) {
  return new Promise((resolve) => {
    const assigns = Object.entries(vars)
      .map(([n, v]) => `[Environment]::SetEnvironmentVariable('${n.replace(/'/g, "''")}','${String(v ?? '').replace(/'/g, "''")}','User')`)
      .join('; ');
    if (!assigns) return resolve({ ok: true });
    require('child_process').execFile('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', assigns],
      { timeout: 20000, windowsHide: true },
      (err) => resolve(err ? { ok: false, error: err.message } : { ok: true }));
  });
}

// 保存：config.json + 同步 User 环境变量 → OMM 脚本全局生效
async function saveConfig(config) {
  normalizeConfig(config);
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2), 'utf-8');
  const envVars = {};
  if (config.provider) envVars.MM_LLM_PROVIDER = config.provider;
  if (config.apiUrl) envVars.MM_LLM_API_URL = config.apiUrl;
  if (config.model) envVars.MM_LLM_MODEL = config.model;
  if (config.apiKey) envVars.MM_LLM_API_KEY = config.apiKey;
  const envResult = await setUserEnvVars(envVars);
  return { success: true, env: envResult };
}

// ---------- IPC：文件操作 ----------
function readWorkspaceFile(filename) {
  const filePath = path.join(detectMainWorkspace(), filename);
  try { return fs.existsSync(filePath) ? fs.readFileSync(filePath, 'utf-8') : ''; } catch (_) { return ''; }
}

function saveWorkspaceFile(filename, content) {
  const filePath = path.join(detectMainWorkspace(), filename);
  const backupPath = filePath + '.bak-' + new Date().toISOString().split('T')[0];
  if (fs.existsSync(filePath)) fs.copyFileSync(filePath, backupPath);
  fs.writeFileSync(filePath, content, 'utf-8');
  return { success: true, backup: backupPath };
}

const mainAgent = () => detectMainAgent();

function readAbstract(date) {
  const p = path.join(PROJECT_ROOT, 'memory-abstracted-daily', mainAgent(), `${date}-abstracted.md`);
  try { return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''; } catch (_) { return ''; }
}

function saveAbstract(date, content) {
  const dir = path.join(PROJECT_ROOT, 'memory-abstracted-daily', mainAgent());
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${date}-abstracted.md`), content, 'utf-8');
  return { success: true };
}

function listChunkingFiles(date) {
  const dir = path.join(PROJECT_ROOT, 'memory-chunking', mainAgent(), date);
  if (!fs.existsSync(dir)) return [];
  return fs.readdirSync(dir).filter(f => f.endsWith('.md') && f.includes('会话')).sort().map(f => ({
    name: f, path: path.join(dir, f), content: fs.readFileSync(path.join(dir, f), 'utf-8')
  }));
}

// ---------- IPC：LLM 调用 ----------
const https = require('https');
const http = require('http');

function callLLM({ prompt, system, config }) {
  const { apiUrl, apiKey, model, temperature, topP } = config;
  const messages = [];
  if (system) messages.push({ role: 'system', content: system });
  messages.push({ role: 'user', content: prompt });

  const payload = { model, messages, temperature: temperature || 0.1, stream: false };
  if (topP) payload.top_p = topP;

  return new Promise((resolve) => {
    const url = new URL(apiUrl);
    const client = url.protocol === 'https:' ? https : http;
    const req = client.request({
      hostname: url.hostname, port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
      timeout: 120000
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          if (result.choices?.[0]?.message) resolve({ success: true, content: result.choices[0].message.content.trim() });
          else resolve({ success: false, error: result.error?.message || '无效响应' });
        } catch (e) { resolve({ success: false, error: `解析失败: ${e.message}` }); }
      });
    });
    req.on('error', (e) => resolve({ success: false, error: `请求失败: ${e.message}` }));
    req.on('timeout', () => { req.destroy(); resolve({ success: false, error: '请求超时' }); });
    req.write(JSON.stringify(payload));
    req.end();
  });
}

// ---------- IPC：模型能力检测 ----------
async function detectModelCapabilities(config) {
  const { apiUrl, apiKey, model } = config;
  if (!apiUrl || !apiKey || !model) return { reasoning: false, levels: [] };

  // 发一个最小请求检测是否支持 reasoning
  const payload = {
    model,
    messages: [{ role: 'user', content: 'hi' }],
    max_tokens: 1,
    stream: false,
  };

  return new Promise((resolve) => {
    const url = new URL(apiUrl);
    const client = url.protocol === 'https:' ? https : http;
    const req = client.request({
      hostname: url.hostname, port: url.port || (url.protocol === 'https:' ? 443 : 80),
      path: url.pathname, method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
      timeout: 15000
    }, (res) => {
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try {
          const result = JSON.parse(data);
          // 检查是否有 reasoning/thinking 相关字段
          const msg = result.choices?.[0]?.message;
          const hasReasoning = !!(msg?.reasoning_content || msg?.reasoning || result.choices?.[0]?.reasoning);
          // 根据 provider 推断支持的档位
          const levels = hasReasoning ? ['low', 'medium', 'high'] : [];
          resolve({ reasoning: hasReasoning, levels });
        } catch (_) {
          resolve({ reasoning: false, levels: [] });
        }
      });
    });
    req.on('error', () => resolve({ reasoning: false, levels: [] }));
    req.on('timeout', () => { req.destroy(); resolve({ reasoning: false, levels: [] }); });
    req.write(JSON.stringify(payload));
    req.end();
  });
}

function parseEvolution(content) {
  const markers = { user: '===USER_MD_CONTENT===', memory: '===MEMORY_MD_CONTENT===', agents: '===AGENTS_MD_CONTENT===' };
  const result = {};
  for (const [key, marker] of Object.entries(markers)) {
    const startIdx = content.indexOf(marker);
    if (startIdx === -1) return null;
    const afterMarker = startIdx + marker.length;
    const nextMarker = Object.values(markers).find(m => { const i = content.indexOf(m, afterMarker); return i !== -1 && i > afterMarker; });
    result[key] = nextMarker ? content.substring(afterMarker, content.indexOf(nextMarker, afterMarker)).trim() : content.substring(afterMarker).trim();
  }
  return result;
}

// ---------- IPC 注册 ----------
ipcMain.handle('get-config', () => loadConfig());
ipcMain.handle('save-config', (_, config) => saveConfig(config));
ipcMain.handle('get-providers', () => Object.entries(LLM_PROVIDERS).map(([key, p]) => ({ key, name: p.name, apiUrl: p.apiUrl, model: p.model })));
ipcMain.handle('get-today', () => new Date().toISOString().split('T')[0]);
ipcMain.handle('list-sessions', () => listSessions());
ipcMain.handle('read-session', (_, sessionFile) => readSession(sessionFile));
ipcMain.handle('read-workspace-file', (_, filename) => readWorkspaceFile(filename));
ipcMain.handle('save-workspace-file', (_, filename, content) => saveWorkspaceFile(filename, content));
ipcMain.handle('read-abstract', (_, date) => readAbstract(date));
ipcMain.handle('save-abstract', (_, date, content) => saveAbstract(date, content));
ipcMain.handle('list-chunking-files', (_, date) => listChunkingFiles(date));
ipcMain.handle('call-llm', (_, params) => callLLM(params));
ipcMain.handle('parse-evolution', (_, content) => parseEvolution(content));
ipcMain.handle('detect-model', (_, config) => detectModelCapabilities(normalizeConfig(config)));
ipcMain.handle('show-message', async (_, opts) => {
  const { dialog } = require('electron');
  return dialog.showMessageBox(win, { type: opts.type || 'info', title: opts.title || '', message: opts.message || '', buttons: ['确定', '取消'] });
});

// ---------- IPC：任务日志 ----------
const LOG_DIR = path.join(PROJECT_ROOT, 'run_log');

ipcMain.handle('list-task-logs', () => {
  if (!fs.existsSync(LOG_DIR)) return [];
  return fs.readdirSync(LOG_DIR).filter(f => f.endsWith('.log')).sort().reverse();
});

ipcMain.handle('read-task-log', (_, filename) => {
  const p = path.join(LOG_DIR, filename);
  try { return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''; } catch (_) { return ''; }
});

ipcMain.handle('read-latest-status', () => {
  const p = path.join(LOG_DIR, 'latest_status.txt');
  try { return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''; } catch (_) { return ''; }
});

// ---------- IPC：任务执行 ----------
const { spawn } = require('child_process');
const SCRIPTS_DIR = (PROJECT_ROOT / '脚本').toString && fs.existsSync(path.join(PROJECT_ROOT, '脚本'))
  ? path.join(PROJECT_ROOT, '脚本') : path.join(PROJECT_ROOT, 'scripts');
const PYTHON = 'python';

ipcMain.handle('run-task', (_, { type, dryRun }) => {
  return new Promise((resolve) => {
    const scriptMap = {
      daily: path.join(SCRIPTS_DIR, 'daily', 'main.py'),
      weekly: path.join(SCRIPTS_DIR, 'weekly', 'main.py'),
      monthly: path.join(SCRIPTS_DIR, 'monthly', 'main.py'),
    };
    const script = scriptMap[type] || scriptMap.daily;
    const args = [script];
    if (dryRun) args.push('--dry-run');
    // 注入当前控制台 LLM 配置 → 控制台内跑任务立即生效（不依赖进程环境刷新）
    const llmEnv = {};
    try {
      const cfg = normalizeConfig(loadConfig());
      if (cfg.provider) llmEnv.MM_LLM_PROVIDER = cfg.provider;
      if (cfg.apiUrl) llmEnv.MM_LLM_API_URL = cfg.apiUrl;
      if (cfg.model) llmEnv.MM_LLM_MODEL = cfg.model;
      if (cfg.apiKey) llmEnv.MM_LLM_API_KEY = cfg.apiKey;
    } catch (_) {}
    const child = spawn(PYTHON, args, { windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, ...llmEnv } });
    let stdout = '', stderr = '';
    child.stdout.on('data', d => stdout += d);
    child.stderr.on('data', d => stderr += d);
    child.on('close', (code) => {
      resolve({ success: code === 0, output: stdout, error: stderr || (code !== 0 ? `exit ${code}` : '') });
    });
    child.on('error', (e) => resolve({ success: false, error: e.message }));
  });
});

// ---------- 生命周期 ----------
app.setAppUserModelId('com.openclaw.omm-console');
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => { if (win) { win.show(); win.focus(); } });
  app.whenReady().then(() => { createWindow(); });
}
