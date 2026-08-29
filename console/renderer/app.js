// ===== 全局状态 =====
let currentConfig = {};
let providers = [];

// ===== 初始化 =====
document.addEventListener('DOMContentLoaded', async () => {
  try { await loadProviders(); } catch (_) { log('服务商列表加载失败', 'warning'); }
  try { await loadConfig(); } catch (_) { log('配置加载失败，使用默认值', 'warning'); }
  setupEventListeners();
  initCalendar();
  log('控制台启动', 'info');
});

// ===== 服务商预设 =====
async function loadProviders() {
  try {
    providers = await window.electronAPI.getProviders();
    const select = document.getElementById('provider');
    providers.forEach(p => {
      const opt = document.createElement('option');
      opt.value = p.key;
      opt.textContent = p.name;
      select.appendChild(opt);
    });
  } catch (_) {
    providers = [];
  }
}

function applyProviderPreset() {
  const key = document.getElementById('provider').value;
  const p = providers.find(x => x.key === key);
  if (!p) return;
  document.getElementById('apiUrl').value = p.apiUrl;
  document.getElementById('model').value = p.model;
}

// ===== 配置 =====
async function loadConfig() {
  currentConfig = await window.electronAPI.getConfig();
  document.getElementById('provider').value = currentConfig.provider || '';
  document.getElementById('apiUrl').value = currentConfig.apiUrl || '';
  document.getElementById('apiKey').value = currentConfig.apiKey || '';
  document.getElementById('model').value = currentConfig.model || '';
  document.getElementById('temperature').value = currentConfig.temperature || 0.1;
  document.getElementById('topP').value = currentConfig.topP || 0.8;
  document.getElementById('thinkingLevel').value = currentConfig.thinkingLevel || 'off';
  updateReasoningStatus();
}

async function saveConfig() {
  currentConfig = {
    provider: document.getElementById('provider').value,
    apiUrl: document.getElementById('apiUrl').value,
    apiKey: document.getElementById('apiKey').value,
    model: document.getElementById('model').value,
    temperature: parseFloat(document.getElementById('temperature').value) || 0.1,
    topP: parseFloat(document.getElementById('topP').value) || 0.8,
    thinkingLevel: document.getElementById('thinkingLevel').value,
  };
  const result = await window.electronAPI.saveConfig(currentConfig);
  if (result.env && !result.env.ok) {
    log(`配置已保存，但同步环境变量失败: ${result.env.error}`, 'error');
  } else {
    log('配置已保存，已同步为 OMM 全局 LLM（新任务生效）', 'success');
  }
  currentConfig = await window.electronAPI.getConfig();
  updateReasoningStatus();
}

// ===== 推理档位检测 =====
let detectTimer = null;
async function updateReasoningStatus() {
  const badge = document.getElementById('reasoningStatus');
  const select = document.getElementById('thinkingLevel');
  badge.textContent = '检测中...';
  badge.className = 'badge';

  if (!currentConfig.apiUrl || !currentConfig.apiKey || !currentConfig.model) {
    badge.textContent = '未配置';
    return;
  }

  try {
    const result = await window.electronAPI.detectModel(currentConfig);
    if (result.reasoning) {
      badge.textContent = '支持';
      badge.className = 'badge badge-success';
      select.disabled = false;
    } else {
      badge.textContent = '不支持';
      badge.className = 'badge badge-muted';
      select.value = 'off';
      select.disabled = true;
    }
  } catch (_) {
    badge.textContent = '检测失败';
    badge.className = 'badge badge-muted';
  }
}

