# MiniMem 项目文档

本文档描述 **mini_mem** 的整体结构、运行方式，以及**记忆子系统的设计思想与实现要点**。适合希望理解「为什么这样建模、怎样读写与衰减」的开发者阅读。

---

## 1. 项目是什么

**MiniMem** 是一个极简的「类人记忆」原型：用 **Neo4j** 存一张**关联网络**（概念、人、时间、事件片段等），在对话时**按当前输入从图中检索相关片段**，拼进系统提示词，让大模型像「记得一些往事」一样接话；对话结束后可选把本轮摘要**写回图中**，供以后联想。

技术栈概览：

| 组件 | 作用 |
|------|------|
| Neo4j | 持久化记忆图（节点 + 无向加权边） |
| OpenAI 兼容 LLM | 对话生成；可选：结构化分析写入内容、可选向量嵌入 |
| FastAPI + 静态页 | Web 对话入口（`web_server.py` + `static/index.html`） |
| CLI | 交互式调试（`cli.py`） |

---

## 2. 目录与模块职责

```
mini_mem/
├── config.py           # 环境变量、Neo4j/LLM/召回与衰减参数
├── memory_graph.py     # Neo4j 封装：Node、RELATED、连通性校验
├── store.py            # 写入记忆：分析（规则/LLM）→ 建节点与边
├── recall.py           # 检索：入口匹配 → 图激活扩散 → 显著性/情绪/时效加权
├── memory_text.py      # 召回中文切词、实体类型启发式（无 Neo4j 依赖）
├── maintenance.py      # 按边 tier 的差异化衰减、统计、孤立节点清理
├── chat.py             # 一轮对话：recall → 拼 system → LLM → 缓冲或单轮 store
├── batch_memory.py     # 会话缓冲：满 N 轮增量抽取批量写 Neo4j；关页 flush
├── backup.py           # 自动备份：定期导出 JSON 备份，清理过期文件
├── web_server.py       # HTTP API（含 /api/chat）
├── embeddings.py       # 可选：OpenAI 兼容 embeddings API
├── cli.py              # 命令行
├── tests/              # 基础测试
├── start_neo4j.sh      # Docker 启动 Neo4j（示例）
└── USAGE.md / EXTRACTOR_CONFIG.md / README.md  # 其他说明
```

---

## 3. 项目架构

### 3.1 系统架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         用户接口层                                    │
├─────────────────────┬───────────────────────┬───────────────────────┤
│   Web 前端          │   CLI 命令行          │   外部 API 调用        │
│   (static/index.html)│   (cli.py)           │   (FastAPI)           │
└─────────┬───────────┴───────────┬───────────┴───────────┬───────────┘
          │                       │                       │
          └───────────────────────┼───────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │      对话处理层            │
                    │       chat.py             │
                    │  ┌─────────────────────┐  │
                    │  │  会话缓冲 (batch_memory)│  │
                    │  └─────────────────────┘  │
                    └─────────────┬─────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
    ┌─────▼──────┐        ┌──────▼──────┐        ┌──────▼──────┐
    │  记忆检索   │        │  记忆存储    │        │  LLM 对话   │
    │  recall.py │        │  store.py   │        │  chat.py    │
    └─────┬──────┘        └──────┬──────┘        └──────┬──────┘
          │                      │                      │
          │              ┌───────▼────────┐             │
          │              │  实体/情绪分析   │             │
          │              │  (LLM/规则)     │             │
          │              └───────┬────────┘             │
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌────────────▼────────────┐
                    │      数据存储层          │
                    │   ┌─────────────────┐   │
                    │   │   Neo4j 图数据库 │   │
                    │   │  (节点 + 加权边)  │   │
                    │   └─────────────────┘   │
                    │   ┌─────────────────┐   │
                    │   │  自动备份 (backup)│   │
                    │   └─────────────────┘   │
                    └─────────────────────────┘
