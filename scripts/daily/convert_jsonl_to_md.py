#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jsonl 会话记录 -> Markdown 转换脚本（简化方案）
================================================

将 DSH / OpenClaw 生成的微信会话 jsonl 记录转换为排版良好的 Markdown 文件。

输入格式（每行一条 JSON 消息）:
    {
      "sessionKey":  "agent:main:...",
      "sessionId":   "38bc2707-...",
      "recordedAt":  "2026-08-01T16:00:55.973Z",
      "id":          "msg_...",
      "role":        "user" | "assistant",
      "content":     "消息文本",
      "timestamp":   1785599984168      # epoch 毫秒
    }

用法:
    python convert_jsonl_to_md.py [输入.jsonl] [--out 路径] [--last N] [--full] [--merge]

    - 不带参数: 自动挑选桌面上修改时间最新的 *.jsonl
    - 默认: 每个会话单独输出一个文件, 文件名 YYYY-MM-DD-会话XX.md
      (XX = 会话编号 01, 02, ... 按时间顺序)
    - --merge: 合并成一个 YYYY-MM-DD.md (旧行为)
    - --out: 拆分模式下为输出目录(省略=输入同目录); 合并模式下为输出文件路径
    - --flat: 仅合并模式有效, 不按 sessionId 分组, 连续输出
    - --full: 保留全部消息 (默认只保留用户消息 + 每轮交互中助手的最后一条汇报)
    - --last N: 每个会话只保留最后 N 轮交互 (用户消息 + 助手汇报), 0=不限制; 默认 0
    - --encoding: 指定输入文件编码, 默认 utf-8

依赖: 仅 Python 标准库 (Python 3.7+), 无需安装任何第三方包。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

# Windows 控制台可能不是 UTF-8, 统一按 UTF-8 输出, 避免打印中文/表情报错
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

DATE_IN_NAME = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_args():
    p = argparse.ArgumentParser(
        description="将 jsonl 会话记录转换为排版良好的 Markdown 文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("input", nargs="?", default=None,
                   help="输入 .jsonl 文件路径; 省略则自动选桌面最新的 *.jsonl")
    p.add_argument("--sessions-dir", default=None,
                   help="单个 openclaw sessions 目录路径 (包含 sessions.json 和 UUID.jsonl 文件)")
    p.add_argument("--agents-dir", default=None,
                   help="openclaw agents 根目录路径 (包含多个 agent 子目录，从 openclaw.json 读取直属 agent)")
    p.add_argument("--agent-ids", default=None,
                   help="要处理的 agent id 列表，逗号分隔 (省略则从 openclaw.json 读取)")
    p.add_argument("--out", default=None,
                   help="拆分模式下为输出目录(省略=输入同目录); 合并模式下为输出文件路径")
    p.add_argument("--merge", action="store_true",
                   help="合并所有会话为一个 YYYY-MM-DD.md (默认每个会话单独输出一个文件)")
    p.add_argument("--flat", action="store_true",
                   help="仅合并模式有效: 不按 sessionId 分组, 连续输出")
    p.add_argument("--full", action="store_true",
                   help="保留全部消息 (默认只保留用户消息 + 每轮交互中助手的最后一条汇报)")
    p.add_argument("--last", type=int, default=0,
                   help="每个会话只保留最后 N 轮交互 (用户消息 + 助手汇报), 0=不限制; 默认 0")
    p.add_argument("--encoding", default="utf-8",
                   help="输入文件编码, 默认 utf-8")
    return p.parse_args()


def pick_input():
    """默认输入: 桌面(或脚本所在目录)上修改时间最新的 *.jsonl。"""
    candidates = []
    home = os.path.expanduser("~")
    for base in (os.path.join(home, "Desktop"), os.path.dirname(os.path.abspath(__file__))):
        if os.path.isdir(base):
            try:
                candidates.extend(
                    os.path.join(base, f)
                    for f in os.listdir(base)
                    if f.lower().endswith(".jsonl")
                )
            except OSError:
                pass
    if not candidates:
        sys.exit("未找到任何 .jsonl 文件, 请手动指定输入路径。")
    return max(candidates, key=os.path.getmtime)


