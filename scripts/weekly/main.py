#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周记忆整理脚本
================
流程：读取近7天 daily 摘要 → 本地模型生成周度摘要 → 云端模型进化 USER/MEMORY/AGENTS
执行时间：每周日 23:00（Windows 计划任务）

支持多 Agent 分区：自动发现所有 agent，逐个处理。
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
    ABSTRACTED_WEEKLY_DIR,
    FILE_SIZE_LIMITS,
    MARKER_AGENTS,
    MARKER_MEMORY,
    MARKER_USER,
    SPLIT_THRESHOLD,
    WORKSPACE_DIR,
    TaskLock,
    atomic_write,
    backup_file,
    call_deepseek,
    check_and_compact_files,
    ensure_dir,
    get_abstracted_dir,
    get_agent_workspace,
    get_evolution_system_prompt,
    get_today_compact,
    get_today_str,
    list_agents,
    list_daily_abstracts,
    list_session_files,
    log_diff,
    parse_deepseek_evolution,
    read_file_safe,
    send_notification,
    setup_logger,
    split_text_by_chars,
)

# 为 call_deepseek 创建别名
call_llm = call_deepseek


def main():
    dry_run = "--dry-run" in sys.argv
    today = get_today_str()
    today_compact = get_today_compact()
    logger = setup_logger("weekly", today_compact)
    logger.info("=" * 60)
    logger.info(f"每周记忆整理开始 - {today}" + (" [DRY RUN]" if dry_run else ""))
    logger.info("=" * 60)

    send_notification("OpenClaw 记忆整理", f"正在进行每周记忆整理 ({today})")

    # ── 幂等锁 ──
    lock = TaskLock("weekly")
    if not lock.acquire():
        msg = "每周任务正在运行中，跳过本次执行"
        logger.warning(msg)
        send_notification("OpenClaw 记忆整理", msg, is_error=True)
        return

    success = False
    try:
        # ════════════════════════════════════════════
        # 步骤 0：发现所有 Agent
        # ════════════════════════════════════════════
        agents = list_agents()
        logger.info(f"[步骤 0] 发现 {len(agents)} 个 Agent: {agents}")

        if not agents:
            logger.info("未发现任何 Agent，跳过本周整理")
            send_notification("OpenClaw 记忆整理", "未发现 Agent，跳过", is_error=True)
            return

        # ════════════════════════════════════════════
        # 逐个 Agent 处理
        # ════════════════════════════════════════════
        agent_results = {}  # {agent_name: {"success": bool, "error": str}}
        for agent_name in agents:
            logger.info(f"\n{'─' * 50}")
            logger.info(f"开始处理 Agent: {agent_name}")
            logger.info(f"{'─' * 50}")

            try:
                agent_success = process_agent(agent_name, dry_run, logger)
                agent_results[agent_name] = {"success": agent_success, "error": "" if agent_success else "处理失败"}
                if not agent_success:
                    logger.error(f"Agent {agent_name} 处理失败")
            except Exception as e:
                agent_results[agent_name] = {"success": False, "error": str(e)[:50]}
                logger.exception(f"Agent {agent_name} 处理异常: {e}")

        # ════════════════════════════════════════════
        # 完成 - 汇总结果通知
        # ════════════════════════════════════════════
        all_success = all(r["success"] for r in agent_results.values())
        if all_success:
            success = True
            logger.info("=" * 60)
            logger.info("每周记忆整理完成（所有 Agent 成功）")
            logger.info("=" * 60)
            send_notification("OpenClaw 记忆整理", "每周记忆整理完成！")
        else:
            success_agents = [name for name, r in agent_results.items() if r["success"]]
            failed_agents = [name for name, r in agent_results.items() if not r["success"]]
            detail_parts = []
            if success_agents:
                detail_parts.append(f"成功: {', '.join(success_agents)}")
            if failed_agents:
                detail_parts.append(f"失败: {', '.join(failed_agents)}")
            detail = " | ".join(detail_parts)
            logger.warning(f"每周记忆整理部分失败: {detail}")
            send_notification("OpenClaw 记忆整理", f"部分失败 - {detail}", is_error=True)

    except Exception as e:
        logger.exception(f"每周任务异常: {e}")
        send_notification("OpenClaw 记忆整理", f"每周任务异常: {str(e)[:100]}", is_error=True)
    finally:
        lock.release()
        if not success:
            logger.info("本轮任务未成功完成")