```

### 3.2 数据流转

#### 写入流程（存储记忆）

```
用户输入 → chat.py → batch_memory.py(缓冲)
                          │
                          ▼ (满 N 轮或 flush 触发)
                    LLM 批量抽取 {summary, text}
                          │
                          ▼
                    store.py → analyze_memory()
                          │
                          ├──→ 实体抽取 → normalize_entities()
                          ├──→ 情绪分析 → valence/arousal
                          ├──→ 显著性 → salience
                          └──→ memory_kind 分类
                          │
                          ▼
                    memory_graph.py → 创建节点 + 连边
                          │
                          ▼
                       Neo4j 持久化
```

#### 读取流程（检索记忆）

```
用户输入 → recall.py
              │
              ├──→ 查询分词 (memory_text.py)
              ├──→ 入口节点查找 (find_nodes_by_name)
              ├──→ 向量检索 (可选，embeddings.py)
              │
              ▼
         图激活扩散 (Cypher 递归查询)
              │
              ├──→ 路径权重计算
              └──→ 应用层衰减 (DECAY_PER_HOP^depth)
              │
              ▼
         _recall_boost() 加权
              │
              ├──→ salience 调制
              ├──→ 情绪显著度
              └──→ 时效衰减
              │
              ▼
         排序返回 top_k
```

### 3.3 模块依赖关系

```
config.py (配置中心，无依赖)
    │
    ├──→ memory_graph.py (仅依赖 config)
    │       │
    │       └──→ store.py
    │       └──→ recall.py
    │       └──→ maintenance.py
    │
    ├──→ memory_text.py (无 Neo4j 依赖)
    │       │
    │       └──→ store.py
    │       └──→ recall.py
    │
    ├──→ embeddings.py (可选)
    │       │
    │       └──→ store.py
    │       └──→ recall.py
    │
    └──→ chat.py (依赖 recall, store, batch_memory)
            │
            └──→ web_server.py
            └──→ cli.py