def load_messages(path, encoding="utf-8"):
    """读取 jsonl, 返回 (消息列表, 解析错误数)。

    消息按 timestamp 升序排列; 每条为 dict, 仅保留所需字段。
    新增 agent_name 字段，从 sessionKey 中提取。
    """
    messages = []
    errors = 0
    with open(path, "r", encoding=encoding) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                errors += 1
                continue
            if not isinstance(obj, dict):
                errors += 1
                continue
            content = obj.get("content")
            if content is None:
                continue
            if isinstance(content, str):
                text = content
            else:
                text = json.dumps(content, ensure_ascii=False, indent=2)
            if not text.strip():
                continue
            try:
                ts = int(obj.get("timestamp", 0))
            except (TypeError, ValueError):
                ts = 0
            # 从 sessionKey 提取 agent 名: agent:<name>:<context>:<uuid>
            sk = str(obj.get("sessionKey", ""))
            agent_name = sk.split(":")[1] if sk.startswith("agent:") and sk.count(":") >= 2 else "kavis"
            messages.append({
                "sessionId": str(obj.get("sessionId", "")),
                "role": str(obj.get("role", "")),
                "content": text,
                "ts": ts,
                "agent_name": agent_name,
            })
    messages.sort(key=lambda m: m["ts"])
    return messages, errors


def _parse_jsonl_messages(jsonl_path, session_id, agent_name, encoding="utf-8"):
    """从单个 .jsonl 文件提取消息，返回 (消息列表, 错误数)。"""
    messages = []
    errors = 0
    try:
        with open(jsonl_path, "r", encoding=encoding) as f:
            for lineno, raw in enumerate(f, 1):
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    errors += 1
                    continue
                if not isinstance(obj, dict):
                    errors += 1
                    continue

                # 只处理 type="message" 的行
                if obj.get("type") != "message":
                    continue

                # 获取 message 对象
                msg_obj = obj.get("message")
                if not isinstance(msg_obj, dict):
                    continue

                role = msg_obj.get("role")
                if role not in ("user", "assistant"):
                    continue

                content = msg_obj.get("content")
                if content is None:
                    continue

                # 处理内容格式
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text_parts.append(item.get("text", ""))
                    text = "\n".join(text_parts)
                else:
                    text = json.dumps(content, ensure_ascii=False, indent=2)

                if not text.strip():
                    continue

                if "[assistant turn failed before producing content]" in text:
                    continue

                # 获取时间戳（ISO 格式）
                timestamp = obj.get("timestamp")
                if timestamp:
                    try:
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
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
                    "agent_name": agent_name,
                })
    except OSError:
        errors += 1
    return messages, errors


