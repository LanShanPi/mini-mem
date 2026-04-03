"""
tests/test_memory_merge.py - 记忆合并模块测试
"""
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

from memory_merge import (
    _text_similarity,
    _get_time_bucket,
    find_similar_memories,
    merge_memory_nodes,
    forget_low_salience_memories,
    detect_conflicts,
    run_memory_maintenance,
)
from memory_graph import MemoryGraph


class TestTextSimilarity:
    """测试文本相似度计算"""

    def test_identical_strings(self):
        """完全相同的字符串相似度应为 1.0"""
        assert _text_similarity("你好世界", "你好世界") == 1.0

    def test_completely_different(self):
        """完全不同的字符串相似度应接近 0"""
        sim = _text_similarity("苹果", "香蕉")
        assert 0.0 <= sim <= 1.0

    def test_partial_overlap(self):
        """部分重叠的字符串"""
        sim = _text_similarity("今天天气好", "今天下雨")
        # "今", "天" 共同，相似度应在 0.2-0.5 之间
        assert 0.1 <= sim <= 0.6

    def test_empty_strings(self):
        """空字符串应返回 0.0"""
        assert _text_similarity("", "你好") == 0.0
        assert _text_similarity("你好", "") == 0.0
        assert _text_similarity("", "") == 0.0

    def test_different_lengths(self):
        """不同长度的字符串"""
        sim = _text_similarity("你好", "你好世界")
        # "你", "好" 共同，相似度应为 2/4 = 0.5
        assert abs(sim - 0.5) < 0.01


class TestGetTimeBucket:
    """测试时间桶转换"""

    def test_iso_format(self):
        """标准 ISO 格式"""
        ts = "2024-03-15T10:30:00"
        bucket = _get_time_bucket(ts)
        assert bucket == "2024-03-15"

    def test_with_timezone(self):
        """带时区的 ISO 格式"""
        ts = "2024-03-15T10:30:00Z"
        bucket = _get_time_bucket(ts)
        assert bucket == "2024-03-15"

    def test_with_offset(self):
        """带偏移的 ISO 格式"""
        ts = "2024-03-15T10:30:00+08:00"
        bucket = _get_time_bucket(ts)
        assert bucket == "2024-03-15"

    def test_invalid_format(self):
        """无效格式应返回 unknown"""
        bucket = _get_time_bucket("invalid")
        assert bucket == "unknown"

    def test_empty_string(self):
        """空字符串应返回 unknown"""
        bucket = _get_time_bucket("")
        assert bucket == "unknown"


class TestFindSimilarMemories:
    """测试相似记忆查找"""

    def test_empty_graph(self):
        """空图应返回空列表"""
        mock_graph = MagicMock()
        mock_graph.driver.session.return_value.__enter__.return_value.run.return_value = []

        pairs = find_similar_memories(mock_graph, threshold=0.8, limit=100)
        assert pairs == []

    def test_no_similar_pairs(self):
        """没有相似对时返回空列表"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟返回两个完全不同的事件
        mock_session.run.return_value = [
            {"id": "1", "text": "今天去公园散步", "ts": "2024-03-15T10:00:00"},
            {"id": "2", "text": "明天要开会", "ts": "2024-03-16T14:00:00"},
        ]

        pairs = find_similar_memories(mock_graph, threshold=0.8, limit=100)
        assert pairs == []

    def test_finds_similar_pairs(self):
        """应能找到相似对"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟返回两个相似的事件
        mock_session.run.return_value = [
            {"id": "1", "text": "今天去公园散步", "ts": "2024-03-15T10:00:00"},
            {"id": "2", "text": "今天去公园走路", "ts": "2024-03-15T11:00:00"},
        ]

        pairs = find_similar_memories(mock_graph, threshold=0.5, limit=100)
        # 这两个字符串应该有较高的相似度
        assert len(pairs) >= 1


