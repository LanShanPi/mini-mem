"""
recall.py - 方案 C：Cypher 子图 + 应用层激活；叠加显著性/情绪/时效调制；可选向量入口
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Set, Tuple
from collections import defaultdict, OrderedDict
from datetime import datetime, timezone
import time

from memory_graph import get_graph, MemoryGraph
from memory_text import recall_query_tokens
from config import (
    ACTIVATION_DEPTH,
    RECALL_TOP_K,
    DECAY_PER_HOP,
    RECAL_SALIENCE_WEIGHT,
    RECAL_AROUSAL_WEIGHT,
    RECAL_RECENCY_HALF_LIFE_DAYS,
    RECAL_BOOST_MIN,
    RECAL_BOOST_MAX,
    RECALL_USE_EMBEDDING,
    EMBEDDING_RECALL_TOP_N,
    EMBEDDING_CANDIDATE_LIMIT,
    RECALL_QUERY_MAX_TOKENS,
    RECALL_ENTRY_NODE_LIMIT,
    RECALL_ACTIVATION_CACHE_TTL_SEC,
    RECALL_ACTIVATION_CACHE_MAXSIZE,
)

# activation 扩散短 TTL 缓存：同一节点在短时间内可能被多个 token 命中反复触发
# 使用 OrderedDict 实现 LRU 缓存，定期清理过期和最少使用的条目
_activation_cache: "OrderedDict[Tuple[str, int], Tuple[float, Dict[str, float]]]" = (
    OrderedDict()
)


def _cleanup_activation_cache() -> None:
    """清理过期和最久未使用的缓存条目"""
    now = time.time()
    ttl = RECALL_ACTIVATION_CACHE_TTL_SEC
    maxsize = RECALL_ACTIVATION_CACHE_MAXSIZE

    # 1. 删除过期条目
    expired_keys = [
        key for key, (ts, _) in _activation_cache.items()
        if now - ts > ttl
    ]
    for key in expired_keys:
        del _activation_cache[key]

    # 2. 如果仍超出大小限制，删除最久未使用的条目
    while len(_activation_cache) > maxsize:
        _activation_cache.popitem(last=False)


def _parse_ts(iso: Optional[str]) -> Optional[datetime]:
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _recency_factor(ts_iso: Optional[str]) -> float:
    dt = _parse_ts(ts_iso)
    if dt is None:
        return 1.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    half = max(1.0, RECAL_RECENCY_HALF_LIFE_DAYS)
    return 0.5 ** (age_days / half)


def _recall_boost(props: Dict[str, Any]) -> float:
    try:
        salience = float(props.get("salience", 0.5))
    except (TypeError, ValueError):
        salience = 0.5
    salience = max(0.0, min(1.0, salience))
    try:
        valence = float(props.get("emotion_valence", 0.0))
    except (TypeError, ValueError):
        valence = 0.0
    try:
        arousal = float(props.get("emotion_arousal", 0.0))
    except (TypeError, ValueError):
        arousal = 0.0
    valence = max(-1.0, min(1.0, valence))
    arousal = max(0.0, min(1.0, arousal))

    ts = props.get("timestamp")
    if ts is None:
        ts = props.get("created_at")
    rec = _recency_factor(ts if isinstance(ts, str) else None)

    mood = arousal * (0.45 + abs(valence))
    boost = (
        1.0
        + RECAL_SALIENCE_WEIGHT * (salience - 0.5)
        + RECAL_AROUSAL_WEIGHT * mood
    ) * (0.65 + 0.35 * rec)
    return max(RECAL_BOOST_MIN, min(RECAL_BOOST_MAX, boost))


def _batch_node_props(
    graph: MemoryGraph, node_ids: List[str]
) -> Tuple[Dict[str, str], Dict[str, Dict[str, Any]]]:
    """返回 (id->name, id->原始属性子集)。"""
    if not node_ids:
        return {}, {}
    id_to_name: Dict[str, str] = {}
    id_to_props: Dict[str, Dict[str, Any]] = {}
    query = """
    UNWIND $node_ids AS id
    MATCH (n:Node) WHERE n.id = id
    RETURN n.id AS id, n.name AS name, n.salience AS salience,
           n.emotion_valence AS emotion_valence,
           n.emotion_arousal AS emotion_arousal,
           n.memory_kind AS memory_kind,
           n.timestamp AS timestamp, n.created_at AS created_at, n.type AS type
    """
    try:
        with graph.driver.session() as session:
            rows = session.run(query, node_ids=node_ids)
            for record in rows:
                nid = record["id"]
                id_to_name[nid] = record["name"]
                id_to_props[nid] = {
                    "salience": record["salience"],
                    "emotion_valence": record["emotion_valence"],
                    "emotion_arousal": record["emotion_arousal"],
                    "memory_kind": record["memory_kind"],
                    "timestamp": record["timestamp"],
                    "created_at": record["created_at"],
                    "type": record["type"],
                }
    except Exception as e:
        print(f"⚠ 批量查询节点失败：{e}")
        with graph.driver.session() as session:
            for nid in node_ids[:200]:
                row = session.run(
                    """
                    MATCH (n:Node {id: $id})
                    RETURN n.id AS id, n.name AS name, n.salience AS salience,
                           n.emotion_valence AS emotion_valence,
                           n.emotion_arousal AS emotion_arousal,
                           n.memory_kind AS memory_kind,
                           n.timestamp AS timestamp, n.created_at AS created_at, n.type AS type
                    """,
                    id=nid,
                ).single()
                if row:
                    id_to_name[nid] = row["name"]
                    id_to_props[nid] = {
                        "salience": row["salience"],
                        "emotion_valence": row["emotion_valence"],
                        "emotion_arousal": row["emotion_arousal"],
                        "memory_kind": row["memory_kind"],
                        "timestamp": row["timestamp"],
                        "created_at": row["created_at"],
                        "type": row["type"],
                    }
    return id_to_name, id_to_props


def _embedding_entry_nodes(
    graph: MemoryGraph,
    keyword: str,
    query_vector: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    if not RECALL_USE_EMBEDDING or not keyword.strip():
        return []
    from embeddings import embed_text, cosine_similarity

    qv = query_vector if query_vector is not None else embed_text(keyword)
    if not qv:
        return []
    with graph.driver.session() as session:
        rows = list(
            session.run(
                """
                MATCH (n:Node)
                WHERE n.embedding IS NOT NULL
                RETURN n.id AS id, n.name AS name, n.embedding AS embedding
                LIMIT $lim
                """,
                lim=EMBEDDING_CANDIDATE_LIMIT,
            )
        )
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for record in rows:
        emb = record["embedding"]
        if not emb:
            continue
        sim = cosine_similarity(qv, list(emb))
        scored.append(
            (sim, {"id": record["id"], "name": record["name"], "type": None, "activation": 0.5})
        )
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:EMBEDDING_RECALL_TOP_N]]


def get_subgraph_cypher(
    start_node_id: str,
    depth: int = ACTIVATION_DEPTH,
    graph: Optional[MemoryGraph] = None,
) -> List[Dict]:
    if graph is None:
        graph = get_graph()

    # 限制深度和返回数量，防止大数据量时查询过慢
    depth_range = f"1..{min(depth, 3)}"  # 硬编码最大深度为 3
    max_results = 200  # 限制返回的最大节点数

    query = f"""
    MATCH path = (start)-[r*{depth_range}]-(target)
    WHERE start.id = $start_id
    WITH target,
         reduce(weight = 1.0, rel IN relationships(path) | weight * rel.weight) AS path_weight,
         length(path) as hop_count
    RETURN
        target.id AS target_id,
        target.name AS target_name,
        path_weight,
        hop_count
    ORDER BY hop_count, path_weight DESC
    LIMIT {max_results}
    """

    try:
        with graph.driver.session() as session:
            results = session.run(query, start_id=start_node_id)
            return [
                {
                    "target_id": record["target_id"],
                    "target_name": record["target_name"],
                    "path_weight": record["path_weight"],
                    "hop_count": record["hop_count"],
                }
                for record in results
            ]
    except Exception as e:
        print(f"⚠ Cypher 查询失败：{e}，回退到逐层查询")
        # 大数据量时回退到单层查询
        try:
            fallback_query = """
            MATCH (start {id: $start_id})-[r]-(target)
            RETURN target.id AS target_id, target.name AS target_name,
                   r.weight AS path_weight, 1 AS hop_count
            LIMIT 50
            """
            with graph.driver.session() as session:
                results = session.run(fallback_query, start_id=start_node_id)
                return [
                    {
                        "target_id": record["target_id"],
                        "target_name": record["target_name"],
                        "path_weight": record["path_weight"],
                        "hop_count": record["hop_count"],
                    }
                    for record in results
                ]
        except Exception:
            pass
        return []


def activate_spread_cypher(
    start_node_id: str,
    depth: int = ACTIVATION_DEPTH,
    graph: Optional[MemoryGraph] = None,
) -> Dict[str, float]:
    if graph is None:
        graph = get_graph()

    key = (start_node_id, int(depth))
    now = time.time()
    cached = _activation_cache.get(key)
    if cached is not None:
        ts, val = cached
        if now - ts <= RECALL_ACTIVATION_CACHE_TTL_SEC:
            _activation_cache.move_to_end(key)
            return val
        # 过期：丢掉旧值，重新计算
        try:
            del _activation_cache[key]
        except KeyError:
            pass

    # 定期清理缓存（每 100 次调用清理一次，避免每次都清理影响性能）
    if len(_activation_cache) > RECALL_ACTIVATION_CACHE_MAXSIZE * 0.8:
        _cleanup_activation_cache()

    subgraph = get_subgraph_cypher(start_node_id, depth, graph)
    activations: Dict[str, float] = {start_node_id: 1.0}

    for record in subgraph:
        target_id = record["target_id"]
        path_weight = float(record["path_weight"] or 0.0)
        hop_count = int(record["hop_count"] or 0)
        activation = path_weight * (DECAY_PER_HOP**hop_count)
        if target_id not in activations or activations[target_id] < activation:
            activations[target_id] = activation

    _activation_cache[key] = (now, activations)
    _activation_cache.move_to_end(key)
    if len(_activation_cache) > RECALL_ACTIVATION_CACHE_MAXSIZE:
        _activation_cache.popitem(last=False)

    return activations


def _dedupe_entry_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    out: List[Dict[str, Any]] = []
    for n in nodes:
        nid = n.get("id")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        out.append(n)
    return out


def recall(
    keyword: str,
    top_k: int = RECALL_TOP_K,
    graph: Optional[MemoryGraph] = None,
    return_explanation: bool = False,  # 新增：是否返回解释信息
) -> List[Tuple[str, float]]:
    """
    混合检索：关键词 + 向量 + 激活扩散

    Args:
        keyword: 查询关键词
        top_k: 返回数量
        graph: 图实例
        return_explanation: 是否返回解释信息（用于调试）

    Returns:
        [(节点名称，分数), ...] 或带解释的信息
    """
    if graph is None:
        graph = get_graph()

    tokens = recall_query_tokens(keyword, max_tokens=RECALL_QUERY_MAX_TOKENS)
    entry_nodes: List[Dict[str, Any]] = []

    # 用于记录检索路径和原因
    recall_trace = {
        "keyword_tokens": tokens,
        "keyword_hits": [],
        "vector_hits": [],
        "activation_spread": [],
    }

    embed_future = None
    embed_ex: Optional[ThreadPoolExecutor] = None
    if RECALL_USE_EMBEDDING and keyword.strip():
        from embeddings import embed_text

        embed_ex = ThreadPoolExecutor(max_workers=1)
        embed_future = embed_ex.submit(embed_text, keyword.strip())

    try:
        # 1. 关键词匹配入口
        for kw in tokens:
            found = graph.find_nodes_by_name(kw, limit=6)
            if found:
                entry_nodes.extend(found)
                recall_trace["keyword_hits"].extend([f["name"] for f in found[:3]])
                continue
            entry_nodes.extend(graph.search_nodes(kw, limit=3))

        q = keyword.strip()
        if q and len(q) <= 56:
            entry_nodes.extend(graph.search_nodes(q[:80], limit=6))

        entry_nodes = _dedupe_entry_nodes(entry_nodes)
        if len(entry_nodes) > RECALL_ENTRY_NODE_LIMIT:
            entry_nodes = entry_nodes[:RECALL_ENTRY_NODE_LIMIT]

        # 2. 向量检索入口（新增：混合检索）
        qv: Optional[List[float]] = None
        if embed_future is not None:
            try:
                qv = embed_future.result(timeout=90)
            except Exception as e:
                print(f"⚠ 并行 embedding 获取失败，将回退同步请求：{e}")
                qv = None
        if RECALL_USE_EMBEDDING and keyword.strip():
            vector_entries = _embedding_entry_nodes(graph, keyword, query_vector=qv)
            entry_nodes.extend(vector_entries)
            recall_trace["vector_hits"].extend([n["name"] for n in vector_entries[:3]])
        entry_nodes = _dedupe_entry_nodes(entry_nodes)
    finally:
        if embed_ex is not None:
            embed_ex.shutdown(wait=True)

    if not entry_nodes:
        # 兜底：尝试全文检索（最近 N 条记忆）
        try:
            with graph.driver.session() as session:
                recent = session.run("""
                    MATCH (n:Node)
                    WHERE n.full_text IS NOT NULL
                    RETURN n.id as id, n.name as name, n.type as type, n.activation as activation
                    ORDER BY n.created_at DESC
                    LIMIT $limit
                """, {"limit": 10})
                for record in recent:
                    entry_nodes.append({
                        "id": record["id"],
                        "name": record["name"],
                        "type": record["type"],
                        "activation": record["activation"],
                    })
                recall_trace["recent_fallback"] = len(entry_nodes)
        except Exception:
            pass

        if not entry_nodes:
            print(f"⚠ 未找到与 '{keyword}' 相关的记忆")
            return []

    # 3. 激活扩散
    all_activations: Dict[str, float] = defaultdict(float)
    for entry in entry_nodes:
        sub = activate_spread_cypher(entry["id"], graph=graph)
        for node_id, activation in sub.items():
            if all_activations[node_id] < activation:
                all_activations[node_id] = activation
            recall_trace["activation_spread"].append(entry.get("name", "unknown"))

    if not all_activations:
        # 兜底：直接返回入口节点
        for entry in entry_nodes[:top_k]:
            all_activations[entry["id"]] = entry.get("activation", 0.5)

    node_ids = list(all_activations.keys())
    id_to_name, id_to_props = _batch_node_props(graph, node_ids)

    results: List[Tuple[str, float]] = []
    for node_id, activation in all_activations.items():
        props = id_to_props.get(node_id, {})
        # 混合检索权重：关键词匹配 vs 向量相似度 vs 激活扩散
        boosted = float(activation) * _recall_boost(props)
        name = id_to_name.get(node_id) or f"节点_{node_id[:8]}"
        results.append((name, boosted))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def recall_detailed(keyword: str, graph: Optional[MemoryGraph] = None) -> Dict:
    if graph is None:
        graph = get_graph()

    nodes = recall(keyword, top_k=20, graph=graph)
    events: List[Tuple[str, float]] = []
    concept_nodes: List[Tuple[str, float]] = []

    for node_name, activation in nodes:
        if "..." in node_name or len(node_name) > 40:
            events.append((node_name, activation))
        else:
            concept_nodes.append((node_name, activation))

    if concept_nodes:
        summary = f"找到 {len(concept_nodes)} 个相关概念：" + "、".join(
            [n[0] for n in concept_nodes[:5]]
        )
        if events:
            summary += f"，以及 {len(events)} 条相关事件片段"
    else:
        summary = "没有找到相关记忆"

    return {
        "nodes": concept_nodes,
        "events": events,
        "summary": summary,
    }


def related_to(
    node_name: str,
    depth: int = 2,
    graph: Optional[MemoryGraph] = None,
) -> List[Tuple[str, float]]:
    if graph is None:
        graph = get_graph()

    entries = graph.find_nodes_by_name(node_name, limit=20)
    if not entries:
        return []

    activations: Dict[str, float] = {}
    entry_ids = {e["id"] for e in entries}
    for entry in entries:
        sub = activate_spread_cypher(entry["id"], depth=depth, graph=graph)
        for node_id, act in sub.items():
            if activations.get(node_id, 0.0) < act:
                activations[node_id] = act

    for eid in entry_ids:
        activations.pop(eid, None)

    if not activations:
        return []

    node_ids = list(activations.keys())
    id_to_name, id_to_props = _batch_node_props(graph, node_ids)

    ranked: List[Tuple[str, float]] = []
    for node_id, activation in activations.items():
        props = id_to_props.get(node_id, {})
        boosted = float(activation) * _recall_boost(props)
        name = id_to_name.get(node_id) or f"节点_{node_id[:8]}"
        ranked.append((name, boosted))

    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked[:20]