```

### 3.4 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 节点标签 | 统一用 `Node` | 简化 schema，用 `type` 属性区分角色 |
| 关系类型 | 统一用 `RELATED` | 避免过细分类，共现即相连 |
| 边方向 | 无向图 | 语义上「A 与 B 相关」是对称的 |
| 激活计算 | 应用层 | Cypher 递归性能有限，Python 更灵活 |
| 写入策略 | 批量缓冲 | 减少 LLM 调用次数，降低延迟 |
| 遗忘机制 | 边衰减 | 节点保留，只删除弱连接，更温和 |

---

## 4. 记忆设计的核心思想（必读）

本节是本项目与「普通 RAG」最大的区别：**记忆不是一堆平铺的文档块，而是一张带权重与类型的关联图**，读写都围绕「联想」与「浓淡」展开。

---

## 5. 设计优势：与传统 RAG 的对比

本节是本项目与「普通 RAG」最大的区别：**记忆不是一堆平铺的文档块，而是一张带权重与类型的关联图**，读写都围绕「联想」与「浓淡」展开。

---

## 4. 设计优势：与传统 RAG 的对比

| 维度 | 传统 RAG | MiniMem 记忆图谱 |
|------|----------|------------------|
| **存储单元** | 文档切片（chunk） | 事件节点 + 实体节点 + 带权重边 |
| **检索方式** | 关键词/向量相似度 | 图激活扩散 + 显著性/情绪/时效加权 |
| **关联表达** | 隐式（靠向量空间接近） | 显式（`RELATED` 边 + `weight` + `tier`） |
| **遗忘机制** | 无（数据永久存在） | 边按 `tier` 差异化衰减，弱边自动删除 |
| **联想能力** | 弱（依赖单一查询） | 强（多跳扩散，可发现间接关联） |
| **可解释性** | 中（相似度分数） | 高（路径清晰可见，权重变化可追踪） |
| **写入成本** | 低（直接嵌入存储） | 中（需 LLM 分析实体/情绪/显著性） |
| **长期演化** | 堆积（只增不减） | 瘦身（冷门路径自然消失） |

### 4.1 核心优势详解

#### 优势 1：联想式召回（更接近人类记忆）

传统 RAG 是「查」——给一个 query，返回最相似的文档块。  
MiniMem 是「想」——从输入中的关键词出发，在图中**扩散激活**，顺着边找到相关联的记忆片段，再根据显著性、情绪强度、时间新鲜度做加权排序。

> **例子**：用户说「上次去公园」，传统 RAG 可能只返回包含「公园」的片段；而 MiniMem 可以从「公园」节点出发，扩散到「散步」「遇到邻居」「那天天气很好」等相关节点，召回更丰富的上下文。

#### 优势 2：记忆有「浓淡」（权重 + 衰减）

- **权重**：重要的、情绪强烈的事件，与实体的连边更粗（`weight` 更高），更容易在扩散中被激活。
- **衰减**：边按 `tier`（`slow`/`normal`/`fast`）使用不同速率衰减，长期不用的路径会变弱甚至断开。

> **设计意图**：模拟人类记忆的「用进废退」——常用路径越来越亮，冷门路径自然消失，图谱不会无限膨胀。

#### 优势 3：数据结构可解释

- 每个节点有明确的 `name`、`type`、`full_text`、`salience`、`emotion_valence`、`emotion_arousal`。
- 每条边有 `weight`（强度）和 `tier`（衰减档位）。
- 召回路径可以可视化追踪（Neo4j Browser 直接查看）。

> **对比**：向量检索只返回一个相似度分数，无法解释「为什么这条记忆被召回」。

#### 优势 4：批量写入 + 空闲触发 + 自动备份

- **批量写入**：满 N 轮对话后一次性抽取并写入，减少 LLM 调用次数。
- **空闲触发**：3 分钟无活动自动 flush 缓冲，避免数据丢失。
- **自动备份**：每 6 小时导出 JSON 备份，保留 7 天，支持数据恢复。

### 4.2 适合的场景

- **长期对话伴侣**：需要记住用户的偏好、经历、承诺，并随时间演化。
- **个性化助手**：根据用户历史行为提供定制化建议。
- **情感陪伴**：记住情绪强烈的时刻，在合适时机自然提起。

### 4.3 不适合的场景

- **纯知识问答**：不需要「记忆」，只需要查文档或知识库。
- **高精度事实检索**：图谱更适合模糊联想，而非精确匹配。
- **数据合规要求极高**：Neo4j 需自行部署和维护，备份策略需额外设计。

---

## 5. 记忆设计的核心思想（必读）

### 5.1 一句话概括

> **记忆 = 统一标签下的节点 + 共现/语义上的连边 + 边上的强度与衰减档位；回想时从线索出发做激活扩散，再用显著性、情绪强度与新鲜度调制排序。**

### 5.2 图模型：只有一种节点标签，一种关系类型

- **节点**：标签为 `Node`，用 `name`、`type`（如 person / concept / event / time / place / organization / topic）、`id` 等区分角色；**同一 `name` 允许配合不同 `type` 共存**（例如同名「苹果」作 organization 与作 concept），`get_or_create_node` 按 **(name, type)** 查找或创建。事件类节点还会带上 `full_text`、`timestamp`、`salience`、`emotion_valence`、`emotion_arousal`、`memory_kind` 等属性。
- **关系**：无向语义上用 `RELATED` 表示「曾一起出现在同一段经验里或统计上共现」；边上存 `weight`（强度）与 `tier`（`slow` / `normal` / `fast`，决定日后衰减快慢）。

设计意图：

1. **不引入过细的本体论**：先让「能连起来」比「分类绝对正确」更重要，符合原型阶段快速迭代。
2. **权重即使用痕迹**：重复共现、强化逻辑会让常用路径更「亮」，冷门路径自然变暗。
3. **tier 模拟不同记忆的遗忘曲线**：承诺、偏好等希望「慢忘」，闲聊、低显著性边可以「快忘」。

### 5.3 写入：从一段文本到「事件 + 实体网」

写入入口主要是 `store_memory`：**Web 批量模式**下由满批/flush 时的 **`batch_memory`** 对每条 `memories[]` 调用；**非批量**时由 `chat.py` 在每轮（或异步后台）对整段转写调用。

1. **分析阶段**（`analyze_memory`）：根据 `ENTITY_EXTRACTOR` 选择  
   - `simple`：规则抽词（日期时间正则 + **子串匹配常见时间词**，避免旧版带空格正则匹配不到「今天」等问题）+ 默认情绪/显著性；  
   - `llm`：模型输出**单一 JSON**；实体优先为 `{"text","type"}` 数组（`person|place|time|concept|organization|topic`），否则退化为字符串数组并由 `memory_text.guess_entity_node_type` 启发式补类型；同时输出 `emotion`、`salience`、`memory_kind`；  
   - `hybrid`：规则与 LLM **合并实体列表**，类型提示以 LLM 为准。  
   这样同一段话不仅留下「说了什么」，还留下**有多重要、偏事实还是闲聊、情绪多强**等信号，供后续召回加权。

2. **建事件节点**：**`full_text`** 始终存写入时的完整正文（供向量与召回）；**节点 `name`** 为简短浏览名——**批量路径**可由 LLM 输出 `summary`（`store_memory(..., display_name=...)`）；**`【对话】` 整段转写**（旧每轮路径）则自动用 **「对话·」+ 用户首句** 作 `name`，避免图里出现「助手：哎呀…」占满名称。纯字符串仍可用正文前 50 字截断作后备。  
   **相对日期**：像「今天」「昨天」「明天」这类词若直接当节点名，会把不同日历日的经历混在同一点上；写入前会用 **`store_memory` 当天的本地日期** 把它们归一成 **`YYYY-MM-DD`** 时间实体（见 `memory_text.normalize_temporal_entities`）。记忆分析 LLM 的 user 提示里也会带上当前日期，鼓励直接输出具体日。召回时若用户话里仍说「今天」，**`recall_query_tokens`** 会同时加入当天的 ISO 日期，便于命中归一化后的节点。历史上已存在的名为「今天」的旧节点可手动合并或删除。

3. **连边策略**（核心设计细节）：  
   - **事件—实体**：每个实体按解析出的 **type** `get_or_create_node(name, type)` 后与事件连边；边权重与 `salience`、`arousal` 相关；边的 `tier` 由 `memory_kind` 决定（例如 commitment/preference 偏 `slow`，smalltalk 偏 `fast`）。  
   - **实体—实体**：高显著性或特定类型时做更密的共现连接；全量共现使用 **`connect_node_ids`（已解析的 id）**，避免旧实现里按名字再默认建成 `concept`、与真实类型节点**分裂成两颗节点**的问题。CLI 场景的 `connect_nodes` 仍按「全部为 concept」简化处理。

意图：**重要的、情绪浓的记忆在图里更「结实」、更易被扩散到；闲聊少占拓扑中心。**

### 5.4 召回：图遍历 + 应用层激活（方案 C）

`recall(keyword)` 的流程：

1. **入口**：**不能**只对中文做 `split()`（整句无空格会零入口）。使用 `memory_text.recall_query_tokens`：英文按标点/空白切、中文在连续汉字段内取 2～4 字滑窗并去停用，再对整句（适度长度内）做一次 `search_nodes`；对每个 token 用 **`find_nodes_by_name`（同名多 type 全部作为入口）** + 模糊 `search_nodes`；结果按节点 id 去重。若开启 `RECALL_USE_EMBEDDING`，再补充向量相似度入口。`related_to` 对同名多节点取多条入口上的激活并 **取 max 合并**。

2. **子图与激活**：对每个入口节点，用 Cypher 在限定深度内找路径，路径强度为沿途 `RELATED.weight` 的乘积，再乘以 **`DECAY_PER_HOP` 的 hop 次方**，得到应用层「激活度」。多入口取各节点上的最大激活。

3. **再加权（`_recall_boost`）**：对每个候选节点，用其属性做调制：  
   - **显著性 `salience`**：整体拉高/压低该节点在排序中的存在感；  
   - **情绪**：`arousal` 与 `valence` 的绝对值组合成「情绪显著度」，偏激烈或偏明确的记忆略突出；  
   - **时效**：按 `timestamp` / `created_at` 做半衰衰减（`RECAL_RECENCY_HALF_LIFE_DAYS`），新近的略优先。  
   最终分数落在 `RECAL_BOOST_MIN`～`RECAL_BOOST_MAX` 之间再与激活相乘。

意图：**既尊重拓扑结构（联想扩散），又尊重「这段记忆当时有多重要、多动情、多新」**，比纯关键词或纯向量更接近粗粒度的人类回忆偏好。

### 5.5 维护：不用则弱，弱极则断

`maintenance.daily_decay` 对所有 `RELATED` 边按 `tier` 使用不同减法衰减；低于 `MIN_WEIGHT` 的边删除。可配合清理孤立节点。

意图：**图谱会随时间「瘦身」**，长期不激活的路径自然让位给新经验，而不是无限堆积均匀噪声。

### 5.6 与大模型的分工

| 环节 | 图 / 规则 | 大模型 |
|------|-----------|--------|
| 存什么结构 | Neo4j schema、连边策略 | 可选：解析实体与情绪标签 |
| 取什么给对话 | recall 排序后的名称列表 → 拼进 system | 根据提示与历史生成自然语言回复 |
| 可选增强 | 节点上的 embedding | embeddings API |

**原则**：图负责**可解释、可衰减、可联想**的长期结构；LLM 负责**理解与措辞**；二者不要互相替代——既不用纯向量库冒充「记忆」，也不把整本历史塞进 prompt。

---

## 6. 网页端发一句话时的内部流程（端到端）

下面假设你已用 `uvicorn` 启动服务，并在浏览器打开 **`http://127.0.0.1:8765`**（`static/index.html`）。