def process_agent(agent_name: str, dry_run: bool, logger) -> bool:
    """处理单个 Agent 的每周记忆整理。

    Args:
        agent_name: Agent 名称
        dry_run: 是否为试运行模式
        logger: 日志记录器

    Returns:
        是否处理成功
    """
    # 获取 Agent 特定的目录
    agent_ws = get_agent_workspace(agent_name)
    abstract_dir = get_abstracted_dir("weekly", agent_name)

    logger.info(f"Agent workspace: {agent_ws}")
    logger.info(f"Weekly abstract dir: {abstract_dir}")

    # ──────────────────────────────────────────────
    # 步骤 1：读取近 7 天 daily 摘要
    # ──────────────────────────────────────────────
    logger.info(f"[步骤 1] 读取 {agent_name} 近 7 天 daily 摘要")

    daily_files = list_daily_abstracts(days=7, agent_name=agent_name)

    if not daily_files:
        logger.info(f"Agent {agent_name} 近 7 天无 daily 摘要，跳过")
        return True  # 无摘要不算失败

    logger.info(f"共找到 {len(daily_files)} 个 daily 摘要")

    # 合并所有 daily 摘要文本
    all_texts = []
    for path in daily_files:
        content = read_file_safe(path)
        if content:
            # 从文件名提取日期
            date_str = path.stem.replace("-abstracted", "")
            all_texts.append(f"=== {date_str} ===\n{content}")

    merged_text = "\n\n".join(all_texts)
    logger.info(f"合并文本总字符: {len(merged_text)}")

    # ──────────────────────────────────────────────
    # 步骤 2：云端模型生成周度摘要
    # ──────────────────────────────────────────────
    logger.info(f"[步骤 2] 为 {agent_name} 生成周度摘要")

    if dry_run:
        week_summary = f"[DRY RUN] 模拟 {agent_name} 周度摘要内容，跳过云端模型调用。"
        logger.info("[DRY RUN] 跳过模型调用，使用模拟摘要")
    else:
        system_prompt = (
            f"你是一个内容提炼助手。请阅读以下 {agent_name} 多天的每日摘要，生成一份周度综合摘要。\n\n"
            "输出要求：\n"
            "1. 输出 1500 字以内的中文周度综合摘要\n"
            "2. 合并各天信息，去除重复\n"
            "3. 按主题/项目分类归纳，而非按日期罗列\n"
            "4. 突出重要决策、任务进展、新增偏好\n"
            "5. 保留待办事项和未完成的任务\n"
            "6. 使用条目式结构"
        )

        # 检测是否需要分批
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
                logger.error(f"Agent {agent_name} 所有分批调用失败")
                return False

            if len(chunk_summaries) > 1:
                merged_summaries = "\n\n---\n\n".join(chunk_summaries)
                merge_prompt = (
                    f"以下是 {agent_name} 本周各分块的摘要，请合并为一份完整周度摘要，"
                    "1500 字以内，去除重复，按主题归纳：\n\n" + merged_summaries
                )
                ok, week_summary, err = call_llm(
                    merge_prompt, system=system_prompt, top_p=0.8, logger=logger
                )
                if not ok:
                    logger.warning(f"合并提炼失败，使用分批拼接: {err}")
                    week_summary = "\n\n".join(chunk_summaries)
            else:
                week_summary = chunk_summaries[0]
        else:
            ok, week_summary, err = call_llm(
                merged_text, system=system_prompt, top_p=0.8, logger=logger
            )
            if not ok:
                logger.error(f"云端模型调用失败: {err}")
                return False

    # 计算日期范围
    dates = [p.stem.replace("-abstracted", "") for p in daily_files]
    dates.sort()
    week_range = f"{dates[0]}至{dates[-1]}"

    # 写入周摘要文件
    header = f"# 每周摘要 {week_range} ({agent_name})\n\n"
    stats = (
        f"> Agent: {agent_name}\n"
        f"> 覆盖天数: {len(daily_files)} | "
        f"日期范围: {week_range}\n"
        f"> 总字符: {len(header) + len(week_summary)}\n\n---\n\n"
    )
    abstract_content = header + stats + week_summary + "\n"
    abstract_path = abstract_dir / f"{week_range}-abstracted.md"
    ensure_dir(abstract_dir)
    atomic_write(abstract_path, abstract_content)
    logger.info(f"周摘要写入: {abstract_path} ({len(abstract_content)} 字符)")

    send_notification("OpenClaw 记忆整理", f"{agent_name} Abstracting Done!", silent=True)

    # ──────────────────────────────────────────────
    # 步骤 3：云端模型进化 USER/MEMORY/AGENTS
    # ──────────────────────────────────────────────
    logger.info(f"[步骤 3] 为 {agent_name} 进化文档")

    if dry_run:
        logger.info("[DRY RUN] 跳过进化步骤")
    else:
        ok = run_evolution(abstract_path, agent_name, agent_ws, logger)
        if not ok:
            logger.error(f"Agent {agent_name} 云端进化失败")
            return False

    logger.info(f"Agent {agent_name} 处理完成")
    send_notification("OpenClaw 记忆整理", f"{agent_name} 每周整理完成！", silent=True)
    return True


def run_evolution(abstract_path: Path, agent_name: str, workspace_dir: Path, logger) -> bool:
    """调用 DeepSeek 进化 USER/MEMORY/AGENTS。

    Args:
        abstract_path: 周摘要文件路径
        agent_name: Agent 名称
        workspace_dir: Agent 的 workspace 目录
        logger: 日志记录器

    Returns:
        是否进化成功
    """
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
        logger.error(f"Agent {agent_name} workspace 文件缺失: {missing}")
        return False

    abstract = read_file_safe(abstract_path)
    if not abstract:
        logger.error(f"摘要文件缺失: {abstract_path}")
        return False

    system_prompt = get_evolution_system_prompt("weekly")

    user_prompt = (
        f"以下是 {agent_name} 的当前三个文档和本周综合摘要，请输出进化后的文档。\n\n"
        "=== 当前 USER.md ===\n"
        f"{user_md}\n\n"
        "=== 当前 MEMORY.md ===\n"
        f"{memory_md}\n\n"
        "=== 当前 AGENTS.md ===\n"
        f"{agents_md}\n\n"
        "=== 本周综合摘要 ===\n"
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
        log_diff(path, old_content, new_content, f"weekly-{agent_name}", logger)
        atomic_write(path, new_content)
        logger.info(f"写入 {name} ({len(new_content)} 字符)")

    logger.info(f"Agent {agent_name} 云端进化完成")
    return True


if __name__ == "__main__":
    main()
