"""
memory_graph.py - 核心记忆图操作

封装 Neo4j 的节点和边操作，提供简洁的 API。
"""
from neo4j import GraphDatabase
from typing import List, Dict, Tuple, Optional
import uuid
from datetime import datetime

from config import (
    NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD,
    DEFAULT_EDGE_WEIGHT, STRENGTHEN_AMOUNT, MAX_WEIGHT, MIN_WEIGHT,
)


class MemoryGraph:
    """记忆图操作类"""
    
    def __init__(self, uri=None, user=None, password=None):
        self.uri = uri or NEO4J_URI
        self.user = user or NEO4J_USER
        self.password = password or NEO4J_PASSWORD
        self.driver = None
    
    def connect(self):
        """创建驱动并做一次真实连通性检查（避免未启动时仍打印「已连接」）。"""
        driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
        try:
            driver.verify_connectivity()
        except Exception as e:
            driver.close()
            raise ConnectionError(
                f"无法连接 Neo4j（{self.uri}）。请先启动数据库，例如项目里执行 ./start_neo4j.sh。"
                f"若在 macOS 上仍失败，可在 .env 设 NEO4J_URI=bolt://127.0.0.1:7687 避免走 IPv6。"
                f" 原始错误：{e}"
            ) from e
        self.driver = driver
        self.ensure_indexes()
        print(f"✓ Neo4j 已连通: {self.uri}")
    
    def ensure_indexes(self) -> None:
        """为关键查询字段建立索引（提升 recall 中的 name 精确匹配速度）。"""
        if not self.driver:
            return
        try:
            with self.driver.session() as session:
                # Neo4j 的索引用于 `MATCH (n:Node {name: $name})` 的精确匹配
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.name)")
                # 备用：部分场景也可能用 type 做过滤/检索
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.type)")
                # 新增：id 字段索引（所有查询都用它）
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.id)")
                # 新增：full_text 索引（用于全文检索/兜底查询）
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.full_text)")
                # 新增：salience 索引（用于遗忘功能）
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.salience)")
                # 新增：memory_kind 索引（用于类型过滤）
                session.run("CREATE INDEX IF NOT EXISTS FOR (n:Node) ON (n.memory_kind)")
        except Exception as e:
            # 索引失败不应阻断服务：回退到无索引模式
            print(f"⚠ Neo4j 索引创建失败（可忽略）：{e}")

    def close(self):
        """关闭连接"""
        if self.driver:
            self.driver.close()
            print("✓ 已关闭 Neo4j 连接")
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    # ==================== 节点操作 ====================
    
    def create_node(self, name: str, node_type: str = "concept", 
                    properties: Optional[Dict] = None) -> str:
        """
        创建节点
        
        Args:
            name: 节点名称
            node_type: 节点类型 (person, concept, event, time, place...)
            properties: 额外属性
        
        Returns:
            节点 ID
        """
        node_id = f"node_{uuid.uuid4().hex[:12]}"
        properties = properties or {}
        
        with self.driver.session() as session:
            session.run("""
                CREATE (n:Node {
                    id: $id,
                    name: $name,
                    type: $type,
                    activation: $activation,
                    created_at: $created_at
                })
            """, {
                "id": node_id,
                "name": name,
                "type": node_type,
                "activation": properties.get("activation", 0.5),
                "created_at": datetime.now().isoformat()
            })
            
            # 设置额外属性
            for key, value in properties.items():
                if key not in ["activation"]:
                    session.run("""
                        MATCH (n:Node {id: $id})
                        SET n[$key] = $value
                    """, {"id": node_id, "key": key, "value": value})
        
        return node_id
    
    def get_or_create_node(self, name: str, node_type: str = "concept",
                           properties: Optional[Dict] = None) -> str:
        """
        按 (name, type) 唯一：同名不同类型可共存，避免「人名」与「概念」抢同一节点。
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n:Node {name: $name, type: $type})
                RETURN n.id as id
            """, {"name": name, "type": node_type})
            record = result.single()
            if record:
                return record["id"]
            return self.create_node(name, node_type, properties)

    def find_nodes_by_name(self, name: str, limit: int = 20) -> List[Dict]:
        """同名节点可能有多条（不同类型），召回时应全部作为入口。"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n:Node {name: $name})
                RETURN n.id as id, n.name as name, n.type as type, n.activation as activation
                LIMIT $limit
            """, {"name": name, "limit": limit})
            return [dict(record) for record in result]

    def find_node_by_name(self, name: str) -> Optional[Dict]:
        """兼容旧 API：只取第一条同名节点。"""
        rows = self.find_nodes_by_name(name, limit=1)
        return rows[0] if rows else None
    
    def search_nodes(self, keyword: str, limit: int = 10) -> List[Dict]:
        """模糊搜索节点"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n:Node)
                WHERE n.name CONTAINS $keyword
                RETURN n.id as id, n.name as name, n.type as type, n.activation as activation
                LIMIT $limit
            """, {"keyword": keyword, "limit": limit})
            
            return [dict(record) for record in result]
    
    # ==================== 边操作 ====================
    
    def create_edge(self, node_a_id: str, node_b_id: str,
                    weight: float = DEFAULT_EDGE_WEIGHT,
                    tier: str = "normal") -> bool:
        """
        创建或更新边（无向图）。tier: slow | normal | fast，用于差异化衰减。
        """
        w = max(MIN_WEIGHT, min(float(weight), MAX_WEIGHT))
        with self.driver.session() as session:
            session.run("""
                MATCH (a:Node {id: $id_a}), (b:Node {id: $id_b})
                MERGE (a)-[r:RELATED]-(b)
                ON CREATE SET r.weight = $weight, r.tier = $tier
                ON MATCH SET r.weight = CASE
                    WHEN r.weight + $strengthen > $max_w THEN $max_w
                    ELSE r.weight + $strengthen
                END
            """, {
                "id_a": node_a_id,
                "id_b": node_b_id,
                "weight": w,
                "tier": tier,
                "strengthen": STRENGTHEN_AMOUNT,
                "max_w": MAX_WEIGHT,
            })
        return True
    
    def connect_node_ids(
        self,
        node_ids: List[str],
        weight: float = DEFAULT_EDGE_WEIGHT,
        tier: str = "normal",
    ):
        """已知节点 id 时两两连边（不再次按名解析类型，供 store 共现网使用）。"""
        ids = [x for x in node_ids if x]
        for i, id_a in enumerate(ids):
            for id_b in ids[i + 1 :]:
                self.create_edge(id_a, id_b, weight=weight, tier=tier)

    def connect_nodes(self, names: List[str], weight: float = DEFAULT_EDGE_WEIGHT,
                      tier: str = "normal"):
        """
        将一组节点两两相连（CLI 等场景：一律按 concept 解析/创建，语义简单）。
        """
        node_ids = []
        for name in names:
            node_id = self.get_or_create_node(name, node_type="concept")
            node_ids.append(node_id)

        self.connect_node_ids(node_ids, weight=weight, tier=tier)
    
    def get_neighbors(self, node_id: str) -> List[Tuple[str, float, str]]:
        """
        Returns:
            [(邻居节点 ID, 边权重, 邻居名称), ...]
        """
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n:Node {id: $id})-[r:RELATED]-(neighbor:Node)
                RETURN neighbor.id as id, neighbor.name as name, r.weight as weight
            """, {"id": node_id})
            
            return [(record["id"], record["weight"], record["name"]) for record in result]
    
    def get_edge_weight(self, node_a_id: str, node_b_id: str) -> Optional[float]:
        """获取两个节点之间的边权重"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (a:Node {id: $id_a})-[r:RELATED]-(b:Node {id: $id_b})
                RETURN r.weight as weight
            """, {"id_a": node_a_id, "id_b": node_b_id})
            record = result.single()
            
            if record:
                return record["weight"]
            return None
    
    # ==================== 图操作 ====================
    
    def get_all_nodes(self, limit: int = 100) -> List[Dict]:
        """获取所有节点"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (n:Node)
                RETURN n.id as id, n.name as name, n.type as type, n.activation as activation
                LIMIT $limit
            """, {"limit": limit})
            
            return [dict(record) for record in result]
    
    def get_all_edges(self, limit: int = 1000) -> List[Dict]:
        """获取所有边"""
        with self.driver.session() as session:
            result = session.run("""
                MATCH (a:Node)-[r:RELATED]-(b:Node)
                RETURN a.name as node_a, b.name as node_b, r.weight as weight,
                       coalesce(r.tier, 'normal') as tier
                LIMIT $limit
            """, {"limit": limit})
            
            return [dict(record) for record in result]
    
    def delete_node(self, node_id: str):
        """删除节点及其所有边"""
        with self.driver.session() as session:
            session.run("""
                MATCH (n:Node {id: $id})
                DETACH DELETE n
            """, {"id": node_id})
    
    def clear_all(self, confirm: str = ""):
        """
        清空整个图（调试用）

        Args:
            confirm: 必须传入 "CONFIRM_CLEAR_ALL" 才能执行，防止误调用
        """
        if confirm != "CONFIRM_CLEAR_ALL":
            raise RuntimeError("clear_all() 需要显式确认：clear_all(confirm='CONFIRM_CLEAR_ALL')")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        print("✓ 已清空所有记忆")
    
    # ==================== 统计 ====================
    
    def stats(self) -> Dict:
        """获取图的统计信息"""
        with self.driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) as count").single()["count"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) as count").single()["count"]

            return {
                "nodes": node_count,
                "edges": edge_count
            }

    def export_all(self) -> Dict:
        """
        导出整个记忆图为 JSON 格式。

        Returns:
            {
                "nodes": [{"id": ..., "name": ..., "type": ..., "activation": ..., ...properties}],
                "edges": [{"source": ..., "target": ..., "weight": ..., "tier": ...}]
            }
        """
        with self.driver.session() as session:
            # 导出所有节点
            node_result = session.run("""
                MATCH (n:Node)
                RETURN n {.id, .name, .type, .activation, .created_at, .full_text,
                          .emotion_valence, .emotion_arousal, .salience, .memory_kind,
                          .timestamp} as props
            """)
            nodes = []
            for record in node_result:
                props = record["props"]
                if props:
                    # 过滤掉 None 值
                    nodes.append({k: v for k, v in props.items() if v is not None})

            # 导出所有边（无向图，只返回一次）
            edge_result = session.run("""
                MATCH (a:Node)-[r:RELATED]-(b:Node)
                WHERE id(a) < id(b)
                RETURN a.id as source, b.id as target, r.weight as weight,
                       coalesce(r.tier, 'normal') as tier
            """)
            edges = []
            for record in edge_result:
                edges.append({
                    "source": record["source"],
                    "target": record["target"],
                    "weight": record["weight"],
                    "tier": record["tier"]
                })

            return {"nodes": nodes, "edges": edges}

    def import_data(self, data: Dict) -> Dict:
        """
        从 JSON 格式导入记忆图。

        Args:
            data: {"nodes": [...], "edges": [...]}

        Returns:
            {"imported_nodes": N, "imported_edges": M, "skipped_nodes": S, "skipped_edges": E}
        """
        nodes = data.get("nodes", [])
        edges = data.get("edges", [])
        imported_nodes = 0
        skipped_nodes = 0
        imported_edges = 0
        skipped_edges = 0

        with self.driver.session() as session:
            # 导入节点
            for node in nodes:
                try:
                    node_id = node.get("id")
                    name = node.get("name", "unnamed")
                    node_type = node.get("type", "concept")
                    activation = node.get("activation", 0.5)
                    created_at = node.get("created_at", datetime.now().isoformat())

                    # 检查是否已存在
                    existing = session.run("""
                        MATCH (n:Node {id: $id})
                        RETURN n.id as id
                    """, {"id": node_id}).single()

                    if existing:
                        skipped_nodes += 1
                        continue

                    session.run("""
                        CREATE (n:Node {
                            id: $id,
                            name: $name,
                            type: $type,
                            activation: $activation,
                            created_at: $created_at
                        })
                    """, {
                        "id": node_id,
                        "name": name,
                        "type": node_type,
                        "activation": activation,
                        "created_at": created_at
                    })

                    # 设置额外属性
                    for key, value in node.items():
                        if key not in ["id", "name", "type", "activation", "created_at"]:
                            session.run("""
                                MATCH (n:Node {id: $id})
                                SET n[$key] = $value
                            """, {"id": node_id, "key": key, "value": value})

                    imported_nodes += 1
                except Exception:
                    skipped_nodes += 1

            # 导入边
            for edge in edges:
                try:
                    source_id = edge.get("source")
                    target_id = edge.get("target")
                    weight = edge.get("weight", DEFAULT_EDGE_WEIGHT)
                    tier = edge.get("tier", "normal")

                    # 检查边是否已存在
                    existing = session.run("""
                        MATCH (a:Node {id: $source})-[r:RELATED]-(b:Node {id: $target})
                        RETURN count(r) as cnt
                    """, {"source": source_id, "target": target_id}).single()

                    if existing and existing["cnt"] > 0:
                        skipped_edges += 1
                        continue

                    session.run("""
                        MATCH (a:Node {id: $source}), (b:Node {id: $target})
                        MERGE (a)-[r:RELATED]-(b)
                        ON CREATE SET r.weight = $weight, r.tier = $tier
                    """, {
                        "source": source_id,
                        "target": target_id,
                        "weight": weight,
                        "tier": tier
                    })
                    imported_edges += 1
                except Exception:
                    skipped_edges += 1

        return {
            "imported_nodes": imported_nodes,
            "imported_edges": imported_edges,
            "skipped_nodes": skipped_nodes,
            "skipped_edges": skipped_edges
        }


# 快捷函数
_default_graph: Optional[MemoryGraph] = None

def get_graph() -> MemoryGraph:
    """获取默认图实例（连接失败时不缓存半成品，便于 Neo4j 启动后重试）。"""
    global _default_graph
    if _default_graph is None:
        g = MemoryGraph()
        try:
            g.connect()
        except Exception:
            _default_graph = None
            raise
        _default_graph = g
    return _default_graph

def close_graph():
    """关闭默认图实例"""
    global _default_graph
    if _default_graph:
        _default_graph.close()
        _default_graph = None