### 6.1 短期上下文（`CHAT_HISTORY_MAX_TURNS`）

服务端在 **`chat_turn`** 内对 **`history` 先裁剪**：只保留最近 **N 个来回**（`N = CHAT_HISTORY_MAX_TURNS`，默认 10），即最多 **`2N` 条** `user`/`assistant` 消息，再拼进对话 LLM。更早的内容依赖 **`recall`** 从图里补回 system，而不是无限堆在 prompt 里。

### 6.2 浏览器 → API

1. 前端发送 **`POST /api/chat`**，JSON 含 **`message`**、**`history`**、**`remember`**，以及 **`session_id`**（`sessionStorage` 持久化；首批可为空，响应里会下发新 id）。  
2. **`api_chat`** 将 `history` 转为 `dict` 列表，调用 **`chat_turn(..., session_id=...)`**。

### 6.3 记忆检索与对话 LLM

3. **`build_memory_block` → `recall`**（见 3.4）：生成「相关记忆要点」与 **`memory_snippets`**；图不可用时有 **`memory_error`**，仍继续对话。  
4. **`llm_chat`**：一次 **`/chat/completions`**，产出助手 **`reply`**（含对可见 CoT 的剥离逻辑）。

### 6.4 写入 Neo4j：批量模式（默认，`MEMORY_BATCH_ENABLED=true`）