// ===== 事件 =====
function setupEventListeners() {
  document.getElementById('saveConfig').addEventListener('click', saveConfig);
  document.getElementById('toggleKey').addEventListener('click', () => {
    const input = document.getElementById('apiKey');
    input.type = input.type === 'password' ? 'text' : 'password';
  });

  // 模型名变化时重新检测
  document.getElementById('model').addEventListener('change', () => {
    clearTimeout(detectTimer);
    detectTimer = setTimeout(updateReasoningStatus, 1000);
  });

  // 服务商变化：自动填充 API 地址 + 模型
  document.getElementById('provider').addEventListener('change', () => {
    applyProviderPreset();
    clearTimeout(detectTimer);
    detectTimer = setTimeout(updateReasoningStatus, 1000);
  });

  // 日期（自定义日历）
  document.getElementById('calPrev').addEventListener('click', () => shiftMonth(-1));
  document.getElementById('calNext').addEventListener('click', () => shiftMonth(1));
  // 点击选日
  document.getElementById('calDays').addEventListener('click', (e) => {
    const cell = e.target.closest('.cal-cell');
    if (!cell || !cell.dataset.date) return;
    const [y, m, d] = cell.dataset.date.split('-').map(Number);
    selectedDate = new Date(y, m - 1, d);
    renderCalendar();
  });

  // 标签页
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => switchTab(tab.dataset.tab));
  });

  // Workspace
  document.getElementById('btnLoadWorkspace').addEventListener('click', loadWorkspace);
  document.getElementById('btnSaveWorkspace').addEventListener('click', saveWorkspace);

  // 日志
  document.getElementById('btnClearLog').addEventListener('click', () => {
    document.getElementById('logArea').innerHTML = '';
  });

  // 任务日志
  document.getElementById('btnRefreshLogs').addEventListener('click', loadTaskLogList);
  document.getElementById('taskLogSelect').addEventListener('change', loadTaskLog);
  document.getElementById('btnRefreshStatus').addEventListener('click', loadLatestStatus);

  // 任务执行
  document.getElementById('btnRunDaily').addEventListener('click', () => runTask('daily', false));
  document.getElementById('btnRunWeekly').addEventListener('click', () => runTask('weekly', false));
  document.getElementById('btnRunMonthly').addEventListener('click', () => runTask('monthly', false));
  document.getElementById('btnRunDry').addEventListener('click', () => runTask('daily', true));
}

// ===== 日期（自定义日历） =====
let calYear = 0, calMonth = 0;
let selectedDate = null; // Date

function initCalendar() {
  const now = new Date();
  calYear = now.getFullYear();
  calMonth = now.getMonth();
  selectedDate = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  renderCalendar();
}

function renderCalendar() {
  const title = document.getElementById('calTitle');
  const daysBox = document.getElementById('calDays');
  title.textContent = `${calYear}年${calMonth + 1}月`;

  const today = new Date();
  const todayStr = fmtDate(today);
  const firstDay = new Date(calYear, calMonth, 1);
  // 周一为一周起点：getDay() 0=周日 → 偏移 (getDay()+6)%7
  const offset = (firstDay.getDay() + 6) % 7;
  const daysInMonth = new Date(calYear, calMonth + 1, 0).getDate();
  const selectedStr = selectedDate ? fmtDate(selectedDate) : '';

  let html = '';
  // 上月补位（灰显）
  const prevMonthDays = new Date(calYear, calMonth, 0).getDate();
  for (let i = offset - 1; i >= 0; i--) {
    const d = prevMonthDays - i;
    html += `<span class="cal-cell cal-dim">${d}</span>`;
  }
  // 当月
  for (let d = 1; d <= daysInMonth; d++) {
    const ds = `${calYear}-${String(calMonth + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const cls = ['cal-cell'];
    if (ds === todayStr) cls.push('cal-today');
    if (ds === selectedStr) cls.push('cal-selected');
    html += `<span class="${cls.join(' ')}" data-date="${ds}">${d}</span>`;
  }
  // 下月补位
  const used = offset + daysInMonth;
  const remain = (7 - (used % 7)) % 7;
  for (let d = 1; d <= remain; d++) {
    html += `<span class="cal-cell cal-dim">${d}</span>`;
  }
  daysBox.innerHTML = html;
}

function fmtDate(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function shiftMonth(delta) {
  calMonth += delta;
  if (calMonth < 0) { calMonth = 11; calYear--; }
  if (calMonth > 11) { calMonth = 0; calYear++; }
  renderCalendar();
}

function switchTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${name}`));
  if (name === 'tasklog') loadTaskLogList();
}