def load_sessions_messages(sessions_dir, encoding="utf-8"):
    """从 openclaw sessions 目录读取消息。

    读取 sessions.json 主索引，遍历每个会话的 .jsonl 文件。
    然后扫描目录中未被索引的 .jsonl 文件，一并读取。
    返回 (消息列表, 错误数)。

    保护：sessionFile 必须位于当前 sessions 目录内，跨目录引用
    （例如 agent 改名后残留的指向旧目录的索引项）一律跳过，避免重复导出。
    """
    messages = []
    errors = 0
    indexed_files = set()
    abs_sessions_dir = os.path.normcase(os.path.abspath(sessions_dir))
    skipped_cross_dir = 0

    # 读取 sessions.json 主索引
    sessions_json_path = os.path.join(sessions_dir, "sessions.json")
    if os.path.isfile(sessions_json_path):
        try:
            with open(sessions_json_path, "r", encoding=encoding) as f:
                sessions_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            sessions_data = {}

        if isinstance(sessions_data, dict):
            for session_key, session_meta in sessions_data.items():
                if not isinstance(session_meta, dict):
                    continue

                jsonl_path = session_meta.get("sessionFile", "")
                if not jsonl_path or not os.path.isfile(jsonl_path):
                    continue

                # 跨目录引用保护：只读取当前 sessions 目录内的文件
                file_in_sessions_dir = os.path.normcase(
                    os.path.abspath(os.path.dirname(jsonl_path))
                ) == abs_sessions_dir
                if not file_in_sessions_dir:
                    skipped_cross_dir += 1
                    continue

                session_id = session_meta.get("sessionId", "")
                agent_name = "kavis"
                if session_key.startswith("agent:"):
                    parts = session_key.split(":")
                    if len(parts) >= 2:
                        agent_name = parts[1]

                indexed_files.add(os.path.normcase(os.path.abspath(jsonl_path)))
                msgs, errs = _parse_jsonl_messages(jsonl_path, session_id, agent_name, encoding)
                messages.extend(msgs)
                errors += errs

    if skipped_cross_dir:
        print(f"      [警告] 跳过 {skipped_cross_dir} 条跨目录 sessionFile 引用"
              f"（残留索引，不属于 {os.path.basename(sessions_dir)}）")

    # 扫描目录中未被 sessions.json 索引的 .jsonl 文件
    for entry in os.listdir(sessions_dir):
        if not entry.endswith(".jsonl"):
            continue
        if ".trajectory." in entry:
            continue
        full_path = os.path.normcase(os.path.abspath(os.path.join(sessions_dir, entry)))
        if full_path in indexed_files:
            continue
        # 用文件名（去掉 .jsonl）作为 sessionId
        session_id = entry.replace(".jsonl", "")
        agent_name = "kavis"  # 默认，由调用方覆盖
        msgs, errs = _parse_jsonl_messages(
            os.path.join(sessions_dir, entry), session_id, agent_name, encoding
        )
        if msgs:
            messages.extend(msgs)
            errors += errs

    messages.sort(key=lambda m: m["ts"])
    return messages, errors


def fmt_time(ts):
    if not ts:
        return "??-?? ??:??:??"
    return datetime.fromtimestamp(ts / 1000.0).strftime("%m-%d %H:%M:%S")


def fmt_time_full(ts):
    if not ts:
        return "????-??-?? ??:??:??"
    return datetime.fromtimestamp(ts / 1000.0).strftime("%Y-%m-%d %H:%M:%S")


def output_date(path, messages):
    """从输入文件名提取 YYYY-MM-DD, 提取不到则用最后一条消息的本地日期。"""
    m = DATE_IN_NAME.search(os.path.basename(path))
    if m:
        return m.group(1)
    for msg in reversed(messages):
        if msg["ts"]:
            return datetime.fromtimestamp(msg["ts"] / 1000.0).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def output_date_from_messages(messages):
    """从消息时间戳推断日期（用于 sessions 目录模式）。"""
    # 使用最后一条消息的日期
    for msg in reversed(messages):
        if msg["ts"]:
            return datetime.fromtimestamp(msg["ts"] / 1000.0).strftime("%Y-%m-%d")
    return datetime.now().strftime("%Y-%m-%d")


def role_label(role):
    return {
        "user": "👤 用户",
        "assistant": "🤖 助手",
        "system": "⚙️ 系统",
    }.get(role, "📄 " + (role or "未知"))


def render_message(msg):
    """渲染一条消息: 标题 + 引用块正文 (引用块保留换行, 且不影响原有 markdown)。"""
    lines = []
    lines.append(f"### {role_label(msg['role'])} · {fmt_time(msg['ts'])}")
    lines.append("")
    for cl in msg["content"].splitlines() or [""]:
        lines.append(f"> {cl}" if cl else ">")
    lines.append("")
    return lines


def collapse_to_reports(messages):
    """只保留: 全部用户消息 + 每轮交互(用户发话后到下次用户发话前)中助手的最后一条消息。

    即: 把连续的同角色助手消息视为一轮的中间过程, 仅保留该轮最后一条"汇报"。
    文件开头若直接是助手消息, 同样只保留该段最后一条。
    """
    result = []
    pending_assistant = []
    for msg in messages:
        if msg["role"] == "user":
            if pending_assistant:
                result.append(pending_assistant[-1])
                pending_assistant = []
            result.append(msg)
        else:
            pending_assistant.append(msg)
    if pending_assistant:
        result.append(pending_assistant[-1])
    return result


