"""
maintenance.py - 按边 tier 差异化衰减与维护
"""
from typing import Optional

from memory_graph import get_graph, MemoryGraph
from config import (
    DECAY_RATE,
    DECAY_RATE_SLOW,
    DECAY_RATE_FAST,
    MIN_WEIGHT,
)


def daily_decay(decay_rate: Optional[float] = None, graph: Optional[MemoryGraph] = None):
    """
    每日衰减：slow / normal / fast 三档；过低权重删边。
    若传入 decay_rate，则以其为 normal 档基准，并按原有 slow/fast 比例缩放。
    """
    if graph is None:
        graph = get_graph()

    if decay_rate is None:
        dn = float(DECAY_RATE)
        ds = float(DECAY_RATE_SLOW)
        df = float(DECAY_RATE_FAST)
    else:
        dn = float(decay_rate)
        base = float(DECAY_RATE) if float(DECAY_RATE) > 0 else 1e-9
        ds = dn * (float(DECAY_RATE_SLOW) / base)
        df = dn * (float(DECAY_RATE_FAST) / base)

    with graph.driver.session() as session:
        session.run(
            """
            MATCH (a:Node)-[r:RELATED]-(b:Node)
            WITH r, coalesce(r.tier, 'normal') AS tier
            SET r.weight = r.weight - CASE tier
                WHEN 'slow' THEN $ds
                WHEN 'fast' THEN $df
                ELSE $dn
            END
            """,
            ds=ds,
            df=df,
            dn=dn,
        )
        session.run(
            """
            MATCH ()-[r:RELATED]-()
            WHERE r.weight <= $min
            DELETE r
            """,
            min=float(MIN_WEIGHT),
        )

    print("✓ 日常衰减完成（按边 tier 分档）")


def cleanup_isolated_nodes(graph: MemoryGraph = None):
    if graph is None:
        graph = get_graph()

    with graph.driver.session() as session:
        result = session.run(
            """
            MATCH (n:Node)
            WHERE NOT (n)--()
            RETURN n.id as id, n.name as name
            """
        )
        isolated = list(result)

        for record in isolated:
            graph.delete_node(record["id"])

    if isolated:
        print(f"✓ 清理了 {len(isolated)} 个孤立节点")
    else:
        print("✓ 没有孤立节点")


def strengthen_path(
    node_a_name: str,
    node_b_name: str,
    amount: float = 0.1,
    graph: MemoryGraph = None,
):
    if graph is None:
        graph = get_graph()

    node_a = graph.find_node_by_name(node_a_name)
    node_b = graph.find_node_by_name(node_b_name)

    if not node_a or not node_b:
        print(f"⚠ 节点不存在：{node_a_name} 或 {node_b_name}")
        return

    weight = graph.get_edge_weight(node_a["id"], node_b["id"])
    if weight:
        with graph.driver.session() as session:
            session.run(
                """
                MATCH (a:Node {id: $id_a})-[r:RELATED]-(b:Node {id: $id_b})
                SET r.weight = r.weight + $amount
                """,
                {"id_a": node_a["id"], "id_b": node_b["id"], "amount": amount},
            )
        print(f"✓ 加强 {node_a_name} ↔ {node_b_name}: {weight:.3f} → {weight + amount:.3f}")
    else:
        print(f"ℹ {node_a_name} 和 {node_b_name} 没有直接连接")


def get_stats(graph: MemoryGraph = None) -> dict:
    if graph is None:
        graph = get_graph()

    stats = graph.stats()

    with graph.driver.session() as session:
        avg_weight = session.run(
            """
            MATCH ()-[r:RELATED]->()
            RETURN avg(r.weight) as avg
            """
        ).single()["avg"]

        strongest = session.run(
            """
            MATCH (a:Node)-[r:RELATED]-(b:Node)
            RETURN a.name, b.name, r.weight
            ORDER BY r.weight DESC
            LIMIT 1
            """
        ).single()

        weakest = session.run(
            """
            MATCH (a:Node)-[r:RELATED]-(b:Node)
            RETURN a.name, b.name, r.weight
            ORDER BY r.weight ASC
            LIMIT 1
            """
        ).single()

    return {
        **stats,
        "avg_weight": round(avg_weight, 3) if avg_weight else 0,
        "strongest_connection": (
            f"{strongest['a.name']} ↔ {strongest['b.name']} ({strongest['r.weight']:.2f})"
            if strongest
            else None
        ),
        "weakest_connection": (
            f"{weakest['a.name']} ↔ {weakest['b.name']} ({weakest['r.weight']:.2f})"
            if weakest
            else None
        ),
    }
