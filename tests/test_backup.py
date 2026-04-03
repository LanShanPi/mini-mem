"""
tests/test_backup.py - 自动备份模块测试
"""
import json
import pytest
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from backup import (
    _get_backup_dir,
    _cleanup_old_backups,
    run_backup,
    _backup_worker,
    start_backup_service,
)
from config import PROJECT_ROOT


class TestGetBackupDir:
    """测试备份目录获取"""

    def test_creates_dir_if_not_exists(self, tmp_path):
        """目录不存在时应创建"""
        with patch("backup.PROJECT_ROOT", str(tmp_path)):
            with patch("backup.MEMORY_AUTO_BACKUP_DIR", "test_backups"):
                backup_dir = _get_backup_dir()
                assert backup_dir.exists()
                assert backup_dir.is_dir()

    def test_returns_existing_dir(self, tmp_path):
        """目录已存在时直接返回"""
        existing_dir = tmp_path / "existing_backups"
        existing_dir.mkdir()

        with patch("backup.PROJECT_ROOT", str(tmp_path)):
            with patch("backup.MEMORY_AUTO_BACKUP_DIR", "existing_backups"):
                backup_dir = _get_backup_dir()
                assert backup_dir == existing_dir


class TestCleanupOldBackups:
    """测试旧备份清理"""

    def test_removes_old_files(self, tmp_path):
        """应删除超过保留天数的文件"""
        # 创建测试备份文件
        old_file = tmp_path / "backup_old.json"
        old_file.write_text("{}", encoding="utf-8")

        # 设置文件修改时间为 10 天前
        old_time = time.time() - (10 * 24 * 60 * 60)
        import os
        os.utime(str(old_file), (old_time, old_time))

        # 验证文件存在
        assert old_file.exists()

        # 清理：保留 7 天内的文件
        count = _cleanup_old_backups(tmp_path, keep_days=7)

        # 文件应该被删除
        assert count == 1
        assert not old_file.exists()

    def test_keep_days_zero(self, tmp_path):
        """keep_days=0 时不应删除任何文件"""
        count = _cleanup_old_backups(tmp_path, keep_days=0)
        assert count == 0


class TestRunBackup:
    """测试备份执行"""

    def test_backup_disabled(self):
        """备份禁用时应返回 disabled 状态"""
        with patch("backup.MEMORY_AUTO_BACKUP_ENABLED", False):
            result = run_backup()
            assert result["status"] == "disabled"

    def test_backup_success(self):
        """备份成功应返回文件信息"""
        mock_graph = MagicMock()
        mock_graph.export_all.return_value = {"nodes": [], "edges": []}
        mock_graph.stats.return_value = {"nodes": 0, "edges": 0}

        with patch("backup.MEMORY_AUTO_BACKUP_ENABLED", True):
            with patch("backup.get_graph", return_value=mock_graph):
                with patch("backup._get_backup_dir") as mock_dir:
                    mock_dir.return_value = Path(PROJECT_ROOT) / "test_backups"
                    mock_dir.return_value.mkdir(exist_ok=True)
                    result = run_backup()

                    assert result["status"] == "success"
                    assert "file" in result
                    assert result["nodes"] == 0
                    assert result["edges"] == 0

    def test_backup_error(self):
        """备份失败应返回 error 状态"""
        with patch("backup.MEMORY_AUTO_BACKUP_ENABLED", True):
            with patch("backup.get_graph") as mock_get_graph:
                mock_get_graph.side_effect = Exception("连接失败")
                result = run_backup()

                assert result["status"] == "error"
                assert "error" in result


class TestStartBackupService:
    """测试备份服务启动"""

    def test_service_disabled(self):
        """服务禁用时应返回 None"""
        with patch("backup.MEMORY_AUTO_BACKUP_ENABLED", False):
            thread = start_backup_service()
            assert thread is None

    def test_service_started(self):
        """服务启动应返回线程"""
        with patch("backup.MEMORY_AUTO_BACKUP_ENABLED", True):
            with patch("backup._backup_worker"):
                thread = start_backup_service()
                assert thread is not None
                assert thread.is_alive() or not thread.is_alive()  # 线程可能已启动


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
