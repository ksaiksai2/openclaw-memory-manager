#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每周记忆整理脚本
================
流程：读取近7天 daily 摘要 → 本地模型生成周度摘要 → 云端模型进化 USER/MEMORY/AGENTS
执行时间：每周日 23:00（Windows 计划任务）
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
    ABSTRACTED_DAILY_DIR,
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
    get_evolution_system_prompt,
    get_today_compact,
    get_today_str,
    log_diff,
    parse_deepseek_evolution,
    read_file_safe,
    send_notification,
    setup_logger,
    split_text_by_chars,
)


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
        # 步骤 1：读取近 7 天 daily 摘要
        # ════════════════════════════════════════════
        logger.info("[步骤 1] 读取近 7 天 daily 摘要")

        end_date = datetime.now()
        daily_files = []
        for i in range(7):
            d = end_date - timedelta(days=i)
            date_str = d.strftime("%Y-%m-%d")
            path = ABSTRACTED_DAILY_DIR / f"{date_str}-abstracted.md"
            if path.exists():
                daily_files.append((date_str, path))
                logger.info(f"  找到: {path.name}")
            else:
                logger.info(f"  缺失: {date_str}-abstracted.md")

        if not daily_files:
            logger.info("近 7 天无 daily 摘要，跳过本周整理")
            send_notification("OpenClaw 记忆整理", "无可用摘要，跳过", is_error=True)
            return

        logger.info(f"共找到 {len(daily_files)} 个 daily 摘要")

        # 合并所有 daily 摘要文本
        all_texts = []
        for date_str, path in daily_files:
            content = read_file_safe(path)
            if content:
                all_texts.append(f"=== {date_str} ===\n{content}")

        merged_text = "\n\n".join(all_texts)
        logger.info(f"合并文本总字符: {len(merged_text)}")
        send_notification("OpenClaw 记忆整理", "Chunking Done!")

        # ════════════════════════════════════════════
        # 步骤 2：云端模型生成周度摘要
        # ════════════════════════════════════════════
        logger.info("[步骤 2] 云端模型生成周度摘要")

        if dry_run:
            week_summary = "[DRY RUN] 模拟周度摘要内容，跳过云端模型调用。"
            logger.info("[DRY RUN] 跳过模型调用，使用模拟摘要")
        else:
            system_prompt = (
                "你是一个内容提炼助手。请阅读以下多天的每日摘要，生成一份周度综合摘要。\n\n"
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
                    ok, result, err = call_deepseek(chunk, system=system_prompt, top_p=0.8, logger=logger)
                    if ok:
                        chunk_summaries.append(result)
                    else:
                        logger.warning(f"  分批 {ci} 失败: {err}")

                if not chunk_summaries:
                    logger.error("所有分批调用失败")
                    send_notification("OpenClaw 记忆整理", "云端模型调用全部失败", is_error=True)
                    return

                if len(chunk_summaries) > 1:
                    merged_summaries = "\n\n---\n\n".join(chunk_summaries)
                    merge_prompt = (
                        "以下是本周各分块的摘要，请合并为一份完整周度摘要，"
                        "1500 字以内，去除重复，按主题归纳：\n\n" + merged_summaries
                    )
                    ok, week_summary, err = call_deepseek(
                        merge_prompt, system=system_prompt, top_p=0.8, logger=logger
                    )
                    if not ok:
                        logger.warning(f"合并提炼失败，使用分批拼接: {err}")
                        week_summary = "\n\n".join(chunk_summaries)
                else:
                    week_summary = chunk_summaries[0]
            else:
                ok, week_summary, err = call_deepseek(
                    merged_text, system=system_prompt, top_p=0.8, logger=logger
                )
                if not ok:
                    logger.error(f"云端模型调用失败: {err}")
                    send_notification("OpenClaw 记忆整理", f"云端模型调用失败: {err}", is_error=True)
                    return

        # 计算日期范围
        dates = [d for d, _ in daily_files]
        dates.sort()
        week_range = f"{dates[0]}至{dates[-1]}"

        # 写入周摘要文件
        header = f"# 每周摘要 {week_range}\n\n"
        stats = (
            f"> 覆盖天数: {len(daily_files)} | "
            f"日期范围: {week_range}\n"
            f"> 总字符: {len(header) + len(week_summary)}\n\n---\n\n"
        )
        abstract_content = header + stats + week_summary + "\n"
        abstract_path = ABSTRACTED_WEEKLY_DIR / f"{week_range}-abstracted.md"
        ensure_dir(ABSTRACTED_WEEKLY_DIR)
        atomic_write(abstract_path, abstract_content)
        logger.info(f"周摘要写入: {abstract_path} ({len(abstract_content)} 字符)")

        send_notification("OpenClaw 记忆整理", "Abstracting Done!")

        # ════════════════════════════════════════════
        # 步骤 3：云端模型进化 USER/MEMORY/AGENTS
        # ════════════════════════════════════════════
        logger.info("[步骤 3] 云端模型进化")

        if dry_run:
            logger.info("[DRY RUN] 跳过进化步骤")
        else:
            ok = run_evolution(abstract_path, logger)
            if not ok:
                logger.error("云端进化失败")
                send_notification("OpenClaw 记忆整理", "云端进化失败", is_error=True)
                return

        # ════════════════════════════════════════════
        # 完成
        # ════════════════════════════════════════════
        success = True
        logger.info("=" * 60)
        logger.info("每周记忆整理完成")
        logger.info("=" * 60)
        send_notification("OpenClaw 记忆整理", "每周记忆整理完成！")

    except Exception as e:
        logger.exception(f"每周任务异常: {e}")
        send_notification("OpenClaw 记忆整理", f"每周任务异常: {str(e)[:100]}", is_error=True)
    finally:
        lock.release()
        if not success:
            logger.info("本轮任务未成功完成")


def run_evolution(abstract_path: Path, logger) -> bool:
    """调用 DeepSeek 进化 USER/MEMORY/AGENTS。"""
    user_md = read_file_safe(WORKSPACE_DIR / "USER.md")
    memory_md = read_file_safe(WORKSPACE_DIR / "MEMORY.md")
    agents_md = read_file_safe(WORKSPACE_DIR / "AGENTS.md")

    if not all([user_md, memory_md, agents_md]):
        missing = []
        if not user_md:
            missing.append("USER.md")
        if not memory_md:
            missing.append("MEMORY.md")
        if not agents_md:
            missing.append("AGENTS.md")
        logger.error(f"workspace 文件缺失: {missing}")
        return False

    abstract = read_file_safe(abstract_path)
    if not abstract:
        logger.error(f"摘要文件缺失: {abstract_path}")
        return False

    system_prompt = get_evolution_system_prompt("weekly")

    user_prompt = (
        "以下是当前三个文档和本周综合摘要，请输出进化后的文档。\n\n"
        "=== 当前 USER.md ===\n"
        f"{user_md}\n\n"
        "=== 当前 MEMORY.md ===\n"
        f"{memory_md}\n\n"
        "=== 当前 AGENTS.md ===\n"
        f"{agents_md}\n\n"
        "=== 本周综合摘要 ===\n"
        f"{abstract}"
    )

    ok, content, err = call_deepseek(user_prompt, system=system_prompt, top_p=0.8, logger=logger)
    if not ok:
        logger.error(f"DeepSeek 调用失败: {err}")
        return False

    result = parse_deepseek_evolution(content)
    if result is None:
        logger.error("DeepSeek 输出缺少完整分隔标记，终止写入")
        logger.debug(f"输出前500字符: {content[:500]}")
        return False

    files_to_write = [
        (WORKSPACE_DIR / "USER.md", result["user"], "USER.md"),
        (WORKSPACE_DIR / "MEMORY.md", result["memory"], "MEMORY.md"),
        (WORKSPACE_DIR / "AGENTS.md", result["agents"], "AGENTS.md"),
    ]

    # 检查文件大小，超限则压缩
    files_to_write = check_and_compact_files(files_to_write, system_prompt, logger)

    for path, new_content, name in files_to_write:
        old_content = read_file_safe(path) or ""
        bak = backup_file(path)
        logger.info(f"备份 {name} → {bak}")
        log_diff(path, old_content, new_content, "weekly", logger)
        atomic_write(path, new_content)
        logger.info(f"写入 {name} ({len(new_content)} 字符)")

    logger.info("云端进化完成")
    return True


if __name__ == "__main__":
    main()
