# MiniMem - 核心函数调用指南

## 📦 核心模块

```
mini_mem/
├── store.py          # 存储记忆
├── recall.py         # 查询/回想记忆
├── memory_graph.py   # 图数据库操作
├── config.py         # 配置
└── maintenance.py    # 维护工具
```

---

## 1️⃣ 存储记忆

### 方式 A: 简单存储（推荐）

```python
from store import store_memory

# 存储一段记忆
store_memory("志理今天喝了咖啡")
store_memory("蓝山在办公室写代码")
```

### 方式 B: 结构化事件

```python
from store import store_event

# 存储事件（参与者 + 概念）
store_event(
    participants=["志理", "蓝山"],
    concepts=["咖啡", "聊天"],
    timestamp="2026-03-13",
    emotion=0.8
)
```

### 方式 C: 指定图实例

```python
from store import store_memory
from memory_graph import get_graph

graph = get_graph()
store_memory("志理喝咖啡", graph=graph)
```

---

## 2️⃣ 查询/回想记忆

### 方式 A: 激活扩散回想（推荐）

```python
from recall import recall

# 回想相关记忆
results = recall("咖啡", top_k=10)

# 输出：[(节点名，相关度), ...]
for name, score in results:
    print(f"{name}: {score:.2f}")
```

### 方式 B: 详细回想

```python
from recall import recall_detailed

results = recall_detailed("志理", top_k=5)

for node, score, path in results:
    print(f"节点：{node['name']}")
    print(f"相关度：{score:.2f}")
    print(f"路径：{path}")
```

### 方式 C: 查找相关节点

```python
from recall import related_to

# 查找与"志理"直接相连的节点
neighbors = related_to("志理")

for name, weight in neighbors:
    print(f"{name} (权重：{weight:.2f})")
```

### 方式 D: 关键词搜索

```python
from memory_graph import get_graph

graph = get_graph()

# 搜索包含关键词的节点
results = graph.search_nodes("咖啡", limit=10)

for node in results:
    print(node['name'])
```

---

## 3️⃣ 完整示例

```python
from store import store_memory
from recall import recall
from memory_graph import get_graph

# 获取图实例
graph = get_graph()

# 存储记忆
store_memory("志理在星巴克喝了美式")
store_memory("蓝山喜欢喝拿铁")
store_memory("志理和蓝山聊咖啡")

# 回想"咖啡"相关的内容
results = recall("咖啡", top_k=5)
print(f"找到 {len(results)} 条相关记忆:")
for name, score in results:
    print(f"  - {name} (相关度：{score:.2f})")

# 输出:
# 找到 5 条相关记忆:
#   - 咖啡 (相关度：1.00)
#   - 志理和蓝山聊咖啡 (相关度：0.90)
#   - 蓝山喜欢喝拿铁 (相关度：0.75)
#   - 志理在星巴克喝了美式 (相关度：0.70)
#   - 星巴克 (相关度：0.60)
```

---

## 4️⃣ 维护操作

```python
from maintenance import daily_decay, get_stats, cleanup_isolated_nodes

# 日常衰减（每天调用一次）
daily_decay()

# 查看统计
stats = get_stats()
print(f"节点：{stats['nodes']}, 边：{stats['edges']}")

# 清理孤立节点
cleanup_isolated_nodes()
```

---

## 5️⃣ 直接使用 Neo4j 查询

```python
from memory_graph import get_graph

graph = get_graph()

# 执行 Cypher 查询
result = graph.driver.session().run("""
    MATCH (n) WHERE n.name CONTAINS "咖啡"
    RETURN n.name as name, n.created_at as time
    ORDER BY time DESC
    LIMIT 10
""")

for record in result:
    print(f"{record['name']} - {record['time']}")
```

---

## ⚡ 快速开始

```python
# 最简单的用法
from store import store_memory
from recall import recall

# 存
store_memory("今天天气不错")

# 取
results = recall("天气")
print(results)
```

---

## 📝 注意事项

1. **首次使用** 需要确保 Neo4j 正在运行
2. **配置** 在 `.env` 文件中设置 Neo4j 连接信息
3. **图实例** 可以不传 `graph` 参数，会自动获取默认实例
4. **关闭连接** 程序结束时调用 `from memory_graph import close_graph; close_graph()`
