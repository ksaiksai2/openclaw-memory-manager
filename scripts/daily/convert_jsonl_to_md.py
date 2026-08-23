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
            agent_name = sk.split(":")[1] if sk.startswith("agent:") and sk.count(":") >= 2 else "main"
            messages.append({
                "sessionId": str(obj.get("sessionId", "")),
                "role": str(obj.get("role", "")),
                "content": text,
                "ts": ts,
                "agent_name": agent_name,
            })
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

    agent_names = sorted(agents_msgs.keys(), key=lambda a: (a != "main", a))
    print(f"      发现 {len(agent_names)} 个 agent: {', '.join(agent_names)}")

    base_out = args.out or os.path.dirname(os.path.abspath(src))

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
            out = os.path.join(base_out, f"{date_str}.md")
            print(f"[2/3] 生成 Markdown (合并 {session_count} 个会话) ...")
            md = build_md(merged, src, date_str, flat=args.flat,
                          original_total=len(agent_msgs), last_n=args.last)
            print(f"[3/3] 写入 {out} ...")
            with open(out, "w", encoding="utf-8") as f:
                f.write(md)
            print(f"完成 ✔  输出: {out}  ({len(md)} 字符)")
        else:
            # 输出到 <base_out>/<agent_name>/<date>/
            out_dir = os.path.join(base_out, agent_name, date_str)
            os.makedirs(out_dir, exist_ok=True)
            print(f"[2/3] 生成 Markdown (按会话拆分 {session_count} 个文件) ...")
            written = []
            for idx, sid, msgs, orig in prepared:
                md = build_session_md(idx, session_count, msgs, src, date_str,
                                      original_total=orig, last_n=args.last, sid=sid)
                out = os.path.join(out_dir, f"{date_str}-会话{idx:02d}.md")
                with open(out, "w", encoding="utf-8") as f:
                    f.write(md)
                written.append((out, len(msgs)))
            print(f"[3/3] 写入 {len(written)} 个文件 ...")
            for out, n in written:
                print(f"      会话 {os.path.basename(out)}: {n} 条")
            print(f"完成 ✔  输出目录: {out_dir}")


if __name__ == "__main__":
    main()