def trim_to_last_turns(messages, n):
    """在已压缩的消息序列中, 只保留最后 n 轮交互。

    messages 已是"用户消息 + 助手汇报"交替(可能以助手开头或以用户结尾)。
    取最后 n 条用户消息及其后的内容; 用户消息不足 n 条时全部保留。
    """
    if n <= 0:
        return messages
    user_idx = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if not user_idx:
        return messages[-n:] if len(messages) > n else messages
    start = user_idx[max(0, len(user_idx) - n)]
    return messages[start:]


def group_sessions(messages):
    """按 sessionId 在时间上出现的先后分组, 得到 [(sessionId, 消息列表), ...]。"""
    order = []
    by_id = {}
    for msg in messages:
        sid = msg["sessionId"]
        if sid not in by_id:
            by_id[sid] = []
            order.append(sid)
        by_id[sid].append(msg)
    return [(sid, by_id[sid]) for sid in order]


def build_session_md(session_no, session_count, messages, src_path, date_str,
                     original_total=None, last_n=0, sid=""):
    """生成单个会话的 Markdown 文件内容 (用于默认的拆分输出)。"""
    lines = []
    lines.append(f"# 会话记录 {date_str} · 会话 {session_no:02d}/{session_count:02d}")
    lines.append("")
    total = len(messages)
    users = sum(1 for m in messages if m["role"] == "user")
    assistants = sum(1 for m in messages if m["role"] == "assistant")
    others = total - users - assistants
    lines.append(f"> 源文件: `{os.path.basename(src_path)}`")
    if messages:
        count_stat = f"**{total}**（用户 {users} / 助手 {assistants}"
        if others:
            count_stat += f" / 其他 {others}"
        count_stat += "）"
        if original_total is not None and original_total != total:
            count_stat = (f"原始 **{original_total}** 条 → 保留 {count_stat}"
                          f"（省略中间过程消息 {original_total - total} 条）")
        lines.append(f"> 消息总数: {count_stat} · 时间范围: "
                     f"{fmt_time_full(messages[0]['ts'])} ~ {fmt_time_full(messages[-1]['ts'])}")
        if last_n > 0:
            lines.append(f"> 保留模式: 仅保留最后 {last_n} 轮交互（用户消息 + 助手最终汇报）")
    if sid:
        lines.append(f"> sessionId: `{sid}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in messages:
        lines.extend(render_message(msg))

    return "\n".join(lines).rstrip() + "\n"


def build_md(messages, src_path, date_str, flat=False, original_total=None, last_n=0):
    lines = []
    lines.append(f"# 会话记录 {date_str}")
    lines.append("")
    total = len(messages)
    users = sum(1 for m in messages if m["role"] == "user")
    assistants = sum(1 for m in messages if m["role"] == "assistant")
    others = total - users - assistants
    lines.append(f"> 源文件: `{os.path.basename(src_path)}`")
    if messages:
        count_stat = f"**{total}**（用户 {users} / 助手 {assistants}"
        if others:
            count_stat += f" / 其他 {others}"
        count_stat += "）"
        if original_total is not None and original_total != total:
            count_stat = (f"原始 **{original_total}** 条 → 保留 {count_stat}"
                          f"（省略中间过程消息 {original_total - total} 条）")
        lines.append(f"> 消息总数: {count_stat} · 时间范围: "
                     f"{fmt_time_full(messages[0]['ts'])} ~ {fmt_time_full(messages[-1]['ts'])}")
        if last_n > 0:
            lines.append(f"> 保留模式: 每个会话仅保留最后 {last_n} 轮交互（用户消息 + 助手最终汇报）")
    if not flat:
        lines.append(f"> 会话段: {len(group_sessions(messages))}")
    lines.append("")
    lines.append("---")
    lines.append("")

    if flat:
        for msg in messages:
            lines.extend(render_message(msg))
    else:
        for idx, (sid, msgs) in enumerate(group_sessions(messages), 1):
            first, last = msgs[0]["ts"], msgs[-1]["ts"]
            lines.append(f"## 会话 {idx} · {fmt_time(first)} ~ {fmt_time(last)} · {len(msgs)} 条")
            lines.append("")
            for msg in msgs:
                lines.extend(render_message(msg))
            lines.append("---")
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main():
    args = parse_args()

    # 确定输出目录
    project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    base_out = args.out or os.path.join(project_root, "memory-chunking")

    # 确定数据源模式
    if args.agents_dir:
        # 多 agent 模式：从 agents 根目录 + openclaw.json 读取
        agents_dir = args.agents_dir
        if not os.path.isdir(agents_dir):
            sys.exit(f"找不到 agents 目录: {agents_dir}")

        # 获取 agent 列表
        if args.agent_ids:
            agent_ids = [a.strip() for a in args.agent_ids.split(",") if a.strip()]
        else:
            # 从 openclaw.json 读取
            sys.path.insert(0, os.path.join(project_root, "scripts"))
            from common.utils import get_openclaw_agents
            agents_info = get_openclaw_agents()
            agent_ids = [a["id"] for a in agents_info]

        if not agent_ids:
            sys.exit("未找到任何直属 agent")

        print(f"[1/3] 多 agent 模式，agents 目录: {agents_dir}")
        print(f"      要处理的 agent: {', '.join(agent_ids)}")

        # 遍历每个 agent
        all_messages = []
        for agent_id in agent_ids:
            sessions_dir = os.path.join(agents_dir, agent_id, "sessions")
            if not os.path.isdir(sessions_dir):
                print(f"      跳过 {agent_id}: sessions 目录不存在")
                continue

            print(f"\n      读取 {agent_id} 的 sessions ...")
            messages, errors = load_sessions_messages(sessions_dir, args.encoding)
            print(f"      {agent_id}: {len(messages)} 条消息"
                  f"（用户 {sum(1 for m in messages if m['role'] == 'user')}"
                  f" / 助手 {sum(1 for m in messages if m['role'] == 'assistant')}）"
                  + (f"，跳过 {errors} 条" if errors else ""))

            # 设置 agent_name
            for msg in messages:
                msg["agent_name"] = agent_id
            all_messages.extend(messages)

        if not all_messages:
            sys.exit("未从任何 agent 的 sessions 目录读取到消息")

        messages = all_messages
        date_str = output_date_from_messages(messages)
        src = agents_dir

    elif args.sessions_dir:
        # 单 agent 模式：从单个 sessions 目录读取
        sessions_dir = args.sessions_dir
        if not os.path.isdir(sessions_dir):
            sys.exit(f"找不到 sessions 目录: {sessions_dir}")

        print(f"[1/3] 从 sessions 目录读取: {sessions_dir} ...")
        messages, errors = load_sessions_messages(sessions_dir, args.encoding)
        print(f"      共 {len(messages)} 条消息"
              f"（用户 {sum(1 for m in messages if m['role'] == 'user')}"
              f" / 助手 {sum(1 for m in messages if m['role'] == 'assistant')}）"
              + (f"，跳过解析失败行 {errors} 条" if errors else ""))

        if not messages:
            sys.exit("未从 sessions 目录读取到任何消息")

        date_str = output_date_from_messages(messages)
        src = sessions_dir

    else:
        # 原有逻辑：从单个 jsonl 文件读取
        src = args.input or pick_input()
        if not os.path.isfile(src):
            sys.exit(f"找不到输入文件: {src}")
        if not src.lower().endswith(".jsonl"):
            sys.exit(f"输入文件不是 .jsonl: {src}")

        print(f"[1/3] 读取 {src} ...")
        messages, errors = load_messages(src, args.encoding)
        print(f"      共 {len(messages)} 条消息"
              f"（用户 {sum(1 for m in messages if m['role'] == 'user')}"
              f" / 助手 {sum(1 for m in messages if m['role'] == 'assistant')}）"
              + (f"，跳过解析失败行 {errors} 条" if errors else ""))

        date_str = output_date(src, messages)

    # 按 agent 分组
    from collections import OrderedDict
    agents_msgs = OrderedDict()
    for msg in messages:
        aname = msg.get("agent_name", "main")
        agents_msgs.setdefault(aname, []).append(msg)

    agent_names = sorted(agents_msgs.keys(), key=lambda a: (a != "kavis", a))
    print(f"\n      发现 {len(agent_names)} 个 agent: {', '.join(agent_names)}")

    for agent_name in agent_names:
        agent_msgs = agents_msgs[agent_name]
        print(f"\n{'='*40}")
        print(f"  Agent: {agent_name} ({len(agent_msgs)} 条消息)")
        print(f"{'='*40}")

        groups = group_sessions(agent_msgs)
        session_count = len(groups)
        print(f"      会话段: {session_count}")

        # 逐会话处理
        prepared = []
        for idx, (sid, msgs) in enumerate(groups, 1):
            orig = len(msgs)
            if not args.full:
                msgs = collapse_to_reports(msgs)
                if args.last > 0:
                    msgs = trim_to_last_turns(msgs, args.last)
            prepared.append((idx, sid, msgs, orig))

        if not args.full:
            kept = sum(len(m) for _, _, m, _ in prepared)
            print(f"      精简: 原始 {len(agent_msgs)} 条 → 保留 {kept} 条")

        if args.merge:
            merged = []
            for _, _, msgs, _ in prepared:
                merged.extend(msgs)
            # merge 模式用全局 date_str
            out = os.path.join(base_out, f"{date_str}.md")
            print(f"[2/3] 生成 Markdown (合并 {session_count} 个会话) ...")
            md = build_md(merged, src, date_str, flat=args.flat,
                          original_total=len(agent_msgs), last_n=args.last)
            print(f"[3/3] 写入 {out} ...")
            with open(out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"完成 ✔  输出: {out}  ({len(md)} 字符)")
        else:
            # 按会话拆分：每个会话独立计算日期，按日期分组输出
            print(f"[2/3] 生成 Markdown (按会话拆分 {session_count} 个文件) ...")
            # 按日期分组
            from collections import defaultdict
            date_groups = defaultdict(list)  # {date_str: [(idx, sid, msgs, orig), ...]}
            for idx, sid, msgs, orig in prepared:
                session_date = output_date_from_messages(msgs) if msgs else date_str
                date_groups[session_date].append((idx, sid, msgs, orig))

            written = []
            for session_date in sorted(date_groups.keys()):
                sessions_in_date = date_groups[session_date]
                out_dir = os.path.join(base_out, agent_name, session_date)
                os.makedirs(out_dir, exist_ok=True)
                # 清理该日期目录下旧的会话文件，保证目录内容 = 本轮完整结果
                # （避免多轮运行残留旧编号文件造成重复）
                for old_file in os.listdir(out_dir):
                    if old_file.startswith(session_date + "-会话") and old_file.endswith(".md"):
                        try:
                            os.remove(os.path.join(out_dir, old_file))
                        except OSError:
                            pass
                for seq, (idx, sid, msgs, orig) in enumerate(sessions_in_date, 1):
                    md = build_session_md(seq, len(sessions_in_date), msgs, src, session_date,
                                          original_total=orig, last_n=args.last, sid=sid)
                    out = os.path.join(out_dir, f"{session_date}-会话{seq:02d}.md")
                    with open(out, "w", encoding="utf-8") as f:
                        f.write(md)
                    written.append((out, len(msgs), session_date))
            print(f"[3/3] 写入 {len(written)} 个文件 ...")
            for out, n, sd in written:
                print(f"      {sd}/{os.path.basename(out)}: {n} 条")
            out_dirs = sorted(set(sd for _, _, sd in written))
            print(f"完成 ✔  输出目录: {', '.join(out_dirs)}")


if __name__ == "__main__":
    main()
