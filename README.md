# MiniMem - 类人记忆网络

基于 Neo4j 的简化记忆存储系统，模拟人脑的记忆机制：**关联 + 权重 + 衰减**。

> 🚀 **现在支持 Docker 一键启动！**

---

## 核心理念

> 记忆 = 节点 + 关联 + 权重 + 衰减

- **节点**：任何概念、事件、人、时间...
- **关联**：节点之间的连线（无向边）
- **权重**：连接的强度（一起激活的次数/频率）
- **衰减**：不用的连接会自动变弱直至消失

---

## 🚀 快速开始

### 方式一：Docker 一键启动（推荐）

```bash
# 1. 配置环境变量
cp .env.example .env 2>/dev/null || true
# 编辑 .env 文件，设置 LLM_API_KEY 等配置

# 2. 一键启动所有服务
docker-compose up -d

# 3. 查看日志
docker-compose logs -f

# 4. 停止服务
docker-compose down
```

**访问地址**：
- Web 界面：http://localhost:8765
- Neo4j Browser：http://localhost:7474（用户名 `neo4j` / 密码 `minimem123`）

### 方式二：本地运行

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量
# 编辑 .env 文件

# 3. 启动 Neo4j
./start_neo4j.sh

# 4. 启动 Web 服务
python -m uvicorn web_server:app --host 127.0.0.1 --port 8765

# 5. 或使用命令行
python cli.py
```

---

## 📦 项目结构

```
mini_mem/
├── README.md              # 本文件
├── DOCUMENTATION.md       # 详细设计文档（推荐）
├── USAGE.md               # 使用指南
├── docker-compose.yml     # Docker 编排
├── Dockerfile             # Web 服务镜像
├── requirements.txt       # Python 依赖
├── config.py              # 配置中心
├── memory_graph.py        # Neo4j 图数据库操作
├── memory_text.py         # 文本处理、实体类型推断
├── store.py               # 记忆存储接口
├── recall.py              # 记忆检索接口
├── backup.py              # 自动备份
├── maintenance.py         # 日常维护（衰减、清理）
├── cli.py                 # 交互式命令行
├── web_server.py          # Web API 服务
├── chat.py                # 对话生成
├── batch_memory.py        # 批量写入优化
└── examples/
    └── demo_zhiri_coffee.py  # 示例脚本
```

---

## 💻 交互式命令行

```bash
python cli.py
```

### 可用命令

| 命令 | 功能 | 示例 |
|------|------|------|
| `存储 <内容>` | 存储记忆 | `存储 志理今天喝了咖啡` |
| `回想 <关键词>` | 回想相关记忆 | `回想 咖啡` |
| `相关 <节点名>` | 查找相关节点 | `相关 志理` |
| `统计` | 查看网络统计 | `统计` |
| `帮助` | 显示帮助 | `帮助` |
| `退出` | 退出程序 | `退出` |

### 使用示例

```
🧠 MiniMem 交互式命令行

> 存储 志理在办公室写代码
✅ 已存储

> 回想 咖啡
  1. 咖啡                                                 ████████████████████ 1.00
  2. 志理                                                 ███████████░░░░░░░░░ 0.57
  3. 星巴克                                               ██████████░░░░░░░░░░ 0.47

> 统计
  节点总数：92
  关系总数：204
  平均权重：0.59
```

---

## 🔌 核心 API

### 存储记忆

```python
from store import store_memory

# 存储一段记忆
store_memory("志理在星巴克喝了美式，说今天好累")
```

### 回想记忆

```python
from recall import recall

# 根据关键词回想
results = recall("志理 咖啡", top_k=10)
for node_name, activation in results:
    print(f"{node_name}: {activation:.2f}")
```

### 查找相关节点

```python
from recall import related_to

# 查找与"志理"相关的节点
neighbors = related_to("志理", depth=2)
for name, weight in neighbors:
    print(f"{name}: {weight:.2f}")
```

### 日常维护

```python
from maintenance import daily_decay, get_stats

# 每天运行一次，让不用的连接自然衰减
daily_decay()

# 查看统计
stats = get_stats()
print(f"节点：{stats['nodes']}, 边：{stats['edges']}")
```

---

## 🧠 召回算法：方案 C（混合优化）

### 核心思想

- **Neo4j** 负责图遍历（Cypher 递归查询）
- **Python** 负责激活计算（应用层衰减）

### Cypher 查询

```cypher
MATCH path = (start)-[r*1..3]-(target)
WHERE start.id = $start_id
WITH target, 
     reduce(weight = 1.0, r IN relationships(path) | weight * r.weight) AS path_weight,
     length(path) as hop_count
RETURN target.id, target.name, path_weight, hop_count
```

### 应用层计算

```python
# 激活强度 = 路径权重 × 衰减系数^跳数
activation = path_weight * (0.7 ** hop_count)
```

---

## 📖 设计原则

1. **大道至简**：只有节点和边，没有复杂分类
2. **共现即相连**：一起出现的事物自动建立关联
3. **一起激活就变强**：常用的连接权重增加
4. **不用就衰减**：不用的连接权重自动降低
5. **激活扩散**：回想时从线索节点出发，激活在网络中流动

---

## 🔧 配置说明

### 环境变量（.env）

```bash
# Neo4j 配置
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=minimem123

# LLM 配置（可选，用于实体提取）
LLM_API_BASE=https://llm-api.talkweb.com.cn/v1
LLM_API_KEY=your-api-key
LLM_MODEL=tw/gpu/qwen3.5-397b-a17b
ENTITY_EXTRACTOR=llm  # simple | llm | hybrid

# 记忆批量写入
MEMORY_BATCH_ENABLED=true
MEMORY_BATCH_TURNS=10
MEMORY_BATCH_KEEP_PAIRS=5

# 自动备份
MEMORY_AUTO_BACKUP_ENABLED=true
MEMORY_AUTO_BACKUP_INTERVAL_HOURS=6
MEMORY_AUTO_BACKUP_KEEP_DAYS=7

# 空闲触发（秒）
MEMORY_IDLE_FLUSH_SECONDS=180
```

---

## 📚 更多文档

| 文档 | 内容 |
|------|------|
| **[DOCUMENTATION.md](DOCUMENTATION.md)** | 项目总览、记忆设计核心思想、与传统 RAG 对比 |
| **[USAGE.md](USAGE.md)** | 详细使用指南和代码示例 |
| **[EXTRACTOR_CONFIG.md](EXTRACTOR_CONFIG.md)** | 实体提取器配置说明 |

---

## 🛡️ 数据安全

- **自动备份**：每 6 小时导出 JSON 备份到 `backups/` 目录，保留 7 天
- **手动备份**：`POST /api/backup` 接口
- **清空保护**：`clear_all()` 需要显式确认参数
- **测试隔离**：测试用例不再清空整个数据库

---

_大道至简，繁花落尽见真淳_
