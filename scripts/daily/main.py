#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日记忆整理脚本
================
流程：会话 chunking（按 agent 分区）→ 逐 agent 提炼摘要 → 逐 agent 进化
执行时间：每日 22:30（Windows 计划任务 → run_all.py）
"""

import sys
import os

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
    MARKER_AGENTS,
    MARKER_MEMORY,
    MARKER_USER,
    MAX_INPUT_CHARS,
    SPLIT_THRESHOLD,
    TaskLock,
    atomic_write,
    backup_file,
    call_deepseek,
    check_and_compact_files,
    ensure_dir,
    get_abstracted_dir,
    get_agent_workspace,
    get_chunking_dir,
    get_conversation_jsonl,
    get_evolution_system_prompt,
    get_today_compact,
    get_today_str,
    list_agents,
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
        # 步骤 1：会话 chunking（一次性，按 agent 分区输出）
        # ════════════════════════════════════════════
        logger.info("[步骤 1] 会话 chunking")
        jsonl_path = get_conversation_jsonl(today)
        # chunking 输出到 CHUNKING_DIR 根目录，脚本内部按 agent 分子目录
        chunk_base = CHUNKING_DIR
        ensure_dir(chunk_base)

        no_conversation = False
        if jsonl_path is None:
            logger.info(f"当日无会话文件: {today}.jsonl")
            marker = chunk_base / "NO_CONVERSATION.txt"
            atomic_write(marker, f"当日 {today} 无会话记录\n")
            no_conversation = True
        else:
            logger.info(f"源文件: {jsonl_path}")
            ok = run_chunking(jsonl_path, chunk_base, logger)
            if not ok:
                logger.error("chunking 失败")
                send_notification("OpenClaw 记忆整理", "会话 chunking 失败", is_error=True)
                return

        # 发现所有 agent
        agents = list_agents(today) if not no_conversation else []
        if not agents and not no_conversation:
            logger.warning("chunking 完成但未发现任何 agent")
            no_conversation = True

        if agents:
            logger.info(f"发现 {len(agents)} 个 agent: {', '.join(agents)}")

        send_notification("OpenClaw 记忆整理", "Chunking Done!")

        # ════════════════════════════════════════════
        # 步骤 2 & 3：逐 agent 提炼摘要 + 进化（main 优先）
        # ════════════════════════════════════════════
        for agent_name in agents:
            agent_ws = get_agent_workspace(agent_name)
            abstract_dir = get_abstracted_dir("daily", agent_name)
            session_files = list_session_files(today, agent_name)

            logger.info(f"{'='*40}")
            logger.info(f"  Agent: {agent_name} | Workspace: {agent_ws}")
            logger.info(f"  会话文件: {len(session_files)} 个")
            logger.info(f"{'='*40}")

            # ── 步骤 2：提炼摘要 ──
            logger.info(f"[{agent_name}] 步骤 2: 提炼摘要")

            if dry_run:
                logger.info(f"[{agent_name}] [DRY RUN] 跳过摘要")
                abstract_content = f"# 每日摘要 {today}\n\n[DRY RUN] 模拟摘要内容。\n"
                abstract_path = abstract_dir / f"{today}-abstracted.md"
                ensure_dir(abstract_dir)
                atomic_write(abstract_path, abstract_content)
            else:
                abstracts = []
                failed_sessions = []

                for i, sf in enumerate(session_files, 1):
                    session_name = sf.stem
                    logger.info(f"  处理会话 {i}/{len(session_files)}: {session_name}")

                    content = read_file_safe(sf)
                    if not content:
                        logger.warning(f"    文件为空: {sf}")
                        failed_sessions.append((i, session_name, "文件为空"))
                        continue

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
                                abstracts.append(
                                    f"## {session_name}\n\n"
                                    + "\n\n".join(chunk_summaries)
                                    + "\n"
                                )
                        else:
                            failed_sessions.append((i, session_name, "所有分块调用失败"))
                    else:
                        try:
                            logger.info(f"    调用 LLM 开始 (session {i}, input={len(content)} chars)")
                            ok, result, err = call_deepseek(
                                content, system=system_prompt, top_p=0.8, logger=logger
                            )
                            logger.info(f"    调用 LLM 返回: ok={ok}, len={len(result) if result else 0}")
                        except Exception as e:
                            logger.error(f"    调用 LLM 异常: {e}")
                            ok, result, err = False, "", str(e)
                        if ok:
                            abstracts.append(f"## {session_name}\n\n{result}\n")
                        else:
                            logger.warning(f"    调用失败: {err}")
                            failed_sessions.append((i, session_name, err))

                # 组装摘要文件
                header = f"# 每日摘要 {today} ({agent_name})\n\n"
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
                abstract_path = abstract_dir / f"{today}-abstracted.md"
                ensure_dir(abstract_dir)
                atomic_write(abstract_path, abstract_content)
                logger.info(f"[{agent_name}] 摘要写入: {abstract_path} ({total_chars} 字符)")

            send_notification("OpenClaw 记忆整理", f"{agent_name} Abstracting Done!")

            # ── 步骤 3：进化 ──
            logger.info(f"[{agent_name}] 步骤 3: 云端模型进化")

            if dry_run:
                logger.info(f"[{agent_name}] [DRY RUN] 跳过进化")
            else:
                ok = run_evolution(today, agent_name, agent_ws, abstract_dir, logger)
                if not ok:
                    logger.error(f"[{agent_name}] 云端进化失败")
                    send_notification("OpenClaw 记忆整理", f"{agent_name} 云端进化失败", is_error=True)
                    # 其他 agent 失败不阻塞 main
                    if agent_name == "main":
                        return
                    continue

            send_notification("OpenClaw 记忆整理", f"{agent_name} 每日整理完成！")

        # ════════════════════════════════════════════
        # 完成
        # ════════════════════════════════════════════
        success = True
        logger.info("=" * 60)
        logger.info("每日记忆整理完成（所有 agent）")
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
    """运行 chunking 脚本，将 jsonl 转为会话 .md 文件（按 agent 分区）。"""
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


def run_evolution(date_str: str, agent_name: str, workspace_dir: Path,
                  abstract_dir: Path, logger) -> bool:
    """调用 LLM 进化指定 agent 的 USER/MEMORY/AGENTS。"""
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
        logger.error(f"[{agent_name}] workspace 文件缺失: {missing}")
        return False

    abstract_path = abstract_dir / f"{date_str}-abstracted.md"
    abstract = read_file_safe(abstract_path)
    if not abstract:
        logger.error(f"[{agent_name}] 摘要文件缺失: {abstract_path}")
        return False

    system_prompt = get_evolution_system_prompt("daily")

    user_prompt = (
        f"以下是 {agent_name} 的当前三个文档和今日会话摘要，请输出进化后的文档。\n\n"
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
        logger.error(f"[{agent_name}] LLM 调用失败: {err}")
        return False

    result = parse_deepseek_evolution(content)
    if result is None:
        logger.error(f"[{agent_name}] LLM 输出缺少完整分隔标记，终止写入")
        logger.debug(f"输出前500字符: {content[:500]}")
        return False

    files_to_write = [
        (workspace_dir / "USER.md", result["user"], "USER.md"),
        (workspace_dir / "MEMORY.md", result["memory"], "MEMORY.md"),
        (workspace_dir / "AGENTS.md", result["agents"], "AGENTS.md"),
    ]

    files_to_write = check_and_compact_files(files_to_write, system_prompt, logger)

    for path, new_content, name in files_to_write:
        old_content = read_file_safe(path) or ""
        bak = backup_file(path)
        logger.info(f"[{agent_name}] 备份 {name} → {bak}")
        log_diff(path, old_content, new_content, f"daily-{agent_name}", logger)
        atomic_write(path, new_content)
        logger.info(f"[{agent_name}] 写入 {name} ({len(new_content)} 字符)")

    logger.info(f"[{agent_name}] 云端进化完成")
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
