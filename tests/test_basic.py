"""
test_basic.py - 基础测试
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from store import store_memory
from recall import recall, related_to
from maintenance import get_stats, daily_decay
from memory_graph import get_graph, close_graph


def test_basic():
    print("🧪 运行基础测试...\n")
    
    graph = get_graph()
    
    # 测试 1: 存储简单记忆（第一人称，贴近真实聊天，而非第三人称小说体）
    print("测试 1: 存储记忆")
    event_id = store_memory("我喜欢喝咖啡")
    assert event_id is not None
    print("  ✓ 存储成功\n")
    
    # 测试 2: 存储多条记忆
    print("测试 2: 存储多条记忆")
    store_memory("我今天在星巴克喝了杯拿铁")
    store_memory("我朋友更习惯喝茶")
    store_memory("我觉得咖啡和茶都算日常饮品")
    print("  ✓ 存储 4 条记忆\n")
    
    # 测试 3: 统计
    print("测试 3: 查看统计")
    stats = get_stats()
    print(f"  节点数：{stats['nodes']}")
    print(f"  边数：{stats['edges']}")
    assert stats['nodes'] > 0
    assert stats['edges'] > 0
    print("  ✓ 统计正常\n")
    
    # 测试 4: 回想（用「我」等与说话人一致的线索，而不是旁人姓名）
    print("测试 4: 回想'我'")
    results = recall("我", top_k=5)
    print(f"  找到 {len(results)} 个相关节点")
    for name, activation in results:
        print(f"    - {name}: {activation:.2f}")
    assert len(results) > 0
    print("  ✓ 回想成功\n")
    
    # 测试 5: 关联查询
    print("测试 5: 查找与'咖啡'相关的节点")
    related = related_to("咖啡", depth=2)
    print(f"  找到 {len(related)} 个相关节点")
    for name, _ in related[:5]:
        print(f"    - {name}")
    print("  ✓ 关联查询成功\n")
    
    # 测试 6: 衰减
    print("测试 6: 日常衰减")
    daily_decay()
    stats_after = get_stats()
    print(f"  衰减后边数：{stats_after['edges']}")
    print("  ✓ 衰减完成\n")
    
    close_graph()
    
    print("=" * 40)
    print("✅ 所有测试通过！")
    print("=" * 40)


if __name__ == "__main__":
    test_basic()