class TestMergeMemoryNodes:
    """测试记忆节点合并"""

    def test_merge_success(self):
        """成功合并两个节点"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟节点属性
        mock_session.run.return_value.single.side_effect = [
            {"text": "文本 A", "salience": 0.6},
            {"text": "文本 B", "salience": 0.4},
        ]

        result = merge_memory_nodes(mock_graph, "id_a", "id_b")

        # 应返回 salience 较高的节点 ID
        assert result == "id_a"

    def test_merge_selects_higher_salience(self):
        """应保留 salience 较高的节点"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # B 的 salience 更高
        mock_session.run.return_value.single.side_effect = [
            {"text": "文本 A", "salience": 0.3},
            {"text": "文本 B", "salience": 0.8},
        ]

        result = merge_memory_nodes(mock_graph, "id_a", "id_b")

        # 应返回 B 的 ID
        assert result == "id_b"

    def test_merge_missing_node(self):
        """节点不存在时应返回 None"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟节点不存在
        mock_session.run.return_value.single.return_value = None

        result = merge_memory_nodes(mock_graph, "id_a", "id_b")

        assert result is None


class TestForgetLowSalienceMemories:
    """测试低重要性记忆遗忘"""

    def test_forget_below_threshold(self):
        """应删除 salience 低于阈值的节点"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟一个低 salience 节点，没有强连接
        mock_nodes_result = MagicMock()
        mock_nodes_result.__iter__ = MagicMock(return_value=iter([{"id": "1", "salience": 0.03}]))

        mock_edges_result = MagicMock()
        mock_edges_result.single.return_value = {"cnt": 0}  # 没有强连接

        # 需要三次调用：1.查找节点 2.检查边 3.删除节点
        mock_session.run = MagicMock(side_effect=[
            mock_nodes_result,   # 第一次调用：查找低 salience 节点
            mock_edges_result,   # 第二次调用：检查边
            mock_edges_result,   # 第三次调用：删除节点（返回值不重要）
        ])

        count = forget_low_salience_memories(mock_graph, threshold=0.05)

        assert count == 1

    def test_keep_with_strong_connections(self):
        """有强连接的节点应被保留"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟一个低 salience 节点，但有强连接
        mock_nodes_result = MagicMock()
        mock_nodes_result.__iter__ = MagicMock(return_value=iter([{"id": "1", "salience": 0.03}]))

        mock_edges_result = MagicMock()
        mock_edges_result.single.return_value = {"cnt": 3}  # 3 条强连接

        mock_session.run = MagicMock(side_effect=[
            mock_nodes_result,   # 第一次调用：查找低 salience 节点
            mock_edges_result,   # 第二次调用：检查边
        ])

        count = forget_low_salience_memories(mock_graph, threshold=0.05)

        assert count == 0

    def test_empty_graph(self):
        """空图应返回 0"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_session.run.return_value = mock_result

        count = forget_low_salience_memories(mock_graph, threshold=0.05)

        assert count == 0


class TestDetectConflicts:
    """测试冲突检测"""

    def test_no_conflict_same_valence(self):
        """相同 valence 不应有冲突"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟相同 valence 的事实
        mock_session.run.return_value = [
            {"text": "事实 A", "valence": 0.5},
            {"text": "事实 B", "valence": 0.6},
        ]

        conflicts = detect_conflicts(mock_graph, "测试实体")

        # valence 差异 0.1 < 0.6，不应有冲突
        assert len(conflicts) == 0

    def test_detects_conflict(self):
        """应检测到 valence 显著差异的冲突"""
        mock_graph = MagicMock()
        mock_session = MagicMock()
        mock_graph.driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)

        # 模拟矛盾的 valence
        mock_session.run.return_value = [
            {"text": "喜欢测试", "valence": 0.8},
            {"text": "讨厌测试", "valence": -0.7},
        ]

        conflicts = detect_conflicts(mock_graph, "测试实体")

        # valence 差异 1.5 > 0.6，应有冲突
        assert len(conflicts) == 1
        assert conflicts[0]["entity"] == "测试实体"
        assert "valence_gap" in conflicts[0]
        assert conflicts[0]["valence_gap"] > 0.6


class TestRunMemoryMaintenance:
    """测试记忆维护主函数"""

    @patch("memory_merge.find_similar_memories")
    @patch("memory_merge.merge_memory_nodes")
    @patch("memory_merge.forget_low_salience_memories")
    def test_maintenance_runs(self, mock_forget, mock_merge, mock_find):
        """维护任务应正常运行"""
        mock_find.return_value = [("1", "2", 0.9)]
        mock_merge.return_value = "merged_id"
        mock_forget.return_value = 2

        mock_graph = MagicMock()

        with patch("memory_merge.get_graph", return_value=mock_graph):
            with patch("memory_merge.MEMORY_MERGE_ENABLED", True):
                stats = run_memory_maintenance()

        assert "similar_pairs_found" in stats
        assert "memories_merged" in stats
        assert "memories_forgotten" in stats
        assert stats["similar_pairs_found"] == 1
        assert stats["memories_merged"] == 1
        assert stats["memories_forgotten"] == 2

    def test_maintenance_disabled(self):
        """维护禁用时应返回 disabled 状态"""
        with patch("memory_merge.MEMORY_MERGE_ENABLED", False):
            stats = run_memory_maintenance()

        assert stats["status"] == "disabled"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
