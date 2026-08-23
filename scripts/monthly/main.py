#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每月记忆整理脚本（多 Agent 分区版本）
=======================================
流程：读取上月 weekly 摘要 → 本地模型生成月度摘要 → 云端模型进化 USER/MEMORY/AGENTS
执行时间：每月 1 日 22:30（Windows 计划任务）
"""

import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_SCRIPTS = os.path.dirname(SCRIPT_DIR)
if PROJECT_SCRIPTS not in sys.path:
    sys.path.insert(0, PROJECT_SCRIPTS)

from datetime import datetime, timedelta
from pathlib import Path

from common.utils import (
    FILE_SIZE_LIMITS,
    MARKER_AGENTS,
    MARKER_MEMORY,
    MARKER_USER,
    SPLIT_THRESHOLD,
    TaskLock,
    atomic_write,
    backup_file,
    call_deepseek as call_llm,
    check_and_compact_files,
    ensure_dir,
    get_abstracted_dir,
    get_agent_workspace,
    get_evolution_system_prompt,
    get_today_compact,
    get_today_str,
    list_agents,
    list_monthly_weekly_abstracts,
    log_diff,
    parse_deepseek_evolution,
    read_file_safe,
    send_notification,
    setup_logger,
    split_text_by_chars,
)


def get_last_month() -> tuple[int, int]:
    """返回上一个自然月的 (year, month)。"""
    today = datetime.now()
    first_of_month = today.replace(day=1)
    last_month = first_of_month - timedelta(days=1)
    return last_month.year, last_month.month


def main():
    # ── 每月1日判断（--force 可绕过，用于测试）──
    now = datetime.now()
    if now.day != 1 and "--force" not in sys.argv and "--dry-run" not in sys.argv:
        return

    dry_run = "--dry-run" in sys.argv
    today = get_today_str()
    today_compact = get_today_compact()
    logger = setup_logger("monthly", today_compact)
    logger.info("=" * 60)
    logger.info(f"每月记忆整理开始 - {today}" + (" [DRY RUN]" if dry_run else ""))
    logger.info("=" * 60)

    send_notification("OpenClaw 记忆整理", f"正在进行每月记忆整理 ({today})")

    # ── 幂等锁 ──
    lock = TaskLock("monthly")
    if not lock.acquire():
        msg = "每月任务正在运行中，跳过本次执行"
        logger.warning(msg)
        send_notification("OpenClaw 记忆整理", msg, is_error=True)
        return

    success = False
    try:
        # ════════════════════════════════════════════
        # 获取所有 Agent（main 排在首位）
        # ════════════════════════════════════════════
        agents = list_agents()
        logger.info(f"发现 {len(agents)} 个 Agent: {', '.join(agents)}")

        # 获取上月年月
        year, month = get_last_month()
        month_label = f"{year}-{month:02d}"
        logger.info(f"目标月份: {month_label}")

        # ════════════════════════════════════════════
        # 遍历每个 Agent 进行记忆整理
        # ════════════════════════════════════════════
        all_agents_success = True
        for agent_name in agents:
            logger.info("=" * 60)
            logger.info(f"开始处理 Agent: {agent_name}")
            logger.info("=" * 60)

            agent_success = process_agent(
                agent_name=agent_name,
                year=year,
                month=month,
                month_label=month_label,
                dry_run=dry_run,
                logger=logger,
            )

            if not agent_success:
                all_agents_success = False
                logger.error(f"Agent {agent_name} 处理失败")
                send_notification(
                    "OpenClaw 记忆整理",
                    f"Agent {agent_name} 月度整理失败",
                    is_error=True,
                )

        # ════════════════════════════════════════════
        # 完成
        # ════════════════════════════════════════════
        success = all_agents_success
        logger.info("=" * 60)
        logger.info("每月记忆整理完成")
        logger.info("=" * 60)
        send_notification("OpenClaw 记忆整理", "每月记忆整理完成！")

    except Exception as e:
        logger.exception(f"每月任务异常: {e}")
        send_notification("OpenClaw 记忆整理", f"每月任务异常: {str(e)[:100]}", is_error=True)
    finally:
        lock.release()
        if not success:
            logger.info("本轮任务未成功完成")


def process_agent(
    agent_name: str,
    year: int,
    month: int,
    month_label: str,
    dry_run: bool,
    logger,
) -> bool:
    """处理单个 Agent 的月度记忆整理。"""
    # 获取 Agent 的 workspace 和 abstract 目录
    agent_ws = get_agent_workspace(agent_name)
    abstract_dir = get_abstracted_dir("monthly", agent_name)

    logger.info(f"Agent workspace: {agent_ws}")
    logger.info(f"Agent abstract dir: {abstract_dir}")

    # ── 步骤 1：读取上月 weekly 摘要 ──
    logger.info(f"[步骤 1] 读取 Agent {agent_name} 的上月 weekly 摘要")

    weekly_files = list_monthly_weekly_abstracts(year, month, agent_name=agent_name)
    if not weekly_files:
        logger.info(f"{month_label} 无 weekly 摘要（Agent: {agent_name}），跳过")
        return True  # 无摘要是正常情况，不算失败

    logger.info(f"找到 {len(weekly_files)} 个 weekly 摘要")
    for name, path in weekly_files:
        logger.info(f"  {name}")

    # 合并所有 weekly 摘要
    all_texts = []
    for name, path in weekly_files:
        content = read_file_safe(path)
        if content:
            all_texts.append(f"=== {name} ===\n{content}")

    merged_text = "\n\n".join(all_texts)
    logger.info(f"合并文本总字符: {len(merged_text)}")

    # ── 步骤 2：生成月度摘要 ──
    logger.info(f"[步骤 2] 生成 Agent {agent_name} 的月度摘要")

    if dry_run:
        month_summary = "[DRY RUN] 模拟月度摘要内容，跳过云端模型调用。"
        logger.info("[DRY RUN] 跳过模型调用，使用模拟摘要")
    else:
        system_prompt = (
            "你是一个内容提炼助手。请阅读以下多周的摘要，生成一份月度综合摘要。\n\n"
            "输出要求：\n"
            "1. 输出 2000 字以内的中文月度综合摘要\n"
            "2. 合并各周信息，去除重复\n"
            "3. 按主题/项目分类归纳，呈现月度全貌\n"
            "4. 突出重要决策、里程碑事件、长期趋势\n"
            "5. 保留待办事项和未完成的任务\n"
            "6. 使用条目式结构"
        )

        if len(system_prompt) + len(merged_text) > SPLIT_THRESHOLD:
            logger.info("文本超限，分批调用云端模型")
            chunks = split_text_by_chars(merged_text, SPLIT_THRESHOLD)
            chunk_summaries = []
            for ci, chunk in enumerate(chunks, 1):
                logger.info(f"  分批 {ci}/{len(chunks)} ({len(chunk)} 字符)")
                ok, result, err = call_llm(chunk, system=system_prompt, top_p=0.8, logger=logger)
                if ok:
                    chunk_summaries.append(result)
                else:
                    logger.warning(f"  分批 {ci} 失败: {err}")

            if not chunk_summaries:
                logger.error("所有分批调用失败")
                return False

            if len(chunk_summaries) > 1:
                merged_summaries = "\n\n---\n\n".join(chunk_summaries)
                merge_prompt = (
                    "以下是本月各分块的摘要，请合并为一份完整月度摘要，"
                    "2000 字以内，去除重复，按主题归纳：\n\n" + merged_summaries
                )
                ok, month_summary, err = call_llm(
                    merge_prompt, system=system_prompt, top_p=0.8, logger=logger
                )
                if not ok:
                    logger.warning(f"合并提炼失败，使用分批拼接: {err}")
                    month_summary = "\n\n".join(chunk_summaries)
            else:
                month_summary = chunk_summaries[0]
        else:
            ok, month_summary, err = call_llm(
                merged_text, system=system_prompt, top_p=0.8, logger=logger
            )
            if not ok:
                logger.error(f"云端模型调用失败: {err}")
                return False

    # 写入月度摘要
    header = f"# 月度摘要 {month_label} ({agent_name})\n\n"
    stats = (
        f"> Agent: {agent_name} | "
        f"覆盖周数: {len(weekly_files)} | "
        f"月份: {month_label}\n"
        f"> 总字符: {len(header) + len(month_summary)}\n\n---\n\n"
    )
    abstract_content = header + stats + month_summary + "\n"
    abstract_path = abstract_dir / f"{month_label}-abstracted.md"
    ensure_dir(abstract_dir)
    atomic_write(abstract_path, abstract_content)
    logger.info(f"月度摘要写入: {abstract_path} ({len(abstract_content)} 字符)")

    send_notification("OpenClaw 记忆整理", f"Abstracting Done! ({agent_name})")

    # ── 步骤 3：云端模型进化 USER/MEMORY/AGENTS ──
    logger.info(f"[步骤 3] 云端模型进化 (Agent: {agent_name})")

    if dry_run:
        logger.info("[DRY RUN] 跳过进化步骤")
        return True

    ok = run_evolution(
        abstract_path=abstract_path,
        agent_name=agent_name,
        workspace_dir=agent_ws,
        logger=logger,
    )
    if not ok:
        logger.error(f"云端进化失败 (Agent: {agent_name})")
        return False

    logger.info(f"Agent {agent_name} 处理完成")
    send_notification("OpenClaw 记忆整理", f"{agent_name} 每月整理完成！")
    return True


def run_evolution(
    abstract_path: Path,
    agent_name: str,
    workspace_dir: Path,
    logger,
) -> bool:
    """调用 DeepSeek 进化 USER/MEMORY/AGENTS。"""
    user_md = read_file_safe(workspace_dir / "USER.md")
    memory_md = read_file_safe(workspace_dir / "MEMORY.md")
    agents_md = read_file_safe(workspace_dir / "AGENTS.md")

    if not all([user_md, memory_md, agents_md]):
        missing = []
        if not user_md:
            missing.append("USER.md")
        if not memory_md:
            missing.append("MEMORY.md")
        if not agents_md:
            missing.append("AGENTS.md")
        logger.error(f"workspace 文件缺失 ({agent_name}): {missing}")
        return False

    abstract = read_file_safe(abstract_path)
    if not abstract:
        logger.error(f"摘要文件缺失: {abstract_path}")
        return False

    system_prompt = get_evolution_system_prompt("monthly")

    user_prompt = (
        f"以下是 {agent_name} 的当前三个文档和月度综合摘要，请输出进化后的文档。\n\n"
        "=== 当前 USER.md ===\n"
        f"{user_md}\n\n"
        "=== 当前 MEMORY.md ===\n"
        f"{memory_md}\n\n"
        "=== 当前 AGENTS.md ===\n"
        f"{agents_md}\n\n"
        "=== 月度综合摘要 ===\n"
        f"{abstract}"
    )

    ok, content, err = call_llm(user_prompt, system=system_prompt, top_p=0.8, logger=logger)
    if not ok:
        logger.error(f"DeepSeek 调用失败: {err}")
        return False

    result = parse_deepseek_evolution(content)
    if result is None:
        logger.error("DeepSeek 输出缺少完整分隔标记，终止写入")
        logger.debug(f"输出前500字符: {content[:500]}")
        return False

    files_to_write = [
        (workspace_dir / "USER.md", result["user"], "USER.md"),
        (workspace_dir / "MEMORY.md", result["memory"], "MEMORY.md"),
        (workspace_dir / "AGENTS.md", result["agents"], "AGENTS.md"),
    ]

    # 检查文件大小，超限则压缩
    files_to_write = check_and_compact_files(files_to_write, system_prompt, logger)

    for path, new_content, name in files_to_write:
        old_content = read_file_safe(path) or ""
        bak = backup_file(path)
        logger.info(f"备份 {name} → {bak}")
        log_diff(path, old_content, new_content, f"monthly-{agent_name}", logger)
        atomic_write(path, new_content)
        logger.info(f"写入 {name} ({len(new_content)} 字符)")

    logger.info(f"云端进化完成 (Agent: {agent_name})")
    return True


if __name__ == "__main__":
    main()
