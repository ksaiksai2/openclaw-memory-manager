#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
总调度脚本 - OpenClaw 记忆整理
===============================
每天 22:30 由计划任务触发一次，自动判断当日需要执行的任务并按顺序执行。

执行规则：
  每天必跑 daily
  周日额外跑 weekly（daily 之后）
  每月1日额外跑 monthly（daily 之后，如有 weekly 则 weekly 之后）
"""

import sys
import os
import subprocess
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def find_pythonw():
    """查找 pythonw.exe：优先同目录，回退 PATH。"""
    # 与当前 python.exe 同目录的 pythonw.exe
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.isfile(pythonw):
        return pythonw
    # 回退：直接用 python.exe（会弹窗但能跑）
    return sys.executable


PYTHONW = find_pythonw()


def run_task(name: str, script: str, extra_args: list[str] = None):
    """执行一个子任务，等待完成。"""
    args = [PYTHONW, script] + (extra_args or [])
    print(f"[调度] 开始执行 {name} ...", flush=True)
    result = subprocess.run(
        args,
        cwd=os.path.dirname(script),
        timeout=7200,  # 2小时超时
    )
    status = "成功" if result.returncode == 0 else f"失败(exit={result.returncode})"
    print(f"[调度] {name} {status}", flush=True)
    return result.returncode == 0


def main():
    now = datetime.now()
    weekday = now.weekday()  # 0=周一, 6=周日
    day = now.day

    # 透传 --dry-run 给子脚本
    extra_args = []
    if "--dry-run" in sys.argv:
        extra_args.append("--dry-run")

    daily_script = os.path.join(SCRIPT_DIR, "daily", "main.py")
    weekly_script = os.path.join(SCRIPT_DIR, "weekly", "main.py")
    monthly_script = os.path.join(SCRIPT_DIR, "monthly", "main.py")

    tasks_to_run = []

    # 每天必跑 daily
    tasks_to_run.append(("daily", daily_script))

    # 周日跑 weekly
    if weekday == 6:
        tasks_to_run.append(("weekly", weekly_script))

    # 每月1日跑 monthly
    if day == 1:
        tasks_to_run.append(("monthly", monthly_script))

    print(f"[调度] 日期: {now.strftime('%Y-%m-%d')} 周{['一','二','三','四','五','六','日'][weekday]}")
    print(f"[调度] 待执行: {[t[0] for t in tasks_to_run]}")
    print(f"[调度] 执行顺序: {' → '.join(t[0] for t in tasks_to_run)}")
    if extra_args:
        print(f"[调度] 参数: {' '.join(extra_args)}")
    print()

    all_ok = True
    for name, script in tasks_to_run:
        ok = run_task(name, script, extra_args)
        if not ok:
            all_ok = False

    print()
    if all_ok:
        print("[调度] 全部完成 OK")
    else:
        print("[调度] 部分任务失败")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
