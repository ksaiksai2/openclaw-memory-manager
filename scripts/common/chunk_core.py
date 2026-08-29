#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chunk_core.py — 会话 chunking 共享核心
======================================
供 rechunk_all.py（全量重建）与 rechunk_daily.py（增量）复用：
  - 会话文件识别（普通 .jsonl + .jsonl.reset.*）
  - 消息解析（OpenClaw type=message 行格式）
  - 消息压缩（保留全部 user + assistant 每回合最后一条）
  - Markdown 渲染（与现有 memory/*.md 结构一致）

设计要点：
  - 全量读取 / 按日筛选读取 两条路径分开，daily 增量避免全量扫描
  - reset 文件同样纳入（修复历史漏扫）
"""

import json
import os
import re
from collections import OrderedDict
from datetime import datetime

# ── 会话文件识别: uuid.jsonl 或 uuid.jsonl.reset.<ts> ──
SESSION_FILE_RE = re.compile(
    r"^([0-9a-fA-F-]{36})\.jsonl(?:\.reset\.[0-9A-Za-z.:-]+)?$"
)


# ─────────────────────────── agent 发现 ───────────────────────────

def get_direct_agents(agents_dir, openclaw_root):
    """从 openclaw.json 读直属 agent；失败回退到 agents 目录下有 sessions 的子目录。"""
    config_path = os.path.join(openclaw_root, "openclaw.json")
    agents = []
    if os.path.isfile(config_path):
        try:
            with open(config_path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            content = re.sub(r",\s*([}\]])", r"\1", content)
            config = json.loads(content)
            lst = config.get("agents", {}).get("list", [])
            if isinstance(lst, list):
                for a in lst:
                    if isinstance(a, dict) and a.get("id"):
                        agents.append(a["id"])
                    elif isinstance(a, str) and a:
                        agents.append(a)
        except Exception:
            agents = []
    if not agents and os.path.isdir(agents_dir):
        for d in sorted(os.listdir(agents_dir)):
            if os.path.isdir(os.path.join(agents_dir, d, "sessions")):
                agents.append(d)
    return agents


# ─────────────────────────── 消息解析 ───────────────────────────

def parse_session_jsonl(jsonl_path, session_id):
    """解析 OpenClaw session 转录文件（type=message 行格式）。

    返回 (messages, errors)；messages 每项 {sessionId, role, content, ts}。
    """
    messages = []
    errors = 0
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") != "message":
                    continue
                msg_obj = obj.get("message")
                if not isinstance(msg_obj, dict):
                    continue
                role = msg_obj.get("role")
                if role not in ("user", "assistant"):
                    continue
                content = msg_obj.get("content")
                if content is None:
                    continue
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            parts.append(item.get("text", ""))
                    text = "\n".join(parts)
                else:
                    text = json.dumps(content, ensure_ascii=False, indent=2)
                if not text.strip():
                    continue
                if "[assistant turn failed before producing content]" in text:
                    continue

                timestamp = obj.get("timestamp")
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                        ts = int(dt.timestamp() * 1000)
                    except (ValueError, TypeError):
                        ts = 0
                else:
                    try:
                        ts = int(msg_obj.get("timestamp", 0))
                    except (TypeError, ValueError):
                        ts = 0
                messages.append({
                    "sessionId": session_id,
                    "role": role,
                    "content": text,
                    "ts": ts,
                })
    except OSError:
        errors += 1
    return messages, errors


def _session_files(sessions_dir):
    """返回 [(entry, session_id, full_path), ...]，含普通 + reset 文件。"""
    out = []
    try:
        entries = os.listdir(sessions_dir)
    except OSError:
        return out
    for entry in entries:
        m = SESSION_FILE_RE.match(entry)
        if not m:
            continue
        out.append((m.group(1), os.path.join(sessions_dir, entry)))
    return out


# ─────────────────────────── 读取路径 ───────────────────────────

def load_sessions_messages(sessions_dir):
    """全量读取：sessions.json 索引 + 目录所有会话文件（含 reset）。"""
    messages = []
    errors = 0
    seen_paths = set()
    abs_dir = os.path.normcase(os.path.abspath(sessions_dir))

    # 1) sessions.json 主索引
    idx_path = os.path.join(sessions_dir, "sessions.json")
    if os.path.isfile(idx_path):
        try:
            with open(idx_path, "r", encoding="utf-8") as f:
                idx = json.load(f)
        except (json.JSONDecodeError, OSError):
            idx = {}
        if isinstance(idx, dict):
            for session_key, meta in idx.items():
                if not isinstance(meta, dict):
                    continue
                sf = meta.get("sessionFile", "")
                if not sf or not os.path.isfile(sf):
                    continue
                if os.path.normcase(os.path.abspath(os.path.dirname(sf))) != abs_dir:
                    continue  # 跨目录引用保护
                sid = meta.get("sessionId", "") or os.path.basename(sf).split(".")[0]
                seen_paths.add(os.path.normcase(os.path.abspath(sf)))
                msgs, errs = parse_session_jsonl(sf, sid)
                messages.extend(msgs)
                errors += errs

    # 2) 目录扫描（普通 + reset）
    for sid, full_path in _session_files(sessions_dir):
        full = os.path.normcase(os.path.abspath(full_path))
        if full in seen_paths:
            continue
        seen_paths.add(full)
        msgs, errs = parse_session_jsonl(full, sid)
        messages.extend(msgs)
        errors += errs

    messages.sort(key=lambda x: x["ts"])
    return messages, errors


def load_daily_sessions_messages(sessions_dir, date_str):
    """增量读取：只处理 mtime >= 目标日零点 的会话文件。

    粗筛（文件 mtime）+ 精筛（最后一条消息日期 == date_str）：
      - 当天活跃的会话 → mtime 今天 → 读内容 → 最后消息可能今天 → 纳入
      - 当天被 reset 的历史会话 → mtime=reset 时间=今天 → 读内容
        → 若最后消息日期 == 目标日 → 纳入（跨天会话归结束日）
        → 若最后消息是更早日期 → 昨天已处理过，跳过（避免重复）
    返回 (messages, errors, scanned_files)。
    """
    day_start = datetime.now().replace(
        year=int(date_str[:4]), month=int(date_str[5:7]), day=int(date_str[8:10]),
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp()

    messages = []
    errors = 0
    scanned = 0
    for sid, full_path in _session_files(sessions_dir):
        try:
            if os.path.getmtime(full_path) < day_start:
                continue  # 粗筛：非目标日文件，秒级跳过
        except OSError:
            continue
        scanned += 1
        msgs, errs = parse_session_jsonl(full_path, sid)
        if not msgs:
            continue
        # 精筛：最后一条消息日期 == 目标日
        last = max(m["ts"] for m in msgs)
        if not last:
            continue
        last_date = datetime.fromtimestamp(last / 1000.0).strftime("%Y-%m-%d")
        if last_date != date_str:
            continue
        messages.extend(msgs)
        errors += errs

    messages.sort(key=lambda x: x["ts"])
    return messages, errors, scanned


# ─────────────────────────── 压缩与渲染 ───────────────────────────

def collapse_to_reports(messages):
    """保留: 全部 user 消息 + 每轮交互(user 发话后到下次 user 发话前)中助手最后一条。"""
    result = []
    pending = []
    for msg in messages:
        if msg["role"] == "user":
            if pending:
                result.append(pending[-1])
                pending = []
            result.append(msg)
        else:
            pending.append(msg)
    if pending:
        result.append(pending[-1])
    return result


def fmt_time(ts):
    if not ts:
        return "??-?? ??:??:??"
    return datetime.fromtimestamp(ts / 1000.0).strftime("%m-%d %H:%M:%S")


def fmt_time_full(ts):
    if not ts:
        return "????-??-?? ??:??:??"
    return datetime.fromtimestamp(ts / 1000.0).strftime("%Y-%m-%d %H:%M:%S")


def date_of(messages):
    for msg in reversed(messages):
        if msg["ts"]:
            return datetime.fromtimestamp(msg["ts"] / 1000.0).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def role_label(role):
    return {"user": "👤 用户", "assistant": "🤖 助手"}.get(role, "📄 " + (role or "未知"))


def build_session_md(seq, total, messages, date_str, original_total, sid):
    """生成单个会话的 Markdown（与现有 memory/*.md 结构一致）。"""
    lines = []
    lines.append(f"# 会话记录 {date_str} · 会话 {seq:02d}/{total:02d}")
    lines.append("")
    users = sum(1 for m in messages if m["role"] == "user")
    assistants = sum(1 for m in messages if m["role"] == "assistant")
    stat = f"**{len(messages)}**（用户 {users} / 助手 {assistants}）"
    if original_total is not None and original_total != len(messages):
        stat = (f"原始 **{original_total}** 条 → 保留 {stat}"
                f"（省略中间过程消息 {original_total - len(messages)} 条）")
    lines.append(f"> 源文件: `agents`")
    lines.append(f"> 消息总数: {stat} · 时间范围: "
                 f"{fmt_time_full(messages[0]['ts'])} ~ {fmt_time_full(messages[-1]['ts'])}")
    lines.append(f"> sessionId: `{sid}`")
    lines.append("")
    lines.append("---")
    lines.append("")
    for msg in messages:
        lines.append(f"### {role_label(msg['role'])} · {fmt_time(msg['ts'])}")
        lines.append("")
        for cl in msg["content"].splitlines() or [""]:
            lines.append(f"> {cl}" if cl else ">")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def group_by_session(messages):
    """按 sessionId 首次出现顺序分组，返回 [(sid, [msgs...]), ...]。"""
    by_sid = OrderedDict()
    for msg in messages:
        by_sid.setdefault(msg["sessionId"], []).append(msg)
    return list(by_sid.items())


# ─────────────────────────── 双份输出 ───────────────────────────
# 设计：嵌套分类目录是书写源头（人工翻阅），扁平目录是检索副本（语义搜索）。
# 同一份 MD 内容同时写入两个位置，保证内容一致。

NESTED_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-会话\d+\.md$")
FLAT_FILE_RE = re.compile(r"^([a-z0-9_-]+)-(\d{4}-\d{2}-\d{2})-会话\d+\.md$")


def cleanup_date_outputs(agent_id, date_str, nested_root, flat_dir):
    """清理某 agent 某日期的旧输出（嵌套 + 扁平）。

    嵌套: {nested_root}/{agent}/{date}/会话文件
    扁平: {flat_dir}/{agent}-{date}-会话N.md
    只删本设计生成的命名模式，不碰其他文件。
    """
    # 嵌套
    nested_dir = os.path.join(nested_root, agent_id, date_str)
    if os.path.isdir(nested_dir):
        for fname in os.listdir(nested_dir):
            if NESTED_FILE_RE.match(fname) and fname.startswith(date_str + "-"):
                try:
                    os.remove(os.path.join(nested_dir, fname))
                except OSError:
                    pass
    # 扁平
    if os.path.isdir(flat_dir):
        for fname in os.listdir(flat_dir):
            m = FLAT_FILE_RE.match(fname)
            if m and m.group(1) == agent_id and m.group(2) == date_str:
                try:
                    os.remove(os.path.join(flat_dir, fname))
                except OSError:
                    pass


def cleanup_agent_outputs(agent_id, nested_root, flat_dir):
    """清理某 agent 全部旧输出（全量重建用）。"""
    # 嵌套
    agent_dir = os.path.join(nested_root, agent_id)
    if os.path.isdir(agent_dir):
        for date_dir in os.listdir(agent_dir):
            full = os.path.join(agent_dir, date_dir)
            if not os.path.isdir(full):
                continue
            for fname in os.listdir(full):
                if NESTED_FILE_RE.match(fname):
                    try:
                        os.remove(os.path.join(full, fname))
                    except OSError:
                        pass
    # 扁平
    if os.path.isdir(flat_dir):
        for fname in os.listdir(flat_dir):
            m = FLAT_FILE_RE.match(fname)
            if m and m.group(1) == agent_id:
                try:
                    os.remove(os.path.join(flat_dir, fname))
                except OSError:
                    pass


def write_session_outputs(agent_id, date_str, seq, total, messages,
                          original_total, sid, nested_root, flat_dir):
    """生成会话 MD 并双份写入（嵌套分类源 + 扁平检索副本）。

    返回 (nested_path, flat_path)。
    """
    md = build_session_md(seq, total, messages, date_str, original_total, sid)

    # 嵌套：{nested_root}/{agent}/{date}/{date}-会话N.md
    nested_dir = os.path.join(nested_root, agent_id, date_str)
    os.makedirs(nested_dir, exist_ok=True)
    nested_name = f"{date_str}-会话{seq:02d}.md"
    nested_path = os.path.join(nested_dir, nested_name)
    with open(nested_path, "w", encoding="utf-8") as f:
        f.write(md)

    # 扁平：{flat_dir}/{agent}-{date}-会话N.md
    os.makedirs(flat_dir, exist_ok=True)
    flat_name = f"{agent_id}-{date_str}-会话{seq:02d}.md"
    flat_path = os.path.join(flat_dir, flat_name)
    with open(flat_path, "w", encoding="utf-8") as f:
        f.write(md)

    return nested_path, flat_path