5. **`remember=true`** 时，本轮 **`(用户话, reply)` 写入内存中的会话缓冲**（`batch_memory.SessionBuffer`，按 **`session_id`** 分桶），**本请求内不调** `store_memory`。  
6. 当该会话缓冲满 **`MEMORY_BATCH_TURNS`**（默认 10）对 **`(user, assistant)`**：
   - **`api_chat`** 注册 **`BackgroundTasks.add_task(batch_flush_worker, session_id)`**；
   - 后台任务：取**当前缓冲中前 10 对**拼成转写文本，带上会话内 **`digest`（已入库摘要）** 调 **一次批量抽取 LLM**，得到 **`digest` 更新** + **`memories[]`**（推荐每项为 **`{"summary","text"}`**：短标题作 Neo4j **节点名**，完整 **`text`** 进 **`analyze_memory` 与 `full_text`**；兼容纯字符串）。对 **`memories` 每条**调用 **`store_memory`**（内部仍走 `analyze_memory` + 建图）；随后在缓冲中 **丢弃最旧 `MEMORY_BATCH_TURNS - MEMORY_BATCH_KEEP_PAIRS` 对**（默认丢 5 对、**保留最后 5 对**），以便与下一批重叠、利于和已有图结构衔接；**增量**靠提示词要求模型勿重复摘要中已有事实。
   - **空闲超时触发**：若会话超过 **`MEMORY_IDLE_FLUSH_SECONDS`**（默认 180 秒，3 分钟）无活动，后台线程会自动 flush 剩余缓冲。
