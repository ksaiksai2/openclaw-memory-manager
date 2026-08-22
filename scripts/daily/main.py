#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日记忆整理脚本
================
流程：会话 chunking → 本地模型提炼摘要 → 云端模型进化 USER/MEMORY/AGENTS
执行时间：每日 23:30（Windows 计划任务）
"""

import sys
import os

# 将项目脚本目录加入 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_SCRIPTS = os.path.dirname(SCRIPT_DIR)
if PROJECT_SCRIPTS not in sys.path:
    sys.path.insert(0, PROJECT_SCRIPTS)

from datetime import datetime
from pathlib import Path

from common.utils import (
    ABSTRACTED_DAILY_DIR,
    CHUNKING_DIR,
    CHUNKING_SCRIPT,
    FILE_SIZE_LIMITS,
    LOG_DIR,
    MARKER_AGENTS,
    MARKER_MEMORY,
    MARKER_USER,
    MAX_INPUT_CHARS,
    SPLIT_THRESHOLD,
    WORKSPACE_DIR,
    TaskLock,
    atomic_write,
    backup_file,
    call_deepseek,
    check_and_compact_files,
    ensure_dir,
    get_chunking_dir,
    get_conversation_jsonl,
    get_evolution_system_prompt,
    get_today_compact,
    get_today_str,
    list_session_files,
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
    logger = setup_logger("daily", today_compact)
    logger.info("=" * 60)
    logger.info(f"每日记忆整理开始 - {today}" + (" [DRY RUN]" if dry_run else ""))
    logger.info("=" * 60)

    send_notification("OpenClaw 记忆整理", f"正在进行每日记忆整理 ({today})")

    # ── 幂等锁 ──
    try:
        lock = TaskLock("daily")
        if not lock.acquire():
            msg = "每日任务正在运行中，跳过本次执行"
            logger.warning(msg)
            send_notification("OpenClaw 记忆整理", msg, is_error=True)
            return
    except Exception as e:
        logger.error(f"获取锁失败: {e}")
        send_notification("OpenClaw 记忆整理", f"获取锁失败: {e}", is_error=True)
        return

    success = False
    try:
        # ════════════════════════════════════════════
        # 步骤 1：会话 chunking
        # ════════════════════════════════════════════
        logger.info("[步骤 1] 会话 chunking")
        jsonl_path = get_conversation_jsonl(today)
        chunk_dir = get_chunking_dir(today)
        ensure_dir(chunk_dir)

        no_conversation = False
        if jsonl_path is None:
            logger.info(f"当日无会话文件: {today}.jsonl")
            # 写入空标记文件
            marker = chunk_dir / "NO_CONVERSATION.txt"
            atomic_write(marker, f"当日 {today} 无会话记录\n")
            no_conversation = True
        else:
            logger.info(f"源文件: {jsonl_path}")
            # 调用已有的 chunking 脚本
            ok = run_chunking(jsonl_path, chunk_dir, logger)
            if not ok:
                logger.error("chunking 失败")
                send_notification("OpenClaw 记忆整理", "会话 chunking 失败", is_error=True)
                return

            # 校验输出
            session_files = list_session_files(today)
            if not session_files:
                logger.warning("chunking 完成但无会话文件输出")
                marker = chunk_dir / "NO_CONVERSATION.txt"
                atomic_write(marker, f"当日 {today} chunking 无输出\n")
                no_conversation = True
            else:
                logger.info(f"chunking 完成: {len(session_files)} 个会话文件")

        send_notification("OpenClaw 记忆整理", "Chunking Done!")

        # ════════════════════════════════════════════
        # 步骤 2：本地模型生成摘要
        # ════════════════════════════════════════════
        logger.info("[步骤 2] 云端模型生成摘要")

        if dry_run:
            logger.info("[DRY RUN] 跳过摘要步骤")
            abstract_content = f"# 每日摘要 {today}\n\n[DRY RUN] 模拟摘要内容。\n"
            abstract_path = ABSTRACTED_DAILY_DIR / f"{today}-abstracted.md"
            ensure_dir(ABSTRACTED_DAILY_DIR)
            atomic_write(abstract_path, abstract_content)
        elif no_conversation:
            logger.info("无会话记录，生成空摘要")
            abstract_content = f"# 每日摘要 {today}\n\n当日无会话记录。\n"
            abstract_path = ABSTRACTED_DAILY_DIR / f"{today}-abstracted.md"
            ensure_dir(ABSTRACTED_DAILY_DIR)
            atomic_write(abstract_path, abstract_content)
            logger.info(f"摘要写入: {abstract_path}")
        else:
            session_files = list_session_files(today)
            abstracts = []
            failed_sessions = []

            for i, sf in enumerate(session_files, 1):
                session_name = sf.stem  # e.g. 2026-08-22-会话01
                logger.info(f"  处理会话 {i}/{len(session_files)}: {session_name}")

                content = read_file_safe(sf)
                if not content:
                    logger.warning(f"    文件为空: {sf}")
                    failed_sessions.append((i, session_name, "文件为空"))
                    continue

                # 组装提示词
                system_prompt = (
                    "你是一个内容提炼助手。请阅读以下会话记录，提炼出核心内容，"
                    "包括：讨论了什么话题、做了什么决策、完成了什么任务、"
                    "有什么待办事项、用户的重要偏好或习惯。\n"
                    "输出要求：\n"
                    "1. 输出 1000 字以内的中文摘要\n"
                    "2. 保留关键事实和数据，省略过程细节\n"
                    "3. 使用条目式结构\n"
                    "4. 不要输出多余的解释或开场白"
                )

                # 检测是否需要分割
                total_len = len(system_prompt) + len(content)
                if total_len > SPLIT_THRESHOLD:
                    logger.info(f"    内容 {len(content)} 字符，需二次分割")
                    chunks = split_text_by_chars(content, SPLIT_THRESHOLD)
                    chunk_summaries = []
                    for ci, chunk in enumerate(chunks, 1):
                        logger.info(f"    分块 {ci}/{len(chunks)} ({len(chunk)} 字符)")
                        ok, result, err = call_deepseek(
                            chunk, system=system_prompt, top_p=0.8, logger=logger
                        )
                        if ok:
                            chunk_summaries.append(result)
                        else:
                            logger.warning(f"    分块 {ci} 失败: {err}")
                    if chunk_summaries:
                        # 合并分块摘要后再提炼一次
                        merged = "\n\n---\n\n".join(chunk_summaries)
                        merge_prompt = (
                            "以下是同一会话记录的多个分块摘要，请合并为一份完整摘要，"
                            "1000 字以内，去除重复内容：\n\n" + merged
                        )
                        ok, result, err = call_deepseek(
                            merge_prompt, system=system_prompt, top_p=0.8, logger=logger
                        )
                        if ok:
                            abstracts.append(f"## {session_name}\n\n{result}\n")
                        else:
                            logger.warning(f"    合并摘要失败: {err}")
                            # 使用分块摘要的拼接
                            abstracts.append(
                                f"## {session_name}\n\n"
                                + "\n\n".join(chunk_summaries)
                                + "\n"
                            )
                    else:
                        failed_sessions.append((i, session_name, "所有分块调用失败"))
                else:
                    try:
                        logger.info(f"    调用 DeepSeek 开始 (session {i}, input={len(content)} chars)")
                        ok, result, err = call_deepseek(
                            content, system=system_prompt, top_p=0.8, logger=logger
                        )
                        logger.info(f"    调用 DeepSeek 返回: ok={ok}, len={len(result) if result else 0}")
                    except Exception as e:
                        logger.error(f"    调用 DeepSeek 异常: {e}")
                        ok, result, err = False, "", str(e)
                    if ok:
                        abstracts.append(f"## {session_name}\n\n{result}\n")
                    else:
                        logger.warning(f"    调用失败: {err}")
                        failed_sessions.append((i, session_name, err))

            # 组装 daily 摘要文件
            header = f"# 每日摘要 {today}\n\n"
            stats = f"> 处理会话: {len(session_files)} | 成功: {len(abstracts)} | 失败: {len(failed_sessions)}\n"
            if failed_sessions:
                fail_info = ", ".join(
                    f"{name}({reason})" for _, name, reason in failed_sessions
                )
                stats += f"> 失败会话: {fail_info}\n"
            stats += "\n---\n\n"

            body = "\n---\n\n".join(abstracts)
            total_chars = len(header) + len(stats) + len(body)
            stats_line = stats.replace(
                "> 处理会话:",
                f"> 总字符: {total_chars} | 处理会话:",
            )

            abstract_content = header + stats_line + body + "\n"
            abstract_path = ABSTRACTED_DAILY_DIR / f"{today}-abstracted.md"
            ensure_dir(ABSTRACTED_DAILY_DIR)
            atomic_write(abstract_path, abstract_content)
            logger.info(f"摘要写入: {abstract_path} ({total_chars} 字符)")

        send_notification("OpenClaw 记忆整理", "Abstracting Done!")

        # ════════════════════════════════════════════
        # 步骤 3：云端模型进化 USER/MEMORY/AGENTS
        # ════════════════════════════════════════════
        logger.info("[步骤 3] 云端模型进化")

        if dry_run:
            logger.info("[DRY RUN] 跳过进化步骤")
        elif no_conversation:
            logger.info("无会话摘要，跳过进化步骤")
        else:
            ok = run_evolution(today, logger)
            if not ok:
                logger.error("云端进化失败")
                send_notification("OpenClaw 记忆整理", "云端进化失败", is_error=True)
                return

        # ════════════════════════════════════════════
        # 完成
        # ════════════════════════════════════════════
        success = True
        logger.info("=" * 60)
        logger.info("每日记忆整理完成")
        logger.info("=" * 60)
        send_notification("OpenClaw 记忆整理", "每日记忆整理完成！")

    except Exception as e:
        logger.exception(f"每日任务异常: {e}")
        send_notification("OpenClaw 记忆整理", f"每日任务异常: {str(e)[:100]}", is_error=True)
    finally:
        lock.release()
        if not success:
            logger.info("本轮任务未成功完成")


def run_chunking(jsonl_path: Path, output_dir: Path, logger) -> bool:
    """运行 chunking 脚本，将 jsonl 转为会话 .md 文件。"""
    import subprocess

    cmd = [
        sys.executable,
        str(CHUNKING_SCRIPT),
        str(jsonl_path),
        "--out",
        str(output_dir),
    ]
    logger.info(f"执行: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                logger.info(f"  [chunking] {line}")
        if result.returncode != 0:
            logger.error(f"chunking 退出码 {result.returncode}")
            if result.stderr:
                logger.error(f"  stderr: {result.stderr.strip()}")
            return False
        return True
    except subprocess.TimeoutExpired:
        logger.error("chunking 超时 (300s)")
        return False
    except Exception as e:
        logger.error(f"chunking 异常: {e}")
        return False


def run_evolution(date_str: str, logger) -> bool:
    """调用 DeepSeek 进化 USER/MEMORY/AGENTS。"""
    # 读取三个 workspace 文件
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

    # 读取当日摘要
    abstract_path = ABSTRACTED_DAILY_DIR / f"{date_str}-abstracted.md"
    abstract = read_file_safe(abstract_path)
    if not abstract:
        logger.error(f"摘要文件缺失: {abstract_path}")
        return False

    # 组装提示词
    system_prompt = get_evolution_system_prompt("daily")

    user_prompt = (
        "以下是当前三个文档和今日会话摘要，请输出进化后的文档。\n\n"
        "=== 当前 USER.md ===\n"
        f"{user_md}\n\n"
        "=== 当前 MEMORY.md ===\n"
        f"{memory_md}\n\n"
        "=== 当前 AGENTS.md ===\n"
        f"{agents_md}\n\n"
        "=== 今日会话摘要 ===\n"
        f"{abstract}"
    )

    ok, content, err = call_deepseek(user_prompt, system=system_prompt, top_p=0.8, logger=logger)
    if not ok:
        logger.error(f"DeepSeek 调用失败: {err}")
        return False

    # 解析输出
    result = parse_deepseek_evolution(content)
    if result is None:
        logger.error("DeepSeek 输出缺少完整分隔标记，终止写入")
        logger.debug(f"输出前500字符: {content[:500]}")
        return False

    # 备份 + 压缩检查 + 写入
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
        log_diff(path, old_content, new_content, "daily", logger)
        atomic_write(path, new_content)
        logger.info(f"写入 {name} ({len(new_content)} 字符)")

    logger.info("云端进化完成")
    return True


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run_log", "crash.log")
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"CRASH at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(traceback.format_exc())
            f.write(f"\n{'='*60}\n")
