#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rechunk_daily.py — 每日增量会话 chunking（双份输出）
====================================================
与全量脚本 rechunk_all.py 输出完全一致，但只处理指定日期
(默认今天) 的会话数据，避免每次全量重扫所有 session 文件。

双份输出：
  1. 嵌套分类源（人工翻阅）: memory-chunking/{agent}/{date}/{date}-会话N.md
  2. 扁平检索副本（语义搜索）: workspace/memory/{agent}-{date}-会话N.md

增量判定（双重筛）：
  1. 粗筛：文件 mtime >= 目标日零点（普通 jsonl 当天活跃 + reset 文件当天被 reset）
  2. 精筛：读内容后，最后一条消息的日期 == 目标日（跨天会话归结束日）

与全量脚本的一致性：
  - 同一 sessionId 同一天只生成一个文件，编号排序规则与全量脚本相同
  - daily 每天跑 = 全量脚本一次跑的等价结果（可互为校验）

用法:
    python rechunk_daily.py [--date YYYY-MM-DD] [--nested-root 路径] [--flat-dir 路径]
                            [--agents-dir 路径] [--agent-ids kavis,echo] [--dry-run]
"""

import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_SCRIPTS = os.path.dirname(SCRIPT_DIR)
if PROJECT_SCRIPTS not in sys.path:
    sys.path.insert(0, PROJECT_SCRIPTS)

from common.chunk_core import (
    cleanup_date_outputs,
    collapse_to_reports,
    date_of,
    get_direct_agents,
    group_by_session,
    load_daily_sessions_messages,
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
    p = argparse.ArgumentParser(description="每日增量会话 chunking（双份输出）")
    p.add_argument("--date", default=None,
                   help="目标日期 YYYY-MM-DD (默认: 今天)")
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
    date_str = args.date or datetime.now().strftime("%Y-%m-%d")
    agents_dir = args.agents_dir
    nested_root = args.nested_root
    flat_dir = args.flat_dir

    # 校验日期格式
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        sys.exit(f"日期格式错误: {date_str} (应为 YYYY-MM-DD)")

    if not os.path.isdir(agents_dir):
        sys.exit(f"找不到 agents 目录: {agents_dir}")

    agent_ids = [a.strip() for a in args.agent_ids.split(",") if a.strip()] \
        if args.agent_ids else get_direct_agents(agents_dir, OPENCLAW_ROOT)
    if not agent_ids:
        sys.exit("未找到任何直属 agent")

    print(f"目标日期:       {date_str}")
    print(f"agents 目录:    {agents_dir}")
    print(f"嵌套分类根:     {nested_root}")
    print(f"扁平检索副本:   {flat_dir}")
    print(f"处理 agent:     {', '.join(agent_ids)}")
    print(f"模式:           {'DRY-RUN（不写文件）' if args.dry_run else '实际执行'}")
    print("=" * 60)

    if not args.dry_run:
        os.makedirs(nested_root, exist_ok=True)
        os.makedirs(flat_dir, exist_ok=True)

    grand_files = grand_msgs = grand_scanned = 0

    for agent_id in agent_ids:
        sessions_dir = os.path.join(agents_dir, agent_id, "sessions")
        if not os.path.isdir(sessions_dir):
            print(f"[跳过] {agent_id}: sessions 目录不存在")
            continue

        print(f"\n=== Agent: {agent_id} ===")
        messages, errors, scanned = load_daily_sessions_messages(sessions_dir, date_str)
        if not messages:
            print(f"  {date_str} 无会话数据"
                  f"（粗筛扫描 {scanned} 个目标日文件"
                  + (f"，解析错误 {errors} 行" if errors else "") + "）")
            continue
        print(f"  原始消息: {len(messages)} 条"
              f"（用户 {sum(1 for m in messages if m['role']=='user')}"
              f" / 助手 {sum(1 for m in messages if m['role']=='assistant')}）"
              f" · 粗筛文件 {scanned} 个")

        # 按 sessionId 分组 → 压缩 → 归类
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

        # 清理目标日期旧输出（双份）
        if not args.dry_run:
            cleanup_date_outputs(agent_id, date_str, nested_root, flat_dir)

        # 写新文件（双份）
        for d in sorted(date_groups.keys()):
            sessions = date_groups[d]
            for seq, (sid, orig_n, kept) in enumerate(sessions, 1):
                if args.dry_run:
                    print(f"  [计划] {agent_id}/{d}/{d}-会话{seq:02d}.md"
                          f" ({len(kept)} 条, 原始 {orig_n})")
                else:
                    write_session_outputs(
                        agent_id, d, seq, len(sessions), kept, orig_n, sid,
                        nested_root, flat_dir)
            print(f"  {d}: {len(sessions)} 个会话文件"
                  + (f"（跳空 {skipped}）" if skipped else ""))

        n_files = sum(len(v) for v in date_groups.values())
        n_msgs = sum(len(m) for v in date_groups.values() for _, _, m in v)
        grand_files += n_files
        grand_msgs += n_msgs
        grand_scanned += scanned
        if errors:
            print(f"  [警告] 解析错误 {errors} 行")

    print("=" * 60)
    print(f"总计: {grand_files} 个文件（每份双写）· {grand_msgs} 条保留消息"
          f" · 粗筛扫描 {grand_scanned} 个目标日文件")
    print("完成 ✔" if not args.dry_run else "DRY-RUN 结束（未写文件）")


if __name__ == "__main__":
    main()