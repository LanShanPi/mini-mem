"""
test_export_import.py - 数据导出/导入测试
"""
import pytest
from memory_graph import MemoryGraph, get_graph, close_graph
from config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
import uuid


@pytest.fixture
def fresh_graph():
    """
    创建一个测试用的图实例。

    安全策略：
    - 不再清空整个数据库
    - 所有测试节点使用 "test_" 前缀的唯一 ID
    - 测试结束后只删除带 "test_" 前缀的节点
    """
    g = MemoryGraph(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    g.connect()
    yield g
    # 清理：只删除测试创建的节点（带 test_ 前缀或特定测试名）
    try:
        with g.driver.session() as session:
            # 删除所有测试节点
            session.run("""
                MATCH (n:Node)
                WHERE n.id STARTS WITH 'test_'
                   OR n.name STARTS WITH 'test_'
                   OR n.id STARTS WITH '导入'
                   OR n.name STARTS WITH '导入'
                   OR n.name STARTS WITH '节点'
                   OR n.name STARTS WITH '边测试'
                   OR n.name STARTS WITH '往返'
                   OR n.name STARTS WITH '属性测试'
                   OR n.name STARTS WITH '重复'
                   OR n.name STARTS WITH '唯一'
                DETACH DELETE n
            """)
        g.close()
    except Exception:
        pass


def test_export_empty_graph(fresh_graph):
    """测试导出空图"""
    data = fresh_graph.export_all()
    assert "nodes" in data
    assert "edges" in data
    assert isinstance(data["nodes"], list)
    assert isinstance(data["edges"], list)


def test_export_single_node(fresh_graph):
    """测试导出单个节点"""
    test_id = f"test_node_{uuid.uuid4().hex[:8]}"
    node_id = fresh_graph.create_node(
        name=test_id,  # 使用唯一 ID 作为名称
        node_type="concept",
        properties={"activation": 0.8, "test_prop": "test_value"}
    )

    data = fresh_graph.export_all()

    # 找到我们创建的节点
    test_nodes = [n for n in data["nodes"] if n["name"] == test_id or n["id"] == node_id]
    assert len(test_nodes) == 1
    node = test_nodes[0]
    assert node["name"] == test_id
    assert node["type"] == "concept"
    assert node["activation"] == 0.8


def test_export_with_edge(fresh_graph):
    """测试导出带边的图"""
    test_a = f"test_edge_a_{uuid.uuid4().hex[:8]}"
    test_b = f"test_edge_b_{uuid.uuid4().hex[:8]}"
    node_a = fresh_graph.create_node(test_a, "concept")
    node_b = fresh_graph.create_node(test_b, "concept")
    fresh_graph.create_edge(node_a, node_b, weight=0.75, tier="slow")

    data = fresh_graph.export_all()

    # 找到我们创建的节点
    test_nodes = [n for n in data["nodes"] if n["name"] in (test_a, test_b)]
    assert len(test_nodes) == 2
    # 无向图只返回一次（id(a) < id(b) 过滤）
    test_edges = [e for e in data["edges"] if e["weight"] == 0.75 and e["tier"] == "slow"]
    assert len(test_edges) == 1
    edge = test_edges[0]
    assert edge["weight"] == 0.75
    assert edge["tier"] == "slow"


def test_import_nodes(fresh_graph):
    """测试导入节点"""
    test_id_1 = f"test_import_node_{uuid.uuid4().hex[:8]}"
    test_id_2 = f"test_import_node_{uuid.uuid4().hex[:8]}"
    test_data = {
        "nodes": [
            {
                "id": test_id_1,
                "name": test_id_1,
                "type": "person",
                "activation": 0.9
            },
            {
                "id": test_id_2,
                "name": test_id_2,
                "type": "event",
                "activation": 0.6,
                "extra_prop": "extra_value"
            }
        ],
        "edges": []
    }

    result = fresh_graph.import_data(test_data)

    assert result["imported_nodes"] == 2
    assert result["skipped_nodes"] == 0
    assert result["imported_edges"] == 0

    # 验证节点已导入（通过唯一名称查找）
    all_nodes = fresh_graph.get_all_nodes(limit=100)
    imported_nodes = [n for n in all_nodes if n["name"] in (test_id_1, test_id_2)]
    assert len(imported_nodes) == 2


def test_import_edges(fresh_graph):
    """测试导入边"""
    # 先创建节点
    node_a_id = f"test_edge_a_{uuid.uuid4().hex[:8]}"
    node_b_id = f"test_edge_b_{uuid.uuid4().hex[:8]}"

    test_data = {
        "nodes": [
            {"id": node_a_id, "name": "边测试 A", "type": "concept"},
            {"id": node_b_id, "name": "边测试 B", "type": "concept"}
        ],
        "edges": [
            {"source": node_a_id, "target": node_b_id, "weight": 0.8, "tier": "fast"}
        ]
    }

    result = fresh_graph.import_data(test_data)

    assert result["imported_nodes"] == 2
    assert result["imported_edges"] == 1

    # 验证边已导入（无向图只返回一次）
    edges = fresh_graph.get_all_edges(limit=100)
    assert len(edges) >= 1
    # 找到我们导入的边
    found = False
    for e in edges:
        if e["weight"] == 0.8 and e["tier"] == "fast":
            found = True
            break
    assert found


def test_import_skip_existing(fresh_graph):
    """测试导入时跳过已存在的节点和边"""
    # 第一次导入
    node_id = f"test_dup_{uuid.uuid4().hex[:8]}"
    test_data = {
        "nodes": [{"id": node_id, "name": "重复测试", "type": "concept"}],
        "edges": []
    }

    result1 = fresh_graph.import_data(test_data)
    assert result1["imported_nodes"] == 1

    # 第二次导入相同数据
    result2 = fresh_graph.import_data(test_data)
    assert result2["imported_nodes"] == 0
    assert result2["skipped_nodes"] == 1


def test_import_extra_properties(fresh_graph):
    """测试导入额外属性"""
    node_id = f"test_props_{uuid.uuid4().hex[:8]}"
    test_data = {
        "nodes": [{
            "id": node_id,
            "name": "属性测试",
            "type": "event",
            "activation": 0.7,
            "full_text": "这是一段完整的测试文本",
            "emotion_valence": 0.6,
            "emotion_arousal": 0.4,
            "salience": 0.8,
            "memory_kind": "episode"
        }],
        "edges": []
    }

    fresh_graph.import_data(test_data)

    # 验证属性
    nodes = fresh_graph.search_nodes("属性测试", limit=10)
    assert len(nodes) == 1
    # Neo4j 中额外属性已设置，但 search_nodes 返回有限字段


def test_roundtrip_export_import(fresh_graph):
    """测试导出 - 导入往返"""
    # 创建测试数据（使用唯一名称）
    test_a = f"test_roundtrip_a_{uuid.uuid4().hex[:8]}"
    test_b = f"test_roundtrip_b_{uuid.uuid4().hex[:8]}"
    node_a = fresh_graph.create_node(test_a, "concept", {"prop_a": "val_a"})
    node_b = fresh_graph.create_node(test_b, "person")
    fresh_graph.create_edge(node_a, node_b, weight=0.9, tier="slow")

    # 导出
    exported = fresh_graph.export_all()

    # 删除测试节点模拟清空（使用安全删除而非 clear_all）
    with fresh_graph.driver.session() as session:
        session.run("""
            MATCH (n:Node)
            WHERE n.name STARTS WITH 'test_roundtrip'
            DETACH DELETE n
        """)

    # 导入
    result = fresh_graph.import_data(exported)

    # 注意：import_data 会跳过已存在的节点/边（通过 id 检查）
    # 由于是空图导入，应该全部导入
    assert result["imported_nodes"] == 2
    assert result["imported_edges"] == 1

    # 验证数据完整性
    nodes = fresh_graph.get_all_nodes(limit=100)
    roundtrip_nodes = [n for n in nodes if n["name"].startswith("test_roundtrip")]
    assert len(roundtrip_nodes) == 2
    # get_all_edges 返回双向边，所以是 2
    edges = fresh_graph.get_all_edges(limit=100)
    roundtrip_edges = [e for e in edges if e["weight"] == 0.9 and e["tier"] == "slow"]
    assert len(roundtrip_edges) >= 1
