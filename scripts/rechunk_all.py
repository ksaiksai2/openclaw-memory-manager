#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rechunk_all.py — 全量会话重chunk（双份输出）
============================================
将 agents/<id>/sessions/ 下的所有会话数据（含已 reset 的历史文件）
生成 Markdown 并双份写入：
  1. 嵌套分类源（人工翻阅）: memory-chunking/{agent}/{date}/{date}-会话N.md
  2. 扁平检索副本（语义搜索）: workspace/memory/{agent}-{date}-会话N.md

内容结构保持与现有 MD 一致：user 每条消息 + assistant 每回合最后一条。

用法:
    python rechunk_all.py [--nested-root 路径] [--flat-dir 路径]
                          [--agents-dir 路径] [--agent-ids kavis,echo] [--dry-run]
"""

import argparse
import os
import sys
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_SCRIPTS = os.path.dirname(SCRIPT_DIR)
if PROJECT_SCRIPTS not in sys.path:
    sys.path.insert(0, PROJECT_SCRIPTS)

from common.chunk_core import (
    cleanup_agent_outputs,
    collapse_to_reports,
    date_of,
    get_direct_agents,
    group_by_session,
    load_sessions_messages,
    write_session_outputs,
)

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

OPENCLAW_ROOT = os.path.expanduser(r"~\ .openclaw".replace(" ", ""))
AGENTS_DIR_DEFAULT = os.path.join(OPENCLAW_ROOT, "agents")
PROJECT_ROOT = PROJECT_SCRIPTS  # 项目根 = openclaw-memory-manager
NESTED_ROOT_DEFAULT = os.path.join(PROJECT_ROOT, "memory-chunking")
FLAT_DIR_DEFAULT = os.path.join(OPENCLAW_ROOT, "workspace", "memory")


def parse_args():
    p = argparse.ArgumentParser(description="全量会话重chunk（双份输出：嵌套源+扁平副本）")
    p.add_argument("--nested-root", default=NESTED_ROOT_DEFAULT,
                   help=f"嵌套分类根目录 (默认 {NESTED_ROOT_DEFAULT})")
    p.add_argument("--flat-dir", default=FLAT_DIR_DEFAULT,
                   help=f"扁平检索副本目录 (默认 {FLAT_DIR_DEFAULT})")
    p.add_argument("--agents-dir", default=AGENTS_DIR_DEFAULT,
                   help=f"openclaw agents 根目录 (默认 {AGENTS_DIR_DEFAULT})")
    p.add_argument("--agent-ids", default=None,
                   help="只处理指定 agent，逗号分隔 (默认: 从 openclaw.json 读直属 agent)")
    p.add_argument("--dry-run", action="store_true", help="只打印计划，不写文件")
    return p.parse_args()


def main():
    args = parse_args()
    agents_dir = args.agents_dir
    nested_root = args.nested_root
    flat_dir = args.flat_dir

    if not os.path.isdir(agents_dir):
        sys.exit(f"找不到 agents 目录: {agents_dir}")

    agent_ids = [a.strip() for a in args.agent_ids.split(",") if a.strip()] \
        if args.agent_ids else get_direct_agents(agents_dir, OPENCLAW_ROOT)
    if not agent_ids:
        sys.exit("未找到任何直属 agent")

    print(f"agents 目录:    {agents_dir}")
    print(f"嵌套分类根:     {nested_root}")
    print(f"扁平检索副本:   {flat_dir}")
    print(f"处理 agent:     {', '.join(agent_ids)}")
    print(f"模式:           {'DRY-RUN（不写文件）' if args.dry_run else '实际执行'}")
    print("=" * 60)

    if not args.dry_run:
        os.makedirs(nested_root, exist_ok=True)
        os.makedirs(flat_dir, exist_ok=True)

    grand_files = grand_msgs = grand_skipped = 0

    for agent_id in agent_ids:
        sessions_dir = os.path.join(agents_dir, agent_id, "sessions")
        if not os.path.isdir(sessions_dir):
            print(f"[跳过] {agent_id}: sessions 目录不存在")
            continue

        print(f"\n=== Agent: {agent_id} ===")
        messages, errors = load_sessions_messages(sessions_dir)
        if not messages:
            print(f"  无消息（解析错误 {errors} 条）")
            continue
        print(f"  原始消息: {len(messages)} 条"
              f"（用户 {sum(1 for m in messages if m['role']=='user')}"
              f" / 助手 {sum(1 for m in messages if m['role']=='assistant')}）")

        # 每会话：压缩 + 按最后消息日期归类
        date_groups = defaultdict(list)  # date -> [(sid, orig_n, kept)]
        skipped = 0
        for sid, msgs in group_by_session(messages):
            orig_n = len(msgs)
            kept = collapse_to_reports(msgs)
            if not kept:
                skipped += 1
                continue
            d = date_of(kept)
            date_groups[d].append((sid, orig_n, kept))

        # 清理旧输出（双份）
        if not args.dry_run:
            cleanup_agent_outputs(agent_id, nested_root, flat_dir)

        # 写新文件（双份）
        for d in sorted(date_groups.keys()):
            sessions = date_groups[d]
            for seq, (sid, orig_n, kept) in enumerate(sessions, 1):
                if args.dry_run:
                    print(f"  [计划] {agent_id}/{d}/{d}-会话{seq:02d}.md"
                          f" ({len(kept)} 条, 原始 {orig_n})")
                else:
                    np_, fp_ = write_session_outputs(
                        agent_id, d, seq, len(sessions), kept, orig_n, sid,
                        nested_root, flat_dir)
            print(f"  {d}: {len(sessions)} 个会话文件"
                  + (f"（跳空 {skipped}）" if skipped else ""))

        n_files = sum(len(v) for v in date_groups.values())
        n_msgs = sum(len(m) for v in date_groups.values() for _, _, m in v)
        grand_files += n_files
        grand_msgs += n_msgs
        grand_skipped += skipped
        if errors:
            print(f"  [警告] 解析错误 {errors} 行")

    print("=" * 60)
    print(f"总计: {grand_files} 个文件（每份双写）· {grand_msgs} 条保留消息"
          + (f" · {grand_skipped} 个空会话跳过" if grand_skipped else ""))
    print("完成 ✔" if not args.dry_run else "DRY-RUN 结束（未写文件）")


if __name__ == "__main__":
    main()