7. **关标签页**：前端 **`sendBeacon`** 调 **`POST /api/chat/flush`**，把**未满 10 对**的剩余缓冲也做一次同样抽取并写库，然后清空该会话缓冲（避免「聊了 9 轮关页啥也没进图」）。

**批量模式下不再使用**「每轮 `MINIMEM_ASYNC_STORE` + 单条 `format_turn_for_memory`」路径。

### 6.5 写入 Neo4j：兼容旧路径（`MEMORY_BATCH_ENABLED=false`）

- **`MINIMEM_ASYNC_STORE=true`**：`chat_turn(remember=False)` 返回后，后台 **`store_memory(format_turn_for_memory(...))`**（每轮一条）。  
- **`MINIMEM_ASYNC_STORE=false`**：`chat_turn` 内同步每轮 `store_memory`。

### 6.6 返回浏览器

响应 JSON 含 **`reply`**、**`memory_snippets`**、**`session_id`**、**`pending_batch_flush`**（本回合是否触发了满批后台任务）、**`memory_batch_enabled`**，以及可选的 **`memory_error`**。前端保存 **`session_id`** 供下轮回传。

### 6.7 流程小结

| 顺序 | 批量默认 | 说明 |
|------|----------|------|
| 1 | ✓ | 裁剪 `history` → `recall` → 对话 LLM → `reply` |
| 2 | ✓ | 本轮对写入 `session_id` 缓冲；满 10 对则后台批量 LLM + 多条 `store_memory` + 缓冲滑窗 |
| 3 | ✓ | 关页 `flush` 写剩余缓冲 |
| （旧） | ✗ | 每轮后台或同步单条 `store_memory` |

**成本提示**：批量模式下单轮对话**不再**附带「记忆分析」LLM；但在满批时会有 **1 次批量抽取 LLM + 若干次 `store_memory`（每条仍可能调 `analyze_memory` LLM）**。若希望批量后分析也省，可把 **`ENTITY_EXTRACTOR=simple`**。

---

## 7. 配置要点（`.env`）

与记忆强相关的变量（完整列表见 `config.py`）：

- **Neo4j**：`NEO4J_URI`、`NEO4J_USER`、`NEO4J_PASSWORD`（macOS 上连接异常时可试 `bolt://127.0.0.1:7687`）。  
- **LLM**：`LLM_API_BASE`（填到 `/v1` 即可，代码会拼接 `/chat/completions` 与 `/embeddings`）、`LLM_MODEL`、`get_llm_api_key()` 所读的密钥来源（`.env` / `api_key.local` 等）。  
- **写入分析**：`ENTITY_EXTRACTOR`、`MAX_ENTITIES`、`SALIENCE_FULL_MESH`、`SKIP_MESH_KINDS`。  
- **向量**：`STORE_EMBEDDING`、`RECALL_USE_EMBEDDING` 及候选上限等。  
- **召回**：`ACTIVATION_DEPTH`、`DECAY_PER_HOP`、`RECALL_TOP_K`、`RECAL_*` 系列。  
- **衰减**：`DECAY_RATE`、`DECAY_RATE_SLOW`、`DECAY_RATE_FAST`、`MIN_WEIGHT`。  
- **批量写图（Web 默认）**：`MEMORY_BATCH_ENABLED`（默认 true）、`MEMORY_BATCH_TURNS`（默认 10）、`MEMORY_BATCH_KEEP_PAIRS`（默认 5，满批后保留在缓冲中的对数）。  
- **短期上下文条数**：`CHAT_HISTORY_MAX_TURNS`（默认 10 个来回）。  
- **自动备份**：`MEMORY_AUTO_BACKUP_ENABLED`（默认 true）、`MEMORY_AUTO_BACKUP_INTERVAL_HOURS`（默认 6 小时）、`MEMORY_AUTO_BACKUP_KEEP_DAYS`（默认 7 天）。
- **空闲 flush**：`MEMORY_IDLE_FLUSH_SECONDS`（默认 180 秒，3 分钟无活动自动 flush）。
- **仅非批量模式**：`MINIMEM_ASYNC_STORE`——每轮先返回再后台 `store_memory`。

