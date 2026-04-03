"""
demo_zhiri_coffee.py - 示例脚本

演示如何用 mini_mem 存储和回想记忆网络。
"""
from store import store_memory, store_event
from recall import recall, recall_detailed, related_to
from maintenance import daily_decay, get_stats, cleanup_isolated_nodes
from memory_graph import get_graph, close_graph


def main():
    print("=" * 60)
    print("🧠 MiniMem - 示例演示")
    print("=" * 60)
    
    graph = get_graph()
    
    # 可选：先清空（调试用）
    # graph.clear_all()
    
    print("\n📝 步骤 1: 存储 30 天的咖啡记忆\n")
    
    # 模拟记忆数据
    memories = [
        # 第 1 天
        ("2026-02-12", "张三在星巴克喝了美式，说今天好累"),
        # 第 3 天
        ("2026-02-14", "张三在咖啡馆喝拿铁，心情很好，因为是情人节"),
        # 第 5 天
        ("2026-02-16", "张三在办公室喝速溶咖啡，加班到深夜"),
        # 第 7 天
        ("2026-02-18", "张三和朋友聊咖啡，说喜欢手冲"),
        # 第 10 天
        ("2026-02-21", "张三在家自己冲咖啡，买了新的咖啡豆"),
        # 第 15 天
        ("2026-02-26", "张三和李四聊咖啡，说拿铁是她的最爱"),
        # 第 20 天
        ("2026-03-03", "张三在星巴克尝试了新品，太甜了"),
        # 第 25 天
        ("2026-03-08", "张三加班，喝了 3 杯美式"),
        # 第 30 天
        ("2026-03-13", "张三说最近咖啡喝太多了，要戒咖啡"),
    ]
    
    for date, text in memories:
        store_memory(text)
    
    print("\n" + "=" * 60)
    print("📊 步骤 2: 查看网络统计")
    print("=" * 60)
    
    stats = get_stats()
    print(f"\n节点数：{stats['nodes']}")
    print(f"边数：{stats['edges']}")
    print(f"平均权重：{stats['avg_weight']}")
    print(f"最强连接：{stats['strongest_connection']}")
    print(f"最弱连接：{stats['weakest_connection']}")
    
    print("\n" + "=" * 60)
    print("🔍 步骤 3: 回想测试")
    print("=" * 60)
    
    # 测试 1: 简单关键词
    print("\n🔹 测试 1: 回想'张三 咖啡'")
    results = recall("张三 咖啡", top_k=10)
    for name, activation in results:
        bar = "█" * int(activation * 20)
        print(f"  {name:20} {bar} {activation:.2f}")
    
    # 测试 2: 只回想"咖啡"
    print("\n🔹 测试 2: 回想'咖啡'")
    results = recall("咖啡", top_k=10)
    for name, activation in results:
        bar = "█" * int(activation * 20)
        print(f"  {name:20} {bar} {activation:.2f}")
    
    # 测试 3: 回想"拿铁"
    print("\n🔹 测试 3: 回想'拿铁'")
    results = recall("拿铁", top_k=10)
    for name, activation in results:
        bar = "█" * int(activation * 20)
        print(f"  {name:20} {bar} {activation:.2f}")
    
    # 测试 4: 详细回想
    print("\n🔹 测试 4: 详细回想'张三'")
    detailed = recall_detailed("张三")
    print(f"  总结：{detailed['summary']}")
    
    # 测试 5: 查找相关节点
    print("\n🔹 测试 5: 查找与'咖啡'相关的所有节点")
    related = related_to("咖啡", depth=2)
    for name, activation in related[:10]:
        bar = "█" * int(activation * 20)
        print(f"  {name:20} {bar} {activation:.2f}")
    
    print("\n" + "=" * 60)
    print("🧹 步骤 4: 日常维护（模拟 30 天后）")
    print("=" * 60)
    
    # 模拟 30 天的衰减
    for i in range(30):
        daily_decay()
    
    print("\n30 天衰减后的统计：")
    stats_after = get_stats()
    print(f"  节点数：{stats_after['nodes']}")
    print(f"  边数：{stats_after['edges']}")
    print(f"  平均权重：{stats_after['avg_weight']}")
    
    # 清理孤立节点
    cleanup_isolated_nodes()
    
    print("\n" + "=" * 60)
    print("🔍 步骤 5: 衰减后再回想")
    print("=" * 60)
    
    print("\n回想'咖啡'（30 天后）：")
    results_after = recall("咖啡", top_k=10)
    for name, activation in results_after:
        bar = "█" * int(activation * 20) if activation > 0.1 else "·"
        print(f"  {name:20} {bar} {activation:.2f}")
    
    # 对比
    print("\n📈 对比：")
    print(f"  衰减前节点数：{stats['nodes']} → 衰减后：{stats_after['nodes']}")
    print(f"  衰减前边数：{stats['edges']} → 衰减后：{stats_after['edges']}")
    
    close_graph()
    
    print("\n" + "=" * 60)
    print("✅ 示例完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
