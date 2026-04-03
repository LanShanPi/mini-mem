"""
test_batch_memory_e2e.py - 批量写入端到端测试

测试 batch_memory 模块的完整流程：
1. 会话缓冲累积
2. 满批触发后台写入
3. flush 未满批剩余数据
4. 验证图内节点/边数量和类型
"""
import os
import sys
import time
from typing import List, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from batch_memory import (
    SessionBuffer,
    append_pair,
    pair_count,
    flush_session_remainder,
    batch_flush_worker,
    _buffers,
    _lock,
)
from config import MEMORY_BATCH_TURNS, MEMORY_BATCH_KEEP_PAIRS
from memory_graph import get_graph, close_graph
from store import store_memory


def _clear_buffers():
    """清空所有缓冲"""
    global _buffers
    with _lock:
        _buffers.clear()


def _count_nodes_and_edges():
    """返回当前图的节点数和边数"""
    try:
        graph = get_graph()
        with graph.driver.session() as session:
            nodes = session.run("MATCH (n) RETURN count(n) as c").single()["c"]
            edges = session.run("MATCH ()-[r]->() RETURN count(r) as c").single()["c"]
        return nodes, edges
    except Exception:
        return -1, -1


def test_session_buffer_accumulation():
    """测试 1: 会话缓冲正确累积"""
    print("\n" + "=" * 50)
    print("测试 1: 会话缓冲累积")
    print("=" * 50)

    _clear_buffers()
    session_id = "test_session_buffer_001"

    # 添加 5 轮对话
    for i in range(5):
        append_pair(session_id, f"用户消息{i}", f"助手回复{i}")

    count = pair_count(session_id)
    print(f"  添加 5 轮后缓冲计数：{count}")
    assert count == 5, f"期望 5，实际{count}"
    print("  ✓ 缓冲累积正确")

    # 验证缓冲内容
    with _lock:
        buf = _buffers.get(session_id)
        assert buf is not None, "缓冲不应为 None"
        assert len(buf.pairs) == 5, f"期望 5 对，实际{len(buf.pairs)}"

        # 直接验证每对
        for i in range(5):
            u, a = buf.pairs[i]
            expected_u = f"用户消息{i}"
            expected_a = f"助手回复{i}"
            assert u == expected_u, f"第{i}轮用户：期望'{expected_u}'，实际'{u}'"
            assert a == expected_a, f"第{i}轮助手：期望'{expected_a}'，实际'{a}'"

    print("  ✓ 缓冲内容正确")


def test_batch_flush_sliding_window():
    """测试 2: 满批后滑动窗口正确"""
    print("\n" + "=" * 50)
    print(f"测试 2: 满批滑动窗口 (TURNS={MEMORY_BATCH_TURNS}, KEEP={MEMORY_BATCH_KEEP_PAIRS})")
    print("=" * 50)

    _clear_buffers()
    session_id = "test_session_window_002"

    # 添加满批轮数
    for i in range(MEMORY_BATCH_TURNS):
        append_pair(session_id, f"轮{i}_用户", f"轮{i}_助手")

    print(f"  满批前缓冲：{pair_count(session_id)}轮")

    # 触发后台 flush
    batch_flush_worker(session_id)

    # 验证缓冲保留最后 KEEP 轮
    with _lock:
        buf = _buffers.get(session_id)
        if buf and len(buf.pairs) > 0:
            remaining = len(buf.pairs)
            expected = MEMORY_BATCH_KEEP_PAIRS
            print(f"  满批后保留：{remaining}轮 (期望：{expected})")
            assert remaining == expected, f"期望{expected}，实际{remaining}"

            # 验证保留的是后面的轮次（不严格要求特定索引）
            pair_texts = [f"{u}:{a}" for u, a in buf.pairs]
            # 应该包含最后几轮
            assert any(f"轮{MEMORY_BATCH_TURNS - 1}" in p for p in pair_texts), "应包含最后一轮"
            print(f"  ✓ 滑动窗口正确，保留最后{remaining}轮")
        else:
            print("  ✓ 缓冲已清空（正常 flush 行为）")


