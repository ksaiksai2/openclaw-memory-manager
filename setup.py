#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenClaw Memory Manager - 安装器
=================================
双击运行，浏览器自动打开安装界面。
填写配置后一键完成安装。

用法:
    python setup.py          # 启动安装器（自动打开浏览器）
    python setup.py --port 9999  # 指定端口
"""

import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs

PORT = int(next((sys.argv[i+1] for i, a in enumerate(sys.argv) if a == "--port" and i+1 < len(sys.argv)), "8765"))
SCRIPT_DIR = Path(__file__).resolve().parent / "scripts"
PROJECT_ROOT = Path(__file__).resolve().parent

# ────────────────────── 工具函数 ──────────────────────

PROVIDERS = {
    "deepseek": {"name": "DeepSeek", "url": "https://api.deepseek.com/v1/chat/completions", "model": "deepseek-v4-flash"},
    "openai":   {"name": "OpenAI",   "url": "https://api.openai.com/v1/chat/completions",     "model": "gpt-4o-mini"},
    "moonshot": {"name": "Moonshot (Kimi)", "url": "https://api.moonshot.cn/v1/chat/completions", "model": "moonshot-v1-8k"},
    "qwen":     {"name": "通义千问",  "url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "model": "qwen-plus"},
    "zhipu":    {"name": "智谱 GLM",  "url": "https://open.bigmodel.cn/api/paas/v4/chat/completions", "model": "glm-4-flash"},
    "siliconflow": {"name": "SiliconFlow", "url": "https://api.siliconflow.cn/v1/chat/completions", "model": "deepseek-ai/DeepSeek-V3"},
    "custom":   {"name": "自定义 (OpenAI 兼容)", "url": "", "model": ""},
}

def find_pythonw():
    python_dir = Path(sys.executable).parent
    for name in ("pythonw.exe", "python.exe"):
        p = python_dir / name
        if p.exists():
            return str(p)
    return sys.executable

def detect_paths():
    """自动检测路径。返回 {字段: 值}，值为 None 表示用 hint 占位。"""
    home = Path.home()
    openclaw = home / ".openclaw"
    username = os.environ.get("USERNAME", "YourUsername")

    # 检测实际存在的路径 → 预填
    # 不存在的 → 返回 None，前端用 hint 展示
    conv = openclaw / "memory-tdai" / "conversations"
    ws = openclaw / "workspace"

    return {
        "provider": "deepseek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-flash",
        "openclaw_root": str(openclaw) if openclaw.exists() else None,
        "conversations_dir": str(conv) if conv.exists() else None,
        "workspace_dir": str(ws) if ws.exists() else None,
        "python_path": find_pythonw(),
        "timeout": "90",
        "max_retries": "2",
        # hint 示例（前端灰色展示）
        "_hint_openclaw_root": f"C:\\Users\\{username}\\.openclaw",
        "_hint_conversations_dir": f"C:\\Users\\{username}\\.openclaw\\memory-tdai\\conversations",
        "_hint_workspace_dir": f"C:\\Users\\{username}\\.openclaw\\workspace",
    }

def check_api_key(api_key, api_url, model):
    """测试 API Key 是否有效。"""
    import urllib.request
    import urllib.error
    payload = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    req = urllib.request.Request(api_url, data=payload, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return True, "连接成功"
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return False, "API Key 无效（401）"
        return False, f"HTTP {e.code}"
    except Exception as e:
        return False, str(e)

def check_paths(config):
    """检查路径是否有效。"""
    issues = []
    conv = Path(config["conversations_dir"])
    if not conv.exists():
        issues.append(f"会话记录目录不存在: {conv}")
    ws = Path(config["workspace_dir"])
    if not ws.exists():
        issues.append(f"workspace 目录不存在: {ws}")
    # 检查三个 md 文件
    for name in ("USER.md", "MEMORY.md", "AGENTS.md"):
        if not (ws / name).exists():
            issues.append(f"缺少文件: {ws / name}")
    return issues

def do_install(config):
    """执行安装步骤。返回 (成功, 消息列表)。"""
    msgs = []

    # 1. 设置环境变量（空值不写入，保留自动推导）
    env_vars = {
        "MM_LLM_API_KEY": config["api_key"],
        "MM_LLM_PROVIDER": config.get("provider", "deepseek"),
        "MM_LLM_API_URL": config.get("api_url", ""),
        "MM_LLM_MODEL": config.get("model", ""),
        "MM_LLM_TIMEOUT": config.get("timeout", "90"),
        "MM_LLM_MAX_RETRIES": config.get("max_retries", "2"),
        "MM_OPENCLAW_ROOT": config.get("openclaw_root", ""),
        "MM_CONVERSATIONS_DIR": config.get("conversations_dir", ""),
        "MM_WORKSPACE_DIR": config.get("workspace_dir", ""),
    }
    # 兼容旧变量名
    env_vars["MEMORY_MANAGER_DEEPSEEK_API_KEY"] = config["api_key"]
    # 过滤空值（不覆盖自动推导默认值）
    env_vars = {k: v for k, v in env_vars.items() if v}

    for k, v in env_vars.items():
        try:
            subprocess.run(
                ["powershell", "-Command",
                 f'[System.Environment]::SetEnvironmentVariable("{k}", "{v}", "User")'],
                capture_output=True, timeout=10
            )
            os.environ[k] = v
            msgs.append(f"✅ 环境变量 {k} 已设置")
        except Exception as e:
            msgs.append(f"❌ 设置环境变量 {k} 失败: {e}")

    # 2. 写入注册表（计划任务回退读取）
    for k, v in env_vars.items():
        try:
            subprocess.run(
                ["powershell", "-Command",
                 f'Set-ItemProperty -Path "HKCU:\\Environment" -Name "{k}" -Value "{v}"'],
                capture_output=True, timeout=10
            )
        except Exception:
            pass

    # 3. 注册计划任务
    pythonw = config.get("python_path", find_pythonw())
    run_all = str(SCRIPT_DIR / "run_all.py")
    try:
        # 删除旧任务
        subprocess.run(
            ["schtasks", "/Delete", "/TN", "OpenClaw-MemoryManager", "/F"],
            capture_output=True, timeout=10
        )
        # 创建新任务
        result = subprocess.run(
            ["schtasks", "/Create",
             "/TN", "OpenClaw-MemoryManager",
             "/TR", f'"{pythonw}" "{run_all}"',
             "/SC", "DAILY", "/ST", "22:30",
             "/F", "/DU", "02:00"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            msgs.append("✅ 计划任务 OpenClaw-MemoryManager 已注册（每天 22:30）")
        else:
            msgs.append(f"❌ 计划任务注册失败: {result.stderr.strip()}")
    except Exception as e:
        msgs.append(f"❌ 计划任务注册异常: {e}")

    # 4. 创建 NotificationAgent 启动快捷方式
    try:
        startup_dir = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup"
        lnk_path = startup_dir / "MemoryManager-NotifyAgent.lnk"
        agent_script = str(SCRIPT_DIR / "notify_agent.py")
        ps_cmd = (
            f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut("{lnk_path}"); '
            f'$s.TargetPath = "{pythonw}"; '
            f'$s.Arguments = "{agent_script}"; '
            f'$s.Save()'
        )
        result = subprocess.run(
            ["powershell", "-Command", ps_cmd],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            msgs.append("✅ NotificationAgent 开机自启已配置")
        else:
            msgs.append(f"⚠️ 快捷方式创建失败: {result.stderr.strip()}")
    except Exception as e:
        msgs.append(f"⚠️ 快捷方式创建异常: {e}")

    # 5. 启动 NotificationAgent
    try:
        agent_script = str(SCRIPT_DIR / "notify_agent.py")
        subprocess.Popen(
            [pythonw, agent_script],
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )
        msgs.append("✅ NotificationAgent 已启动")
    except Exception as e:
        msgs.append(f"⚠️ NotificationAgent 启动失败: {e}")

    # 6. 创建输出目录
    for d in ("memory-chunking", "memory-abstracted-daily", "memory-abstracted-weekly",
              "memory-abstracted-monthly", "run_log"):
        (PROJECT_ROOT / d).mkdir(exist_ok=True)
    msgs.append("✅ 输出目录已创建")

    return True, msgs


# ────────────────────── HTML ──────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw Memory Manager - 安装器</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", sans-serif; background: #f5f5f5; color: #333; padding: 20px; }
.container { max-width: 640px; margin: 0 auto; }
h1 { font-size: 1.5em; margin-bottom: 4px; }
.subtitle { color: #666; font-size: 0.9em; margin-bottom: 24px; }
.card { background: #fff; border-radius: 8px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
.card h2 { font-size: 1.1em; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 1px solid #eee; }
.field { margin-bottom: 14px; }
.field label { display: block; font-size: 0.85em; font-weight: 600; margin-bottom: 4px; color: #555; }
.field input { width: 100%; padding: 8px 10px; border: 1px solid #ddd; border-radius: 4px; font-size: 0.9em; font-family: monospace; }
.field input:focus { outline: none; border-color: #4a90d9; }
.field input::placeholder { color: #bbb; font-style: italic; }
.field .hint { font-size: 0.78em; color: #999; margin-top: 3px; }
.field-with-btn { display: flex; gap: 6px; align-items: stretch; }
.field-with-btn input { flex: 1; }
.apply-btn { padding: 0 10px; border: 1px solid #ddd; border-radius: 4px; background: #f8f8f8; color: #666; font-size: 0.75em; cursor: pointer; white-space: nowrap; }
.apply-btn:hover { background: #eee; border-color: #bbb; }
.row { display: flex; gap: 12px; }
.row .field { flex: 1; }
.btn { display: inline-block; padding: 10px 20px; border: none; border-radius: 6px; font-size: 0.95em; cursor: pointer; font-weight: 600; }
.btn-primary { background: #4a90d9; color: #fff; }
.btn-primary:hover { background: #3a7bc8; }
.btn-primary:disabled { background: #aaa; cursor: not-allowed; }
.btn-secondary { background: #eee; color: #333; }
.btn-secondary:hover { background: #ddd; }
.btn-row { display: flex; gap: 10px; margin-top: 8px; }
.status { margin-top: 16px; padding: 12px; border-radius: 6px; font-size: 0.88em; white-space: pre-wrap; font-family: monospace; max-height: 300px; overflow-y: auto; display: none; }
.status.ok { background: #e8f5e9; color: #2e7d32; display: block; }
.status.err { background: #ffebee; color: #c62828; display: block; }
.status.info { background: #e3f2fd; color: #1565c0; display: block; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.75em; font-weight: 600; }
.badge-ok { background: #e8f5e9; color: #2e7d32; }
.badge-err { background: #ffebee; color: #c62828; }
.badge-warn { background: #fff3e0; color: #e65100; }
.footer { text-align: center; color: #999; font-size: 0.8em; margin-top: 20px; }
</style>
</head>
<body>
<div class="container">
<h1>🧠 OpenClaw Memory Manager</h1>
<p class="subtitle">基于 TencentDB Agent Memory 的自动化记忆整理方案</p>

<div class="card">
<h2>1. LLM Provider</h2>
<div class="field">
<label>Provider</label>
<select id="provider" onchange="onProviderChange()" style="width:100%;padding:8px 10px;border:1px solid #ddd;border-radius:4px;font-size:0.9em;">
<option value="deepseek">DeepSeek</option>
<option value="openai">OpenAI</option>
<option value="moonshot">Moonshot (Kimi)</option>
<option value="qwen">通义千问</option>
<option value="zhipu">智谱 GLM</option>
<option value="siliconflow">SiliconFlow</option>
<option value="custom">自定义 (OpenAI 兼容)</option>
</select>
</div>
<div class="field">
<label>API Key</label>
<input type="password" id="api_key" placeholder="sk-xxxxxxxxxxxxxxxx" />
</div>
<div class="field" id="api_url_field" style="display:none;">
<label>API URL</label>
<input type="text" id="api_url" placeholder="https://api.example.com/v1/chat/completions" />
</div>
<div class="field">
<label>模型名称</label>
<input type="text" id="model" />
</div>
<div class="btn-row">
<button class="btn btn-secondary" onclick="testKey()">测试连接</button>
<span id="key_status"></span>
</div>
</div>

<div class="card">
<h2>2. 路径配置</h2>
<div class="field">
<label>OpenClaw 根目录</label>
<div class="field-with-btn">
<input type="text" id="openclaw_root" />
<button class="apply-btn" onclick="applyPath('openclaw_root')">自动填入</button>
</div>
<div class="hint" id="hint_openclaw_root">包含 workspace/ 和 memory-tdai/ 的目录</div>
</div>
<div class="field">
<label>会话记录目录</label>
<div class="field-with-btn">
<input type="text" id="conversations_dir" />
<button class="apply-btn" onclick="applyPath('conversations_dir')">自动填入</button>
</div>
<div class="hint" id="hint_conversations_dir">TencentDB Agent Memory 产生的 JSONL 文件所在目录</div>
</div>
<div class="field">
<label>Workspace 目录</label>
<div class="field-with-btn">
<input type="text" id="workspace_dir" />
<button class="apply-btn" onclick="applyPath('workspace_dir')">自动填入</button>
</div>
<div class="hint" id="hint_workspace_dir">USER.md / MEMORY.md / AGENTS.md 所在目录</div>
</div>
<div class="btn-row">
<button class="btn btn-secondary" onclick="checkPaths()">检查路径</button>
<span id="path_status"></span>
</div>
</div>

<div class="card">
<h2>3. 高级配置</h2>
<div class="field">
<label>Python 路径</label>
<input type="text" id="python_path" />
<div class="hint">pythonw.exe 的完整路径</div>
</div>
<div class="row">
<div class="field">
<label>超时（秒）</label>
<input type="number" id="timeout" value="90" />
</div>
<div class="field">
<label>重试次数</label>
<input type="number" id="max_retries" value="2" />
</div>
</div>
</div>

<div class="card">
<h2>4. 安装</h2>
<p style="font-size:0.88em;color:#666;margin-bottom:12px;">
点击安装将执行以下操作：<br>
• 设置环境变量（用户级）<br>
• 注册计划任务（每天 22:30）<br>
• 配置 NotificationAgent 开机自启<br>
• 启动 NotificationAgent
</p>
<button class="btn btn-primary" id="install_btn" onclick="doInstall()">🚀 一键安装</button>
<div id="install_status" class="status"></div>
</div>

<div class="card" id="result_card" style="display:none;">
<h2>安装结果</h2>
<div id="result_content"></div>
</div>

<div class="footer">
OpenClaw Memory Manager · <a href="https://github.com" target="_blank">GitHub</a>
</div>
</div>

<script>
const PROVIDERS = {
    "deepseek":    {name:"DeepSeek",          url:"https://api.deepseek.com/v1/chat/completions",                    model:"deepseek-v4-flash"},
    "openai":      {name:"OpenAI",            url:"https://api.openai.com/v1/chat/completions",                      model:"gpt-4o-mini"},
    "moonshot":    {name:"Moonshot (Kimi)",   url:"https://api.moonshot.cn/v1/chat/completions",                     model:"moonshot-v1-8k"},
    "qwen":        {name:"通义千问",           url:"https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", model:"qwen-plus"},
    "zhipu":       {name:"智谱 GLM",          url:"https://open.bigmodel.cn/api/paas/v4/chat/completions",           model:"glm-4-flash"},
    "siliconflow": {name:"SiliconFlow",       url:"https://api.siliconflow.cn/v1/chat/completions",                  model:"deepseek-ai/DeepSeek-V3"},
    "custom":      {name:"自定义",             url:"", model:""},
};

function applyPath(fieldId) {
    const el = document.getElementById(fieldId);
    // 从 placeholder 中提取路径（去掉"如："前缀）
    const hint = el.placeholder.replace(/^如：/, '');
    if (hint) el.value = hint;
}

function onProviderChange() {
    const p = document.getElementById('provider').value;
    const preset = PROVIDERS[p];
    // model 和 api_url 用 placeholder 展示，不预填
    document.getElementById('model').value = '';
    document.getElementById('model').placeholder = '如：' + preset.model;
    document.getElementById('api_url').value = '';
    document.getElementById('api_url').placeholder = preset.url || 'https://api.example.com/v1/chat/completions';
    document.getElementById('api_url_field').style.display = (p === 'custom') ? 'block' : 'none';
}

async function loadConfig() {
    const r = await fetch('/api/config');
    const d = await r.json();
    document.getElementById('provider').value = d.provider || 'deepseek';
    onProviderChange();
    document.getElementById('timeout').value = d.timeout || '90';
    document.getElementById('max_retries').value = d.max_retries || '2';

    // 所有路径和模型字段：始终用 placeholder 提示，不预填
    const hintFields = ['openclaw_root', 'conversations_dir', 'workspace_dir'];
    for (const f of hintFields) {
        const el = document.getElementById(f);
        el.value = '';
        el.placeholder = '如：' + (d['_hint_' + f] || '');
    }
    // python_path 用自动检测值作为 placeholder
    const pythonEl = document.getElementById('python_path');
    pythonEl.value = '';
    pythonEl.placeholder = '如：' + (d.python_path || 'D:\\Scripts\\pythonw.exe');
}

async function testKey() {
    const key = document.getElementById('api_key').value.trim();
    const api_url = document.getElementById('api_url').value.trim();
    const model = document.getElementById('model').value.trim();
    if (!key) { document.getElementById('key_status').innerHTML = '<span class="badge badge-err">请输入 Key</span>'; return; }
    document.getElementById('key_status').innerHTML = '<span class="badge badge-warn">测试中...</span>';
    const r = await fetch('/api/test-key', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify({api_key: key, api_url: api_url, model: model})
    });
    const d = await r.json();
    if (d.ok) {
        document.getElementById('key_status').innerHTML = '<span class="badge badge-ok">✓ ' + d.msg + '</span>';
    } else {
        document.getElementById('key_status').innerHTML = '<span class="badge badge-err">✗ ' + d.msg + '</span>';
    }
}

async function checkPaths() {
    const config = getConfig();
    const r = await fetch('/api/check-paths', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(config)
    });
    const d = await r.json();
    if (d.issues.length === 0) {
        document.getElementById('path_status').innerHTML = '<span class="badge badge-ok">✓ 路径正常</span>';
    } else {
        document.getElementById('path_status').innerHTML = '<span class="badge badge-err">✗ ' + d.issues[0] + '</span>';
    }
}

function getConfig() {
    return {
        provider: document.getElementById('provider').value,
        api_key: document.getElementById('api_key').value.trim(),
        api_url: document.getElementById('api_url').value.trim(),
        model: document.getElementById('model').value.trim(),
        openclaw_root: document.getElementById('openclaw_root').value.trim(),
        conversations_dir: document.getElementById('conversations_dir').value.trim(),
        workspace_dir: document.getElementById('workspace_dir').value.trim(),
        python_path: document.getElementById('python_path').value.trim(),
        timeout: document.getElementById('timeout').value.trim(),
        max_retries: document.getElementById('max_retries').value.trim(),
    };
}

async function doInstall() {
    const config = getConfig();
    if (!config.api_key) { alert('请填写 API Key'); return; }

    const btn = document.getElementById('install_btn');
    const status = document.getElementById('install_status');
    btn.disabled = true;
    btn.textContent = '安装中...';
    status.className = 'status info';
    status.textContent = '正在安装...\n';

    const r = await fetch('/api/install', {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(config)
    });
    const d = await r.json();

    btn.disabled = false;
    btn.textContent = '🚀 一键安装';

    if (d.ok) {
        status.className = 'status ok';
        status.textContent = d.msgs.join('\n') + '\n\n✅ 安装完成！每天 22:30 自动执行记忆整理。';
    } else {
        status.className = 'status err';
        status.textContent = d.msgs.join('\n');
    }
}

loadConfig();
</script>
</body>
</html>"""


# ────────────────────── HTTP Handler ──────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # 静默日志

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._html(HTML_PAGE)
        elif self.path == "/api/config":
            self._json(detect_paths())
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/api/test-key":
            ok, msg = check_api_key(
                body.get("api_key", ""),
                body.get("api_url", ""),
                body.get("model", "")
            )
            self._json({"ok": ok, "msg": msg})

        elif self.path == "/api/check-paths":
            issues = check_paths(body)
            self._json({"issues": issues})

        elif self.path == "/api/install":
            ok, msgs = do_install(body)
            self._json({"ok": ok, "msgs": msgs})

        else:
            self.send_error(404)


# ────────────────────── 启动 ──────────────────────

def main():
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"安装器已启动: {url}")
    print("浏览器将自动打开，如果没有请手动访问上述地址。")
    print("按 Ctrl+C 退出。")

    # 延迟打开浏览器
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出。")
        server.server_close()


if __name__ == "__main__":
    main()