function log(msg, type = 'info') {
  const area = document.getElementById('logArea');
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  const time = new Date().toLocaleTimeString('zh-CN');
  entry.innerHTML = `<span class="log-time">[${time}]</span> ${msg}`;
  area.appendChild(entry);
  area.scrollTop = area.scrollHeight;
}

function updateStatus(text) {
  document.getElementById('status').textContent = text;
}

// ===== Workspace =====
async function loadWorkspace() {
  const filename = document.getElementById('workspaceFile').value;
  updateStatus(`加载 ${filename}...`);
  try {
    const content = await window.electronAPI.readWorkspaceFile(filename);
    document.getElementById('workspaceContent').value = content;
    document.getElementById('workspaceInfo').textContent = `${content.length} 字符`;
    log(`${filename} 已加载`, 'success');
    updateStatus('就绪');
  } catch (e) {
    log(`加载失败: ${e.message}`, 'error');
  }
}

async function saveWorkspace() {
  const filename = document.getElementById('workspaceFile').value;
  const content = document.getElementById('workspaceContent').value;
  if (!content.trim()) { log('内容为空', 'warning'); return; }
  try {
    await window.electronAPI.saveWorkspaceFile(filename, content);
    log(`${filename} 已保存`, 'success');
  } catch (e) {
    log(`保存失败: ${e.message}`, 'error');
  }
}

// ===== 任务日志 =====
async function loadTaskLogList() {
  const select = document.getElementById('taskLogSelect');
  try {
    const logs = await window.electronAPI.listTaskLogs();
    select.innerHTML = '<option value="">选择日志文件...</option>';
    logs.forEach(l => {
      const opt = document.createElement('option');
      opt.value = l;
      opt.textContent = l;
      select.appendChild(opt);
    });
  } catch (e) {
    log(`加载日志列表失败: ${e.message}`, 'error');
  }
}

async function loadTaskLog() {
  const filename = document.getElementById('taskLogSelect').value;
  if (!filename) return;
  try {
    const content = await window.electronAPI.readTaskLog(filename);
    renderLogContent(content || '（空文件）');
  } catch (e) {
    log(`加载日志失败: ${e.message}`, 'error');
  }
}

async function loadLatestStatus() {
  try {
    const status = await window.electronAPI.readLatestStatus();
    renderLogContent(status || '（无状态文件）');
  } catch (e) {
    log(`加载状态失败: ${e.message}`, 'error');
  }
}

function renderLogContent(text) {
  const container = document.getElementById('taskLogContent');
  container.innerHTML = '';
  const lines = text.split('\n');
  for (const line of lines) {
    const div = document.createElement('div');
    div.className = 'log-line';
    if (line.includes('ERROR') || line.includes('失败') || line.includes('异常')) {
      div.className += ' log-error';
    } else if (line.includes('WARNING') || line.includes('警告')) {
      div.className += ' log-warn';
    } else if (line.includes('INFO') || line.includes('完成') || line.includes('成功')) {
      div.className += ' log-info';
    } else if (line.startsWith('===')) {
      div.className += ' log-sep';
    }
    div.textContent = line || '\u00A0';
    container.appendChild(div);
  }
}

// ===== 任务执行 =====
async function runTask(type, dryRun) {
  const labels = { daily: '每日', weekly: '每周', monthly: '每月' };
  const label = dryRun ? 'Dry Run' : `${labels[type] || '每日'}任务`;
  log(`开始执行 ${label}...`, 'info');
  updateStatus(`执行 ${label}...`);
  try {
    const result = await window.electronAPI.runTask({ type, dryRun });
    if (result.success) {
      log(`${label} 执行完成`, 'success');
    } else {
      log(`${label} 失败: ${result.error}`, 'error');
    }
    updateStatus('就绪');
  } catch (e) {
    log(`执行失败: ${e.message}`, 'error');
    updateStatus('就绪');
  }
}
