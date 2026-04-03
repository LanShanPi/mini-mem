"""
memory_text.py - 与记忆管线相关的纯文本工具（无 Neo4j 依赖，便于测试）

- 召回查询切词：中文不能依赖空白 split
- 实体节点类型启发式（LLM 可覆盖）
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Optional, Set, Tuple

# 相对「日历日」词 → 相对 ref_date 的偏移；写入图时换成具体日期节点，避免「任何一天都叫今天」混在一个点上
_REL_DAY_ENTITY_OFFSET: Dict[str, int] = {
    "今天": 0,
    "今日": 0,
    "昨天": -1,
    "昨日": -1,
    "前天": -2,
    "明天": 1,
    "明日": 1,
    "后天": 2,
}

# 召回：从用户话里拆出「可能命中图上节点名」的线索
_RECALL_STOP: Set[str] = frozenset(
    {
        "的", "了", "是", "在", "有", "和", "与", "或", "就", "都", "也", "还", "吗", "呢", "吧", "啊",
        "我", "你", "他", "她", "它", "我们", "你们", "他们", "自己", "大家", "谁", "什么", "怎么",
        "这个", "那个", "这样", "那样", "一个", "一些", "有点", "非常", "比较", "可以", "应该",
        "不是", "没有", "如果", "因为", "所以", "但是", "然后", "不过", "其实", "只是", "就是",
        "你好", "谢谢", "请问", "帮忙", "一下", "知道", "觉得", "认为", "感觉", "想", "说", "看",
    }
)

_VALID_NODE_TYPES = frozenset(
    {"person", "place", "time", "concept", "organization", "topic", "event"}
)

_PLACE_HINT = re.compile(
    r"(路|街|巷|弄|号|区|市|省|县|镇|村|大厦|广场|中心|站|机场|地铁|酒店|医院|学校|公园|楼|室|门店|店)$"
)
_ORG_HINT = re.compile(r"(公司|集团|有限|股份|大学|学院|银行|局|部|处|院|所|厂|店|科技)$")
_PERSON_SUFFIX = re.compile(r"(先生|女士|老师|医生|经理|主任|同学|哥|姐|总)$")


def recall_query_tokens(
    text: str, max_tokens: int = 32, ref_date: Optional[date] = None
) -> List[str]:
    """
    为 recall 生成入口词：英文按空白；中文连续汉字段内取 2～4 字滑窗 + 整段（适度截断）。
    若句中含「今天」等，会额外加入对应 YYYY-MM-DD，与按日期存储的节点对齐。
    """
    raw = (text or "").strip()
    if not raw:
        return []

    out: List[str] = []
    seen: Set[str] = set()

    def add(s: str) -> None:
        s = s.strip()
        if len(s) < 2:
            return
        if s in _RECALL_STOP:
            return
        if s not in seen:
            seen.add(s)
            out.append(s)

    for part in re.split(r"[\s\u3000,，.。!！?？;；:]+", raw):
        if 2 <= len(part) <= 64:
            add(part)

    if 2 <= len(raw) <= 56:
        add(raw)

    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", raw):
        if len(seg) > 42:
            seg = seg[:42]
        for n in (4, 3, 2):
            if len(seg) < n:
                continue
            for i in range(0, len(seg) - n + 1):
                add(seg[i : i + n])
                if len(out) >= max_tokens * 3:
                    break
            if len(out) >= max_tokens * 3:
                break
        if len(out) >= max_tokens * 3:
            break

    # 查询里若出现「今天」等，同时加入具体 YYYY-MM-DD，便于命中已按日期归一化存储的节点
    d = ref_date or date.today()
    for word, off in _REL_DAY_ENTITY_OFFSET.items():
        if word in raw:
            iso = (d + timedelta(days=off)).isoformat()
            if iso not in seen and len(iso) >= 2:
                seen.add(iso)
                out.append(iso)
                if len(out) >= max_tokens * 3:
                    break

    return out[:max_tokens]


def resolve_temporal_entity_name(name: str, ref_date: Optional[date] = None) -> str:
    """把「今天」「昨天」等换成 ref 当天的日历日期字符串；其它原样返回。"""
    s = (name or "").strip()
    if not s:
        return s
    d = ref_date or date.today()
    off = _REL_DAY_ENTITY_OFFSET.get(s)
    if off is not None:
        return (d + timedelta(days=off)).isoformat()
    return s


def normalize_temporal_entities(
    entities: List[str],
    entity_types: Optional[Dict[str, str]] = None,
    ref_date: Optional[date] = None,
) -> Tuple[List[str], Dict[str, str]]:
    """
    对实体列表做日期归一化并保序去重；把原 entity_types 从旧名映射到新名（仅首次出现保留类型）。
    """
    entity_types = entity_types or {}
    d = ref_date or date.today()
    pairs = [(e, resolve_temporal_entity_name(e, d)) for e in entities]
    seen: List[str] = []
    new_types: Dict[str, str] = {}
    for old_e, new_e in pairs:
        if not new_e:
            continue
        if new_e not in seen:
            seen.append(new_e)
            t = entity_types.get(old_e)
            if t:
                new_types[new_e] = t
        else:
            t = entity_types.get(old_e)
            if t and new_e not in new_types:
                new_types[new_e] = t
    return seen, new_types


def normalize_llm_entity_type(t: Optional[str]) -> Optional[str]:
    if not t or not isinstance(t, str):
        return None
    x = t.strip().lower()
    aliases = {
        "org": "organization",
        "company": "organization",
        "corp": "organization",
        "location": "place",
        "loc": "place",
        "date": "time",
        "when": "time",
    }
    x = aliases.get(x, x)
    return x if x in _VALID_NODE_TYPES else None


def guess_entity_node_type(name: str, llm_type: Optional[str] = None) -> str:
    """
    决定写入图时实体的 Node.type；LLM 给出的合法 type 优先，其次启发式。
    """
    hinted = normalize_llm_entity_type(llm_type)
    if hinted and hinted != "event":
        return hinted

    s = (name or "").strip()
    if not s:
        return "concept"

    if re.match(r"^\d{4}-\d{2}-\d{2}", s) or re.match(r"^\d{1,2}:\d{2}", s):
        return "time"

    time_words = (
        "今天", "明天", "昨天", "后天", "上周", "本周", "下周", "最近", "刚才", "昨晚",
        "上午", "下午", "晚上", "夜里", "清晨", "周末", "月初", "月底", "年初", "去年",
    )
    if s in time_words:
        return "time"
    if len(s) <= 12:
        for w in time_words:
            if len(w) >= 2 and w in s:
                return "time"

    if _PLACE_HINT.search(s) or any(x in s for x in ("星巴克", "咖啡馆", "办公室", "家里", "家中")):
        return "place"

    if _ORG_HINT.search(s) or any(x in s for x in ("有限公司", "股份有限公司")):
        return "organization"

    if _PERSON_SUFFIX.search(s) and 2 <= len(s) <= 12:
        return "person"

    if 2 <= len(s) <= 4 and re.match(r"^[\u4e00-\u9fff]{2,4}$", s):
        return "concept"

    return "concept"
