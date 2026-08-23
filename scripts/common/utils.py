#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
共享工具模块 - OpenClaw 记忆整理
=================================
提供：文件读写、DeepSeek API 调用、系统通知、日志记录
日/周/月三个周期脚本共用此模块
"""

import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ────────────────────── 路径配置 ──────────────────────
# 优先级：环境变量 > 自动推导（基于脚本位置）> 默认值
#
# 自动推导逻辑：
#   脚本位于 <PROJECT_ROOT>/scripts/common/utils.py
#   向上两级即为 PROJECT_ROOT


def _auto_detect_project_root() -> Path:
    """从脚本位置向上推导项目根目录。"""
    return Path(__file__).resolve().parent.parent.parent


def _auto_detect_openclaw_root() -> Path:
    """推导 OpenClaw 根目录（~/.openclaw）。"""
    return Path.home() / ".openclaw"


PROJECT_ROOT = Path(os.environ.get(
    "MM_PROJECT_ROOT",
    str(_auto_detect_project_root())
))

OPENCLAW_ROOT = Path(os.environ.get(
    "MM_OPENCLAW_ROOT",
    str(_auto_detect_openclaw_root())
))

# 输入源：会话记录目录（TencentDB Agent Memory 插件产生）
CONVERSATIONS_DIR = Path(os.environ.get(
    "MM_CONVERSATIONS_DIR",
    str(OPENCLAW_ROOT / "memory-tdai" / "conversations")
))

# workspace 目录（USER.md / MEMORY.md / AGENTS.md 所在）
WORKSPACE_DIR = Path(os.environ.get(
    "MM_WORKSPACE_DIR",
    str(OPENCLAW_ROOT / "workspace")
))

# 输出目录（相对于项目根目录）
CHUNKING_DIR = PROJECT_ROOT / "memory-chunking"
ABSTRACTED_DAILY_DIR = PROJECT_ROOT / "memory-abstracted-daily"
ABSTRACTED_WEEKLY_DIR = PROJECT_ROOT / "memory-abstracted-weekly"
ABSTRACTED_MONTHLY_DIR = PROJECT_ROOT / "memory-abstracted-monthly"
LOG_DIR = PROJECT_ROOT / "run_log"

# 脚本目录
# 脚本目录（兼容中文目录名"脚本"和英文"scripts"）
SCRIPTS_DIR = PROJECT_ROOT / "脚本" if (PROJECT_ROOT / "脚本").exists() else PROJECT_ROOT / "scripts"
CHUNKING_SCRIPT = SCRIPTS_DIR / "daily" / "convert_jsonl_to_md.py"

# 通知队列目录
NOTIFY_QUEUE = Path(os.environ.get(
    "MM_NOTIFY_QUEUE",
    r"C:\ProgramData\MemoryManager\notify_queue"
))

# ────────────────────── Provider 预设 ──────────────────────

LLM_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-flash",
    },
    "openai": {
        "name": "OpenAI",
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model": "gpt-4o-mini",
    },
    "moonshot": {
        "name": "Moonshot (Kimi)",
        "api_url": "https://api.moonshot.cn/v1/chat/completions",
        "model": "moonshot-v1-8k",
    },
    "qwen": {
        "name": "通义千问",
        "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model": "qwen-plus",
    },
    "zhipu": {
        "name": "智谱 GLM",
        "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-4-flash",
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "api_url": "https://api.siliconflow.cn/v1/chat/completions",
        "model": "deepseek-ai/DeepSeek-V3",
    },
}

# ────────────────────── LLM 配置 ──────────────────────

LLM_PROVIDER = os.environ.get("MM_LLM_PROVIDER", "deepseek")
_preset = LLM_PROVIDERS.get(LLM_PROVIDER, {})

LLM_API_URL = os.environ.get("MM_LLM_API_URL", _preset.get("api_url", ""))
LLM_MODEL = os.environ.get("MM_LLM_MODEL", _preset.get("model", ""))

# API Key：环境变量 → 注册表回退
# 兼容旧变量名 MEMORY_MANAGER_DEEPSEEK_API_KEY
LLM_API_KEY = (
    os.environ.get("MM_LLM_API_KEY", "")
    or os.environ.get("MEMORY_MANAGER_DEEPSEEK_API_KEY", "")
)
if not LLM_API_KEY:
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            for var in ("MM_LLM_API_KEY", "MEMORY_MANAGER_DEEPSEEK_API_KEY"):
                try:
                    LLM_API_KEY, _ = winreg.QueryValueEx(key, var)
                    if LLM_API_KEY:
                        break
                except FileNotFoundError:
                    pass
    except Exception:
        pass
if not LLM_API_KEY:
    raise RuntimeError(
        "API Key 未设置。\n"
        "请设置环境变量 MM_LLM_API_KEY（或 MEMORY_MANAGER_DEEPSEEK_API_KEY）。"
    )

LLM_TIMEOUT = int(os.environ.get("MM_LLM_TIMEOUT", "90"))
LLM_MAX_RETRIES = int(os.environ.get("MM_LLM_MAX_RETRIES", "2"))

# 向后兼容别名
DEEPSEEK_API_URL = LLM_API_URL
DEEPSEEK_MODEL = LLM_MODEL
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_TIMEOUT = LLM_TIMEOUT
DEEPSEEK_MAX_RETRIES = LLM_MAX_RETRIES

# ────────────────────── 输入限制 ──────────────────────

MAX_INPUT_CHARS = 20000  # 严格上限
SPLIT_THRESHOLD = 18000  # 超过此值自动二次分割

# ────────────────────── 文件大小限制 ──────────────────────

# 每个文件的字符上限（超过则触发压缩）
FILE_SIZE_LIMITS = {
    "USER.md": 5000,
    "MEMORY.md": 8000,
    "AGENTS.md": 3000,
}

# ────────────────────── 进化提示词模板 ──────────────────────

_EVOLUTION_FILE_GUIDE = (
    "三个文件的定位和进化方向：\n\n"
    "【USER.md — 用户画像】\n"
    "- 内容：用户身份、称呼、背景、兴趣、沟通偏好、习惯、性格特点\n"
    "- 进化方向：主动更新用户的新偏好、新习惯、沟通风格的变化\n"
    "- 风格：条目式，保留用户原话的关键表述\n\n"
    "【MEMORY.md — 知识库】\n"
    "- 内容：项目事实、技术栈、工具配置、决策记录、待办事项、已完成的里程碑\n"
    "- 进化方向：积累性增长，合并去重，淘汰过时信息，保留有参考价值的历史决策\n"
    "- 风格：按项目/主题分类，条目式\n\n"
    "【AGENTS.md — agent 行为配置】\n"
    "- 内容：agent 的工作流程、安全规则、群聊规范、静默规则、工具使用指引\n"
    "- 进化方向：大部分内容是固定的行为指令，不能随意改写\n"
    "- ⚠️ 严格保护：原有的规则、指令、格式结构必须保留，不得删除或改写\n"
    "- ✅ 允许的操作：仅当会话中出现了新的、明确的用户指令（如「以后XXX情况不要YYY」）\n"
    "  才可以在对应章节追加新规则；如果没有此类指令，原样返回，不做任何修改\n"
)

EVOLUTION_SIZE_LIMITS = (
    f"严格控制输出长度：USER.md 不超过 {FILE_SIZE_LIMITS['USER.md']} 字符，"
    f"MEMORY.md 不超过 {FILE_SIZE_LIMITS['MEMORY.md']} 字符，"
    f"AGENTS.md 不超过 {FILE_SIZE_LIMITS['AGENTS.md']} 字符"
)


def get_evolution_system_prompt(task_type: str) -> str:
    """生成进化任务的 system prompt。

    Args:
        task_type: 'daily' / 'weekly' / 'monthly'
    """
    task_desc = {
        "daily": "今日会话摘要",
        "weekly": "本周综合摘要（可覆盖、修正、合并之前每日迭代生成的记忆内容）",
        "monthly": "月度综合摘要（深度审视，可大幅重组和精简；如果月内无新信息，不做改动，原样返回）",
    }

    return (
        f"你是一个记忆管理助手。你的任务是根据{task_desc[task_type]}，审查并进化用户的三个文档文件。\n\n"
        f"{_EVOLUTION_FILE_GUIDE}\n"
        "输入内容（在用户消息中提供）：\n"
        "- 当前 USER.md / MEMORY.md / AGENTS.md 的完整原文\n"
        "- 本次会话摘要（作为进化依据）\n\n"
        "通用规则：\n"
        "1. 保留原有文档内高价值稳定信息\n"
        "2. 只更新新增、变更的事实、偏好、任务进度\n"
        "3. 不做无意义大规模改写\n"
        "4. 合并重复信息，精简冗余\n"
        "5. 跨文件去重：同一信息不要在多个文件中重复出现——用户画像归 USER.md，项目事实归 MEMORY.md，行为规则归 AGENTS.md\n"
        "6. 保持原有文档的格式和风格\n"
        f"7. {EVOLUTION_SIZE_LIMITS}\n\n"
        "输出格式（严格遵守）：\n"
        "使用三重标记分隔三个文档内容：\n"
        f"{MARKER_USER}\n"
        "（USER.md 完整更新后全部文本）\n"
        f"{MARKER_MEMORY}\n"
        "（MEMORY.md 完整更新后全部文本）\n"
        f"{MARKER_AGENTS}\n"
        "（AGENTS.md 完整更新后全部文本）\n\n"
        "注意事项：\n"
        "- 标记之间为对应文件完整更新后全部文本\n"
        "- 不输出多余解释、开场白、总结\n"
        "- 禁止输出 markdown 代码块包裹\n"
        "- 仅输出标记+文件正文"
    )


def check_and_compact_files(
    files_to_write: list[tuple[Path, str, str]],
    system_prompt: str,
    logger: logging.Logger,
) -> list[tuple[Path, str, str]]:
    """检查写入后的文件大小，超限则调用模型压缩。

    Args:
        files_to_write: [(路径, 新内容, 文件名), ...]
        system_prompt: 进化用的 system prompt（压缩时复用）
        logger: 日志记录器

    Returns:
        压缩后的文件列表（未超限的保持不变）
    """
    result = []
    for path, content, name in files_to_write:
        limit = FILE_SIZE_LIMITS.get(name)
        if limit and len(content) > limit:
            logger.warning(f"{name} 超限: {len(content)} 字符 > {limit} 上限，触发压缩")
            compact_prompt = (
                f"以下 {name} 内容已超过 {limit} 字符上限（当前 {len(content)} 字符）。\n"
                f"请精简到 {limit} 字符以内，保留最重要的信息，去除过时、重复、低价值的内容。\n"
                f"只输出精简后的完整文件内容，不要输出任何解释。\n\n"
                f"{content}"
            )
            ok, compacted, err = call_deepseek(
                compact_prompt, system=system_prompt, logger=logger
            )
            if ok and len(compacted) <= limit * 1.1:  # 允许10%容差
                logger.info(f"{name} 压缩完成: {len(content)} → {len(compacted)} 字符")
                result.append((path, compacted, name))
            elif ok:
                logger.warning(f"{name} 压缩后仍超限: {len(compacted)} 字符，使用原内容")
                result.append((path, content, name))
            else:
                logger.warning(f"{name} 压缩失败: {err}，使用原内容")
                result.append((path, content, name))
        else:
            result.append((path, content, name))
    return result


# 分隔标记
MARKER_USER = "===USER_MD_CONTENT==="
MARKER_MEMORY = "===MEMORY_MD_CONTENT==="
MARKER_AGENTS = "===AGENTS_MD_CONTENT==="

# ────────────────────── 日志 ──────────────────────


def setup_logger(task_type: str, date_str: str = None) -> logging.Logger:
    """创建并返回日志记录器。

    Args:
        task_type: 'daily' / 'weekly' / 'monthly'
        date_str: 日期字符串，省略则用今天
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y%m%d")
    log_file = LOG_DIR / f"task-{task_type}-{date_str}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"memory-{task_type}-{date_str}")
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        fmt = logging.Formatter(
            "[%(asctime)s] %(levelname)-7s %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh = logging.FileHandler(str(log_file), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        if hasattr(ch.stream, "reconfigure"):
            try:
                ch.stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
        logger.addHandler(ch)

    return logger


# ────────────────────── 文件操作 ──────────────────────


def ensure_dir(path: Path):
    """确保目录存在，不存在则递归创建。"""
    path.mkdir(parents=True, exist_ok=True)


def atomic_write(path: Path, content: str, encoding: str = "utf-8"):
    """原子写入：先写 .tmp 再 rename，防止中断导致文件损坏。"""
    ensure_dir(path.parent)
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp", dir=str(path.parent), prefix=path.stem + "_"
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding) as f:
            f.write(content)
        os.replace(tmp_path, str(path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_file(path: Path, keep_days: int = 30) -> Path:
    """备份文件为 .bak-YYYYMMDD 格式，返回备份路径。

    自动清理超过 keep_days 天的旧备份。
    """
    if not path.exists():
        return None
    date_str = datetime.now().strftime("%Y%m%d")
    bak_path = path.with_name(path.name + f".bak-{date_str}")
    if bak_path.exists():
        i = 2
        while True:
            bak_path = path.with_name(path.name + f".bak-{date_str}-{i}")
            if not bak_path.exists():
                break
            i += 1
    import shutil
    shutil.copy2(str(path), str(bak_path))

    # 清理旧备份
    cleanup_old_backups(path, keep_days)

    return bak_path


def cleanup_old_backups(original_path: Path, keep_days: int = 30):
    """删除超过 keep_days 天的 .bak-* 备份文件。"""
    import re
    from datetime import timedelta

    cutoff = datetime.now() - timedelta(days=keep_days)
    pattern = re.compile(r"\.bak-(\d{8})(?:-\d+)?$")
    prefix = original_path.name + ".bak-"

    for f in original_path.parent.iterdir():
        if not f.name.startswith(prefix):
            continue
        m = pattern.search(f.name)
        if not m:
            continue
        try:
            bak_date = datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            continue
        if bak_date < cutoff:
            try:
                f.unlink()
            except OSError:
                pass


def log_diff(path: Path, old_content: str, new_content: str, task_type: str, logger: logging.Logger = None):
    """记录文件变更 diff 到 run_log/diff-YYYYMMDD.log。

    只记录有实际变更的文件。输出格式为 unified diff 风格，便于阅读。
    """
    import difflib

    if old_content == new_content:
        return

    date_str = datetime.now().strftime("%Y%m%d")
    diff_file = LOG_DIR / f"diff-{date_str}.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"{path.name} (旧)",
        tofile=f"{path.name} (新)",
        n=2,
    )
    diff_text = "".join(diff)

    if not diff_text.strip():
        return

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"\n{'='*60}\n[{ts}] {task_type} 进化 - {path.name}\n{'='*60}\n"

    try:
        with open(diff_file, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(diff_text)
            f.write("\n")
    except Exception:
        pass

    if logger:
        added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
        removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
        logger.info(f"  {path.name} diff: +{added} -{removed} 行")


def read_file_safe(path: Path, encoding: str = "utf-8") -> str | None:
    """安全读取文件，不存在返回 None。"""
    if not path.exists():
        return None
    return path.read_text(encoding=encoding)


def get_file_char_count(path: Path, encoding: str = "utf-8") -> int:
    """获取文件字符数。"""
    return len(path.read_text(encoding=encoding))


# ────────────────────── 文本分割 ──────────────────────


def split_text_by_chars(text: str, threshold: int = SPLIT_THRESHOLD) -> list[str]:
    """按字符数分割文本。优先在段落边界分割。"""
    if len(text) <= threshold:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > threshold:
            if current:
                chunks.append(current)
            current = line
        else:
            current = current + "\n" + line if current else line
    if current:
        chunks.append(current)

    final = []
    for chunk in chunks:
        if len(chunk) <= MAX_INPUT_CHARS:
            final.append(chunk)
        else:
            for i in range(0, len(chunk), MAX_INPUT_CHARS):
                final.append(chunk[i : i + MAX_INPUT_CHARS])
    return final


# ────────────────────── LLM API 调用 ──────────────────────


def call_llm(
    prompt: str,
    system: str = None,
    max_output_tokens: int = None,
    top_p: float = None,
    logger: logging.Logger = None,
) -> tuple[bool, str, str]:
    """调用 LLM API（兼容所有 OpenAI 格式的 provider）。

    Args:
        prompt: 用户提示词
        system: 系统提示词（可选）
        max_output_tokens: 最大输出 token 数（可选）
        top_p: nucleus sampling 参数（可选）
        logger: 日志记录器

    Returns:
        (成功, 模型输出文本, 错误信息)
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.1,
        "stream": False,
    }
    if top_p is not None:
        payload["top_p"] = top_p
    if max_output_tokens is not None:
        payload["max_tokens"] = max_output_tokens

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LLM_API_KEY}",
    }
    req = urllib.request.Request(
        LLM_API_URL, data=data, headers=headers, method="POST"
    )

    last_error = ""
    for attempt in range(LLM_MAX_RETRIES + 1):
        try:
            if logger:
                logger.info(
                    f"调用 LLM ({LLM_PROVIDER}/{LLM_MODEL})"
                    f"{' 重试 #' + str(attempt) if attempt > 0 else ''}"
                )
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                result = json.loads(resp.read().decode("utf-8"))
            content = result["choices"][0]["message"]["content"].strip()
            if not content:
                return False, "", "LLM 返回空内容"
            if logger:
                logger.info(f"LLM 返回 {len(content)} 字符")
            return True, content, ""
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_error = f"HTTP {e.code}: {body}"
            if logger:
                logger.warning(f"LLM 请求失败 (尝试 {attempt+1}): {last_error}")
        except urllib.error.URLError as e:
            last_error = f"网络错误: {e}"
            if logger:
                logger.warning(f"LLM 网络异常 (尝试 {attempt+1}): {last_error}")
        except Exception as e:
            last_error = f"异常: {e}"
            if logger:
                logger.warning(f"LLM 异常 (尝试 {attempt+1}): {last_error}")

        if attempt < LLM_MAX_RETRIES:
            time.sleep(3 * (attempt + 1))

    return False, "", f"LLM 调用失败（重试 {LLM_MAX_RETRIES} 次）: {last_error}"


# 向后兼容别名
call_deepseek = call_llm


def parse_deepseek_evolution(content: str) -> dict[str, str] | None:
    """解析 DeepSeek 进化输出，提取三个文件内容。

    Returns:
        {'user': ..., 'memory': ..., 'agents': ...} 或 None（标记缺失）
    """
    markers = {
        "user": MARKER_USER,
        "memory": MARKER_MEMORY,
        "agents": MARKER_AGENTS,
    }
    result = {}
    for key, marker in markers.items():
        start = content.find(marker)
        if start == -1:
            return None
        remaining = content[start + len(marker) :]
        next_pos = len(remaining)
        for _, other_marker in markers.items():
            if other_marker == marker:
                continue
            pos = remaining.find(other_marker)
            if pos != -1 and pos < next_pos:
                next_pos = pos
        value = remaining[:next_pos].strip()
        result[key] = value

    if not all(result.values()):
        return None
    return result


# ────────────────────── 通知 ──────────────────────

_STATUS_FILE = LOG_DIR / "latest_status.txt"


def send_notification(title: str, message: str, is_error: bool = False):
    """发送通知：写状态文件 + 写队列文件（由 NotificationAgent 消费）。"""
    if len(message) > 200:
        message = message[:197] + "..."

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        status = "❌ 失败" if is_error else "✅ 成功"
        atomic_write(_STATUS_FILE, f"[{ts}] {status} {title}\n{message}\n")
    except Exception:
        pass

    try:
        NOTIFY_QUEUE.mkdir(parents=True, exist_ok=True)
        queue_file = NOTIFY_QUEUE / f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}.json"
        atomic_write(queue_file, json.dumps({
            "title": title,
            "message": message,
            "is_error": is_error,
            "ts": ts,
        }, ensure_ascii=False))
    except Exception:
        pass


# ────────────────────── 幂等锁 ──────────────────────


class TaskLock:
    """防止同一周期任务并发执行的文件锁。"""

    def __init__(self, task_type: str):
        self.lock_file = LOG_DIR / f".{task_type}.lock"
        self._acquired = False

    def acquire(self) -> bool:
        """尝试获取锁，返回是否成功。"""
        ensure_dir(self.lock_file.parent)
        if self.lock_file.exists():
            try:
                pid = int(self.lock_file.read_text().strip())
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    capture_output=True,
                    text=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if str(pid) in result.stdout:
                    return False
            except (ValueError, OSError):
                pass
        self.lock_file.write_text(str(os.getpid()))
        self._acquired = True
        return True

    def release(self):
        if self._acquired:
            try:
                self.lock_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._acquired = False

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("任务正在运行中，跳过本次执行")
        return self

    def __exit__(self, *args):
        self.release()


# ────────────────────── 文件路径工具 ──────────────────────


def get_today_str() -> str:
    """返回今天日期 YYYY-MM-DD。"""
    return datetime.now().strftime("%Y-%m-%d")


def get_today_compact() -> str:
    """返回今天日期 YYYYMMDD。"""
    return datetime.now().strftime("%Y%m%d")


def get_conversation_jsonl(date_str: str = None) -> Path | None:
    """获取指定日期的会话 jsonl 文件路径，不存在返回 None。"""
    if date_str is None:
        date_str = get_today_str()
    path = CONVERSATIONS_DIR / f"{date_str}.jsonl"
    return path if path.exists() else None


# Agent workspace 映射：agent_name → workspace 目录
# main agent 使用默认 WORKSPACE_DIR，其他 agent 使用 workspace-<name>
AGENT_WORKSPACE_MAP: dict[str, Path] = {}


def get_agent_workspace(agent_name: str) -> Path:
    """获取指定 agent 的 workspace 目录。"""
    if agent_name in AGENT_WORKSPACE_MAP:
        return AGENT_WORKSPACE_MAP[agent_name]
    if agent_name == "main":
        return WORKSPACE_DIR
    return OPENCLAW_ROOT / f"workspace-{agent_name}"


def get_chunking_dir(date_str: str = None, agent_name: str = None) -> Path:
    """获取指定日期的 chunking 输出目录。支持 agent 分区。"""
    if date_str is None:
        date_str = get_today_str()
    if agent_name:
        return CHUNKING_DIR / agent_name / date_str
    return CHUNKING_DIR / date_str


def get_abstracted_dir(task_type: str, agent_name: str = None) -> Path:
    """获取摘要输出目录。支持 agent 分区。"""
    base = {
        "daily": ABSTRACTED_DAILY_DIR,
        "weekly": ABSTRACTED_WEEKLY_DIR,
        "monthly": ABSTRACTED_MONTHLY_DIR,
    }[task_type]
    if agent_name:
        return base / agent_name
    return base


def list_agents(date_str: str = None) -> list[str]:
    """从 chunking 目录发现所有 agent。main 排在第一位。"""
    if date_str is None:
        date_str = get_today_str()
    agents = []
    # 新结构：chunking/<agent>/<date>/
    for d in CHUNKING_DIR.iterdir():
        if d.is_dir() and (d / date_str).exists():
            agents.append(d.name)
    # 兼容旧结构（无 agent 分区）：chunking/<date>/
    if not agents and (CHUNKING_DIR / date_str).exists():
        agents.append("main")
    # main 排第一
    agents.sort(key=lambda a: (a != "main", a))
    return agents


def list_session_files(date_str: str = None, agent_name: str = None) -> list[Path]:
    """列出指定日期 chunking 目录下的会话 .md 文件，按文件名排序。"""
    chunk_dir = get_chunking_dir(date_str, agent_name)
    if not chunk_dir.exists():
        return []
    files = sorted(chunk_dir.glob("*-会话*.md"))
    return files


def list_daily_abstracts(days: int = 7, end_date: str = None, agent_name: str = None) -> list[Path]:
    """列出最近 N 天的 daily abstracted 文件。"""
    if end_date is None:
        end = datetime.now()
    else:
        end = datetime.strptime(end_date, "%Y-%m-%d")

    from datetime import timedelta
    abstract_dir = get_abstracted_dir("daily", agent_name)
    result = []
    for i in range(days):
        d = end - timedelta(days=i)
        path = abstract_dir / f"{d.strftime('%Y-%m-%d')}-abstracted.md"
        if path.exists():
            result.append(path)
    return sorted(result)


def list_monthly_weekly_abstracts(year: int, month: int, agent_name: str = None) -> list[Path]:
    """列出指定月份的所有 weekly abstracted 文件。"""
    abstract_dir = get_abstracted_dir("weekly", agent_name)
    result = []
    for p in abstract_dir.glob("*-abstracted.md"):
        name = p.stem.replace("-abstracted", "")
        parts = name.split("至")
        if len(parts) != 2:
            continue
        try:
            start = datetime.strptime(parts[0], "%Y-%m-%d")
            if start.year == year and start.month == month:
                result.append(p)
        except ValueError:
            continue
    return sorted(result)