---

## 8. 延迟与并行：哪些能提速、哪些不能

| 环节 | 能否与「对话 LLM」并行 | 说明 |
|------|------------------------|------|
| **recall（Neo4j）** | 否（须先于对话拼 system） | 若 **`RECALL_USE_EMBEDDING`**，recall 内部可对 **embedding 与图查**做并行。 |
| **对话 LLM** | — | 主瓶颈之一；可换模型或流式（未默认实现）。 |
| **批量写图（默认）** | **是** | 满 N 轮后 **`batch_flush_worker`** 在 **BackgroundTasks** 里跑：批量抽取 LLM + 多条 `store_memory`；**单轮请求**一般不再等记忆分析。 |
| **旧：每轮异步 store** | **是** | `MEMORY_BATCH_ENABLED=false` 且 `MINIMEM_ASYNC_STORE=true` 时，每轮后台 `store_memory`。 |
| **同轮「对话 + 单条记忆分析」** | 否 | 仅旧路径下，单条 `store_memory` 依赖当轮完整 `reply`。批量路径下，记忆分析拆到 **满批后台** 或 **每条 memory 的 store** 中。 |

**可选手段**：减小 **`memory_top_k`**、**`CHAT_HISTORY_MAX_TURNS`** 以减 prompt；关页务必触发 **`/api/chat/flush`**（前端已 `sendBeacon`）以免未满批丢失。

---

## 9. 运行与调试

1. **安装**：`pip install -r requirements.txt`  
2. **Neo4j**：`./start_neo4j.sh` 或自备实例，保证 Bolt 端口可连。  
3. **清空图**（慎用）：`MemoryGraph().connect()` 后 `clear_all(confirm='CONFIRM_CLEAR_ALL')`，或在 Browser 中执行 `MATCH (n) DETACH DELETE n`。  
   **注意**：`clear_all()` 需要显式确认参数，防止误调用清空数据。
4. **自动备份**：服务启动后每 6 小时自动备份一次到 `backups/` 目录，保留 7 天。
   - 手动触发：`POST /api/backup`
   - 配置：`MEMORY_AUTO_BACKUP_ENABLED`、`MEMORY_AUTO_BACKUP_INTERVAL_HOURS`、`MEMORY_AUTO_BACKUP_KEEP_DAYS`
5. **Web**：`python -m uvicorn web_server:app --host 127.0.0.1 --port 8765`，浏览器打开 `http://127.0.0.1:8765`（勿用 `file://`）。  
6. **图可视化**：Neo4j Browser `http://localhost:7474`（凭据与容器一致）。  
7. **CLI**：`python cli.py` 便于单独测存储与回想。

---

## 10. 延伸阅读

- `README.md`：快速开始与理念摘要  
- `USAGE.md`：命令与 API 示例  
- `EXTRACTOR_CONFIG.md`：实体/记忆分析模式说明  

---

*文档版本随代码演进；若行为与本文不一致，以 `config.py` 与各模块实现为准。*

---

## 11. 客观评估：核心设计思想、优点与风险

### 11.1 核心设计思想（”为什么这样做”）
- 将对话中的信息抽象为图结构：用 `event`（事件/记忆）节点承载完整文本（`full_text`），再把其中的实体作为节点，并通过带 `weight`/`tier` 的边表达“共现强度与遗忘曲线倾向”。
- 把“短期”与“长期”分离：对话里仅保留最近有限轮数（`CHAT_HISTORY_MAX_TURNS`）作为上下文；更早内容依赖 `recall` 从 Neo4j 召回，再以“相关记忆要点”形式注入系统提示。
- 存储时做结构化信号分层：`emotion_valence` / `emotion_arousal` / `salience` / `memory_kind` 共同决定连边策略、事件-实体层级和后续衰减（tier-based decay），让“重要内容更容易被扩散到召回路径”。
- 控制成本与延迟：Web 默认采用“会话缓冲 + 满批后台写图”（`batch_memory`），在降低每轮写入成本的同时，通过“保留滑窗重叠的对数轮数”来提升批量增量抽取与既有图结构的衔接度。