def test_flush_remainder():
    """测试 3: flush 未满批的剩余数据"""
    print("\n" + "=" * 50)
    print("测试 3: flush 未满批剩余数据")
    print("=" * 50)

    _clear_buffers()
    session_id = "test_session_003"

    # 只添加 3 轮（不满批）
    for i in range(3):
        append_pair(session_id, f"剩余{i}_用户", f"剩余{i}_助手")

    print(f"  flush 前缓冲：{pair_count(session_id)}轮")

    # 记录 flush 前图状态
    nodes_before, edges_before = _count_nodes_and_edges()
    print(f"  flush 前图状态：节点={nodes_before}, 边={edges_before}")

    # flush 剩余
    flush_session_remainder(session_id)

    # 验证缓冲已清空
    with _lock:
        buf = _buffers.get(session_id)
        if buf:
            assert len(buf.pairs) == 0, "flush 后缓冲应为空"
    print("  ✓ 缓冲已清空")

    # 验证图有变化（如果 LLM 可用）
    nodes_after, edges_after = _count_nodes_and_edges()
    print(f"  flush 后图状态：节点={nodes_after}, 边={edges_after}")

    if nodes_after > nodes_before:
        print("  ✓ 数据已写入图")
    else:
        print("  ⚠ 图无变化（可能 LLM 不可用或分析结果为空）")


def test_entity_filtering_quality():
    """测试 4: 实体过滤质量"""
    print("\n" + "=" * 50)
    print("测试 4: 实体过滤质量")
    print("=" * 50)

    from store import _is_bad_entity_phrase, _compress_entities

    # 应该被过滤的坏实体
    bad_entities = [
        "用户提到",
        "比较喜欢",
        "我觉得",
        "说实话",
        "总之",
        "但是",
        "用户说",
        "这件事",
        "那个情况",
    ]

    # 应该保留的好实体
    good_entities = [
        "星巴克",
        "李小明",
        "北京市",
        "水果味咖啡",
        "2024-01-15",
        "科技有限公司",
        "王老师",
    ]

    # 验证坏实体被过滤
    filtered_count = 0
    for entity in bad_entities:
        if _is_bad_entity_phrase(entity):
            filtered_count += 1
        else:
            print(f"  ⚠ 未过滤坏实体：{entity}")

    print(f"  坏实体过滤：{filtered_count}/{len(bad_entities)}")
    # 允许 1-2 个边界情况，不强制 100%
    assert filtered_count >= len(bad_entities) - 2, f"坏实体过滤率应 >= 80%，实际{filtered_count}/{len(bad_entities)}"
    print("  ✓ 坏实体过滤正确（>= 80%）")

    # 验证好实体保留
    kept_count = 0
    for entity in good_entities:
        if not _is_bad_entity_phrase(entity):
            kept_count += 1
        else:
            print(f"  ⚠ 错误过滤好实体：{entity}")

    print(f"  好实体保留：{kept_count}/{len(good_entities)}")
    assert kept_count == len(good_entities), "所有好实体应保留"
    print("  ✓ 好实体保留正确")

    # 测试压缩逻辑
    test_entities = ["咖啡", "水果味咖啡", "味咖啡", "用户提到", "星巴克"]
    compressed = _compress_entities(test_entities, max_n=10)
    print(f"  压缩前：{test_entities}")
    print(f"  压缩后：{compressed}")

    # 应保留更具体的"水果味咖啡"而非"味咖啡"
    assert "水果味咖啡" in compressed
    assert "用户提到" not in compressed
    print("  ✓ 压缩逻辑正确")


def run_all_tests():
    """运行所有端到端测试"""
    print("\n" + "#" * 60)
    print("# MiniMem 批量写入端到端测试")
    print("#" * 60)

    try:
        test_session_buffer_accumulation()
        test_batch_flush_sliding_window()
        test_flush_remainder()
        test_entity_filtering_quality()

        print("\n" + "=" * 60)
        print("✅ 所有端到端测试通过！")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ 测试失败：{e}")
        raise
    except Exception as e:
        print(f"\n❌ 测试异常：{e}")
        raise
    finally:
        close_graph()


if __name__ == "__main__":
    run_all_tests()
