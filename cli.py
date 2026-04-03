#!/usr/bin/env python3
"""
MiniMem 交互式命令行工具
"""
import sys
sys.path.insert(0, '.')

from store import store_memory
from recall import recall, related_to
from maintenance import get_stats
from memory_graph import get_graph


def cmd_store(args):
    if not args:
        print("❌ 用法：存储 <记忆内容>")
        return
    text = " ".join(args)
    store_memory(text)
    print(f"✅ 已存储：{text}")


def cmd_recall(args):
    if not args:
        print("❌ 用法：回想 <关键词>")
        return
    
    query = " ".join(args)
    print(f"\n🔍 回想：{query}")
    print("-" * 40)
    
    results = recall(query, top_k=10)
    
    if results:
        for i, (name, score) in enumerate(results, 1):
            bar_len = int(score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            print(f"  {i}. {name[:50]:<50} {bar} {score:.2f}")
    else:
        print("  (没有找到相关记忆)")
    print()


def cmd_related(args):
    if not args:
        print("❌ 用法：相关 <节点名称>")
        return
    
    node_name = " ".join(args)
    print(f"\n🔗 查找与 \"{node_name}\" 相关的节点:")
    print("-" * 40)
    
    results = related_to(node_name)
    
    if results:
        for neighbor, weight in results[:10]:
            print(f"  - {neighbor} (权重：{weight:.2f})")
    else:
        print("  (没有找到相关节点)")
    print()


def cmd_stats(args):
    stats = get_stats()
    print("\n📊 记忆网络统计")
    print("=" * 40)
    print(f"  节点总数：{stats['nodes']}")
    print(f"  关系总数：{stats['edges']}")
    if 'avg_weight' in stats and stats['avg_weight'] is not None:
        print(f"  平均权重：{stats['avg_weight']:.2f}")
    print()


def cmd_help(args):
    print("""
📖 MiniMem 命令帮助

存储类:
  存储 <内容>              存储一段记忆

查询类:
  回想 <关键词>            回想相关记忆
  相关 <节点名>            查找相关节点
  统计                     查看网络统计

其他:
  帮助                     显示这个帮助
  退出                     退出程序
""")


COMMANDS = {
    "存储": cmd_store,
    "存": cmd_store,
    "回想": cmd_recall,
    "想": cmd_recall,
    "相关": cmd_related,
    "关": cmd_related,
    "统计": cmd_stats,
    "计": cmd_stats,
    "帮助": cmd_help,
    "help": cmd_help,
    "退出": lambda _: sys.exit(0),
    "quit": lambda _: sys.exit(0),
    "exit": lambda _: sys.exit(0),
}


def main():
    print("=" * 60)
    print("🧠 MiniMem 交互式命令行（方案 C 优化版）")
    print("=" * 60)
    print("输入 \"帮助\" 查看可用命令")
    print()
    
    graph = get_graph()
    print("✅ 已连接到 Neo4j")
    print()
    
    while True:
        try:
            line = input("> ").strip()
            if not line:
                continue
            
            parts = line.split(maxsplit=1)
            cmd = parts[0]
            args = parts[1].split() if len(parts) > 1 else []
            
            if cmd in COMMANDS:
                COMMANDS[cmd](args)
            else:
                print(f"❌ 未知命令：{cmd}")
                print("输入 \"帮助\" 查看可用命令")
                
        except KeyboardInterrupt:
            print("\n\n👋 再见！")
            sys.exit(0)
        except Exception as e:
            print(f"❌ 错误：{e}")


if __name__ == "__main__":
    main()
