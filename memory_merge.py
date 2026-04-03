"""
memory_merge.py - 记忆合并和压缩

功能：
1. 定期扫描相似事件节点并合并
2. 添加重要性阈值，低于阈值的记忆在衰减后自动删除
3. 冲突检测：同一实体有矛盾属性时标记
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from memory_graph import get_graph, MemoryGraph
from config import (
    MEMORY_MERGE_ENABLED,
    MEMORY_MERGE_SIMILARITY_THRESHOLD,
    MEMORY_MERGE_MIN_SALIENCE,
    MEMORY_FORGET_THRESHOLD,
    DECAY_RATE,
    MIN_WEIGHT,
)

logger = logging.getLogger(__name__)


def _text_similarity(a: str, b: str) -> float:
    """计算两段文本的 Jaccard 相似度"""
    if not a or not b:
        return 0.0

    # 使用字符级分词（中文友好）
    set_a = set(a)
    set_b = set(b)

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)

    return intersection / union if union > 0 else 0.0


def _get_time_bucket(ts_iso: str) -> str:
    """将时间戳转换为时间桶（按天）"""
    try:
        dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return "unknown"


def find_similar_memories(
    graph: MemoryGraph,
    threshold: float = MEMORY_MERGE_SIMILARITY_THRESHOLD,
    limit: int = 100,
) -> List[Tuple[str, str, float]]:
    """
    查找相似记忆对

    Returns:
        [(id_a, id_b, similarity), ...]
    """
    similar_pairs: List[Tuple[str, str, float]] = []

    try:
        with graph.driver.session() as session:
            # 获取所有事件节点
            events = session.run("""
                MATCH (n:Node)
                WHERE n.full_text IS NOT NULL
                RETURN n.id as id, n.full_text as text, n.timestamp as ts
                ORDER BY n.created_at DESC
                LIMIT $limit
            """, {"limit": limit})

            event_list = list(events)

            # 两两比较（只比较同一时间桶内的）
            time_buckets: Dict[str, List[dict]] = {}
            for e in event_list:
                ts = e["ts"] or ""
                bucket = _get_time_bucket(ts)
                if bucket not in time_buckets:
                    time_buckets[bucket] = []
                time_buckets[bucket].append({
                    "id": e["id"],
                    "text": e["text"],
                })

            # 在每个时间桶内查找相似对
            for bucket, items in time_buckets.items():
                for i in range(len(items)):
                    for j in range(i + 1, len(items)):
                        sim = _text_similarity(items[i]["text"], items[j]["text"])
                        if sim >= threshold:
                            similar_pairs.append((
                                items[i]["id"],
                                items[j]["id"],
                                sim,
                            ))

    except Exception as e:
        logger.warning(f"查找相似记忆失败：{e}")

    return similar_pairs


def merge_memory_nodes(
    graph: MemoryGraph,
    id_a: str,
    id_b: str,
) -> Optional[str]:
    """
    合并两个记忆节点

    策略：
    1. 保留 salience 较高的节点的 ID
    2. 合并 full_text（用分隔符拼接）
    3. 合并实体连接（取并集）
    4. 删除另一个节点

    Returns:
        合并后的节点 ID，或 None（失败）
    """
    try:
        with graph.driver.session() as session:
            # 获取两个节点的属性
            props_a = session.run("""
                MATCH (n:Node {id: $id})
                RETURN n.full_text as text, n.salience as salience
            """, {"id": id_a}).single()

            props_b = session.run("""
                MATCH (n:Node {id: $id})
                RETURN n.full_text as text, n.salience as salience
            """, {"id": id_b}).single()

            if not props_a or not props_b:
                return None

            # 决定保留哪个节点（salience 高的）
            sal_a = props_a["salience"] or 0.5
            sal_b = props_b["salience"] or 0.5

            if sal_a >= sal_b:
                keep_id, remove_id = id_a, id_b
                keep_text = props_a["text"]
                merge_text = props_b["text"]
            else:
                keep_id, remove_id = id_b, id_a
                keep_text = props_b["text"]
                merge_text = props_a["text"]

            # 合并文本
            merged_text = f"{keep_text}\n\n[合并记忆] {merge_text}"

            # 更新保留节点的文本
            session.run("""
                MATCH (n:Node {id: $id})
                SET n.full_text = $text,
                    n.merged_at = $ts
            """, {"id": keep_id, "text": merged_text, "ts": datetime.now().isoformat()})

            # 转移边：将 remove_id 的边转移到 keep_id
            # 无向图：匹配所有方向的边，先 MERGE 新边，再 DELETE 旧边
            session.run("""
                MATCH (other:Node)-[r:RELATED]-(t:Node {id: $remove_id})
                MATCH (k:Node {id: $keep_id})
                MERGE (other)-[nr:RELATED]-(k)
                ON CREATE SET nr.weight = r.weight, nr.tier = r.tier
                WITH r
                LIMIT 1000
                DELETE r
            """, {"remove_id": remove_id, "keep_id": keep_id})

            # 删除旧节点
            session.run("""
                MATCH (n:Node {id: $id})
                DETACH DELETE n
            """, {"id": remove_id})

            logger.info(f"合并记忆节点：{remove_id} → {keep_id}")
            return keep_id

    except Exception as e:
        logger.exception(f"合并记忆节点失败：{e}")
        return None


def forget_low_salience_memories(
    graph: MemoryGraph,
    threshold: float = MEMORY_FORGET_THRESHOLD,
) -> int:
    """
    遗忘低重要性记忆

    Returns:
        删除的节点数量
    """
    deleted_count = 0

    try:
        with graph.driver.session() as session:
            # 查找 salience 低于阈值的节点
            low_salience = session.run("""
                MATCH (n:Node)
                WHERE n.salience < $threshold
                RETURN n.id as id, n.salience as salience
            """, {"threshold": threshold})

            for record in low_salience:
                node_id = record["id"]

                # 检查是否有强连接（weight > MIN_WEIGHT 的边）
                edges = session.run("""
                    MATCH (n:Node {id: $id})-[r:RELATED]-()
                    WHERE r.weight > $min_weight
                    RETURN count(r) as cnt
                """, {"id": node_id, "min_weight": MIN_WEIGHT}).single()

                if edges and edges["cnt"] > 0:
                    # 有强连接，保留
                    continue

                # 删除节点
                session.run("""
                    MATCH (n:Node {id: $id})
                    DETACH DELETE n
                """, {"id": node_id})

                deleted_count += 1
                logger.info(f"遗忘低重要性记忆：{node_id} (salience={record['salience']})")

    except Exception as e:
        logger.exception(f"遗忘记忆失败：{e}")

    return deleted_count


def detect_conflicts(
    graph: MemoryGraph,
    entity_name: str,
) -> List[Dict[str, Any]]:
    """
    检测同一实体的矛盾属性

    Returns:
        矛盾列表
    """
    conflicts: List[Dict[str, Any]] = []

    try:
        with graph.driver.session() as session:
            # 查找连接到该实体的所有事件
            events = session.run("""
                MATCH (e:Node {name: $name})<-[:RELATED]-(ev:Node)
                WHERE ev.memory_kind = 'fact'
                RETURN ev.full_text as text, ev.emotion_valence as valence
            """, {"name": entity_name})

            # 检查 valence 是否有显著差异（矛盾）
            valences = []
            for e in events:
                v = e["valence"]
                if v is not None:
                    valences.append((e["text"], float(v)))

            if len(valences) >= 2:
                # 检查最大差异
                max_v = max(valences, key=lambda x: x[1])
                min_v = min(valences, key=lambda x: x[1])

                if max_v[1] - min_v[1] > 0.6:  # 显著差异
                    conflicts.append({
                        "entity": entity_name,
                        "positive": max_v[0],
                        "negative": min_v[0],
                        "valence_gap": max_v[1] - min_v[1],
                    })

    except Exception as e:
        logger.exception(f"检测冲突失败：{e}")

    return conflicts


def run_memory_maintenance() -> Dict[str, Any]:
    """
    运行记忆维护任务

    Returns:
        维护结果统计
    """
    if not MEMORY_MERGE_ENABLED:
        return {"status": "disabled"}

    graph = get_graph()
    stats = {
        "similar_pairs_found": 0,
        "memories_merged": 0,
        "memories_forgotten": 0,
        "conflicts_detected": 0,
    }

    try:
        # 1. 查找并合并相似记忆
        similar_pairs = find_similar_memories(graph)
        stats["similar_pairs_found"] = len(similar_pairs)

        for id_a, id_b, _ in similar_pairs:
            if merge_memory_nodes(graph, id_a, id_b):
                stats["memories_merged"] += 1

        # 2. 遗忘低重要性记忆
        stats["memories_forgotten"] = forget_low_salience_memories(graph)

        # 3. 检测冲突（可选：只检测高频实体）
        # 这里简化处理，不扫描所有实体

        logger.info(f"记忆维护完成：{stats}")

    except Exception as e:
        logger.exception(f"记忆维护失败：{e}")

    return stats
