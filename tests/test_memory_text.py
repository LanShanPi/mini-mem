"""纯函数测试：召回切词与实体类型启发式（不依赖 Neo4j）。"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from datetime import date

from memory_text import (
    guess_entity_node_type,
    normalize_temporal_entities,
    recall_query_tokens,
    resolve_temporal_entity_name,
)


def test_recall_tokens_chinese_not_only_whitespace():
    toks = recall_query_tokens("我今天头疼得厉害")
    assert "今天" in toks
    assert "头疼" in toks
    assert len(toks) >= 2


def test_recall_tokens_english_phrase():
    toks = recall_query_tokens("check Neo4j status")
    assert any("Neo4j" in t for t in toks)


def test_guess_time_and_place():
    assert guess_entity_node_type("2024-03-01") == "time"
    assert guess_entity_node_type("今天") == "time"
    assert guess_entity_node_type("南京西路", None) == "place"


def test_guess_llm_override():
    assert guess_entity_node_type("苹果", "organization") == "organization"


def test_resolve_today_to_iso():
    ref = date(2026, 3, 15)
    assert resolve_temporal_entity_name("今天", ref) == "2026-03-15"
    assert resolve_temporal_entity_name("昨天", ref) == "2026-03-14"
    assert resolve_temporal_entity_name("头疼", ref) == "头疼"


def test_normalize_temporal_entities_dedupe():
    ref = date(2026, 1, 1)
    ent, types = normalize_temporal_entities(
        ["今天", "头疼", "今天"],
        {"今天": "time", "头疼": "concept"},
        ref_date=ref,
    )
    assert ent == ["2026-01-01", "头疼"]
    assert types.get("2026-01-01") == "time"


def test_recall_tokens_include_iso_when_today_in_query():
    toks = recall_query_tokens("我今天头疼", ref_date=date(2026, 5, 20))
    assert "2026-05-20" in toks


if __name__ == "__main__":
    test_recall_tokens_chinese_not_only_whitespace()
    test_recall_tokens_english_phrase()
    test_guess_time_and_place()
    test_guess_llm_override()
    test_resolve_today_to_iso()
    test_normalize_temporal_entities_dedupe()
    test_recall_tokens_include_iso_when_today_in_query()
    print("test_memory_text: OK")
