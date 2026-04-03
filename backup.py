"""
backup.py - 记忆数据自动备份

功能：
1. 定期将 Neo4j 数据导出为 JSON 备份文件
2. 自动清理超过保留天数的旧备份
3. 后台线程运行，不阻塞主服务
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import (
    MEMORY_AUTO_BACKUP_DIR,
    MEMORY_AUTO_BACKUP_ENABLED,
    MEMORY_AUTO_BACKUP_INTERVAL_HOURS,
    MEMORY_AUTO_BACKUP_KEEP_DAYS,
    PROJECT_ROOT,
)
from memory_graph import get_graph

logger = logging.getLogger(__name__)


def _get_backup_dir() -> Path:
    """获取备份目录，不存在则创建"""
    backup_dir = Path(PROJECT_ROOT) / MEMORY_AUTO_BACKUP_DIR
    if not backup_dir.exists():
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            # 添加到 .gitignore
            gitignore_file = Path(PROJECT_ROOT) / ".gitignore"
            if gitignore_file.exists():
                content = gitignore_file.read_text(encoding="utf-8")
                if MEMORY_AUTO_BACKUP_DIR not in content:
                    with open(gitignore_file, "a", encoding="utf-8") as f:
                        f.write(f"\n# 备份文件\n{MEMORY_AUTO_BACKUP_DIR}/\n")
            else:
                gitignore_file.write_text(f"{MEMORY_AUTO_BACKUP_DIR}/\n", encoding="utf-8")
        except OSError as e:
            logger.warning("无法创建备份目录：%s", e)
    return backup_dir


def _cleanup_old_backups(backup_dir: Path, keep_days: int) -> int:
    """
    清理超过保留天数的旧备份文件

    Returns:
        删除的文件数量
    """
    if keep_days <= 0:
        return 0

    deleted_count = 0
    now = time.time()
    cutoff = now - (keep_days * 24 * 60 * 60)

    try:
        for f in backup_dir.glob("*.json"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                deleted_count += 1
                logger.info("清理过期备份：%s", f.name)
    except OSError as e:
        logger.warning("清理旧备份失败：%s", e)

    return deleted_count


def run_backup() -> dict:
    """
    执行一次备份任务

    Returns:
        备份结果信息
    """
    if not MEMORY_AUTO_BACKUP_ENABLED:
        return {"status": "disabled", "reason": "自动备份已禁用"}

    try:
        backup_dir = _get_backup_dir()
        graph = get_graph()

        # 导出当前数据
        data = graph.export_all()

        # 生成带时间戳的文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stats = graph.stats()
        filename = f"backup_{timestamp}_nodes{stats['nodes']}_edges{stats['edges']}.json"
        filepath = backup_dir / filename

        # 写入备份文件
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("备份完成：%s (%d 节点，%d 边)", filename, stats["nodes"], stats["edges"])

        # 清理旧备份
        cleanup_count = _cleanup_old_backups(backup_dir, MEMORY_AUTO_BACKUP_KEEP_DAYS)

        return {
            "status": "success",
            "file": str(filepath),
            "nodes": stats["nodes"],
            "edges": stats["edges"],
            "cleaned_up": cleanup_count,
        }

    except Exception as e:
        logger.exception("自动备份失败：%s", e)
        return {
            "status": "error",
            "error": str(e),
        }


def _backup_worker() -> None:
    """后台备份线程：每隔 N 小时运行一次"""
    interval_seconds = MEMORY_AUTO_BACKUP_INTERVAL_HOURS * 60 * 60

    # 等待 5 分钟后再开始第一次备份，让服务先稳定运行
    time.sleep(300)

    while True:
        try:
            run_backup()
        except Exception as e:
            logger.exception("备份线程异常：%s", e)

        time.sleep(interval_seconds)


def start_backup_service() -> Optional[threading.Thread]:
    """
    启动备份服务

    Returns:
        后台线程对象，如果禁用则返回 None
    """
    if not MEMORY_AUTO_BACKUP_ENABLED:
        logger.info("自动备份服务已禁用")
        return None

    thread = threading.Thread(target=_backup_worker, daemon=True, name="backup-worker")
    thread.start()
    logger.info(
        "自动备份服务已启动：每 %d 小时备份一次，保留 %d 天",
        MEMORY_AUTO_BACKUP_INTERVAL_HOURS,
        MEMORY_AUTO_BACKUP_KEEP_DAYS,
    )
    return thread