### 11.2 优点（做得相对好的地方）

- 架构闭环清晰：`recall` 负责”读”、`chat_turn` 负责”对话”、`store_memory`（或批量 `batch_memory`）负责”写”，每个环节都有明确入口与可替换策略（`ENTITY_EXTRACTOR`、`RECALL_USE_EMBEDDING`、批量开关等）。
- 图谱语义带权重：相较纯文本记忆，本项目通过 `weight` 表达共现强度，并通过 `tier` 与 `memory_kind` 让不同记忆的衰减速度不同，召回时更贴近”记忆可被激活与遗忘”的直觉模型。
- 批量写图落地：不仅有”满 N 轮触发后台任务”，还提供”未满批在 flush 时写入”的兜底路径（`/api/chat/flush` + `beforeunload sendBeacon`），避免未满批就完全丢失的体验问题。
- 数据安全增强：`clear_all()` 需要确认参数、测试不再清空数据库、自动备份功能定期导出 JSON 备份。
- 空闲超时触发：3 分钟无活动自动 flush 缓冲，避免用户忘记关闭页面时数据丢失。

### 11.3 风险与不足（客观存在的问题）

- **结构化抽取质量强依赖 LLM**：当 `ENTITY_EXTRACTOR=llm` 时，实体抽取来自模型输出，若提示词边界定义不严，会出现”把口语短语/句式碎片当实体”的污染问题。后置清洗虽能缓解，但本质属于输入侧噪声控制问题。
- **对网关输出形态敏感**：部分模型/网关可能把推理/自检内容混入 `content`，需要额外的剥离与兜底规则来保证前端只展示可见回复。该部分规则越多，越需要持续回归测试覆盖典型输出样式。
- **LLM 调用链路可能”每条 memory 仍较重”**：批量把”抽取/摘要”合并了，但每条 `memories` 最终仍会走 `store_memory -> analyze_memory`（取决于 `ENTITY_EXTRACTOR`），因此成本仍可能随 memory 数量线性增长。
- **缺少端到端自动化测试**：目前有较多单元/轻量测试，但从”前端 session_id -> chat_turn -> 缓冲 -> 满批后台 -> flush -> 图内节点/边数量与类型是否符合预期”的全链路覆盖还相对不足。
- **`memory_merge.py` 会删除低 salience 节点**：`forget_low_salience_memories()` 函数会删除 salience 低于阈值的节点，这是设计行为但属于”删除数据”操作。

### 11.4 安全与数据保护

**数据安全**：
- `clear_all()` 需要显式确认参数 `confirm='CONFIRM_CLEAR_ALL'`，防止误调用清空数据。
- 测试文件不再清空整个数据库，只清理带测试前缀的节点。
- 自动备份功能每 6 小时将数据导出到 `backups/` 目录，保留 7 天。
- 手动备份接口：`POST /api/backup`。

### 11.5 建议的改进方向（可以进一步做）

- 在实体抽取侧进一步”收紧实体定义”并加入严格过滤：例如只允许名词性片段、明确反例（句式/态度/元说明），并对抽取结果做包含关系合并与长度区间裁剪（已部分落地，可继续强化）。
- 把前端可见文本与结构化字段强隔离：优先让网关以结构化字段返回最终答案，服务端只取最终答案字段；减少对”文本剥离正则”依赖。
- 加端到端回归测试：至少覆盖两类关键行为：满批写入窗口是否正确滑动、flush 是否能把未满批写入。
- 进一步降低每条 memory 的分析成本：在批量写图后考虑”统一分析一次”或对分析模型/参数做策略化降级（例如在高噪声场景切到 `simple`）。
