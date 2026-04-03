"""
store.py - 记忆存储：实体 + 情绪/显著性/类型，差异化连边与可选向量
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import date, datetime
import re
import json
import requests

from entity_normalization import normalize_entities, is_blacklisted, is_whitelisted
from memory_graph import get_graph, MemoryGraph
from memory_text import (
    guess_entity_node_type,
    normalize_llm_entity_type,
    normalize_temporal_entities,
)
from config import (
    DEFAULT_EDGE_WEIGHT,
    ENTITY_EXTRACTOR,
    MAX_ENTITIES,
    LLM_API_BASE,
    LLM_MODEL,
    SALIENCE_FULL_MESH,
    SKIP_MESH_KINDS,
    STORE_EMBEDDING,
    get_llm_api_key,
    ENTITY_NORMALIZATION_ENABLED,
)

_VALID_KINDS = frozenset(
    {"episode", "fact", "commitment", "preference", "meta", "smalltalk"}
)


def simple_extract_entities(text: str) -> List[str]:
    """规则实体：日期时间正则 + 常见时间词（不靠带空格的伪正则，避免匹配不到[今天]）。"""
    entities: List[str] = []
    time_patterns = [
        r"\d{4}-\d{2}-\d{2}",
        r"\d{1,2}:\d{2}",
    ]
    for pattern in time_patterns:
        entities.extend(re.findall(pattern, text))

    time_phrases = (
        "今天", "明天", "昨天", "后天", "上周", "本周", "下周", "最近", "刚才", "昨晚",
        "上午", "下午", "晚上", "周末", "月初", "月底", "去年",
    )
    for p in time_phrases:
        if p in text:
            entities.append(p)

    text_no_time = text
    for pattern in time_patterns:
        text_no_time = re.sub(pattern, "", text_no_time)
    for p in time_phrases:
        text_no_time = text_no_time.replace(p, "")

    chinese_words = re.findall(r"[\u4e00-\u9fa5]{2,4}", text_no_time)
    entities.extend(chinese_words)

    common_words = {
        "的", "了", "是", "在", "我", "你", "他", "我们", "你们", "他们",
        "说", "想", "可以", "什么", "怎么", "一个", "这个", "那个",
    }
    entities = [e for e in entities if e not in common_words]

    seen = set()
    unique_entities: List[str] = []
    for e in entities:
        if e not in seen:
            seen.add(e)
            unique_entities.append(e)

    return unique_entities[:MAX_ENTITIES]


_BAD_EXACT_PHRASES = frozenset({
    "用户提到", "用户表示", "用户说", "比较喜欢", "有点喜欢", "比较想",
    "这件事", "这个事情", "那个事情", "这种情况", "那个情况", "这种事",
    "那种事", "这些事", "那些事", "一方面", "另一方面", "一般来说",
    "说实话", "事实上", "实际上", "问题是", "总之", "然而", "而且",
    "并且", "所以", "但是", "不过", "也许", "大概", "恐怕", "反正",
    "我觉得", "我认为", "我感觉", "我想说", "我觉得说",
})

_BAD_PREFIX_PATTERNS = re.compile(
    r"^(比较 | 有点 | 有些 | 有些许 | 可能 | 也许 | 应该 | 好像 | 似乎 | 大概 | 恐怕 | 反正 | 总之 | 所以 | 但是 | 不过 | 然而 | 而且 | 并且)",
    re.IGNORECASE,
)

_BAD_SENTENCE_PATTERNS = re.compile(
    r"^(我 | 你 | 他 | 她 | 它 | 我们 | 你们 | 他们 | 用户 | 助手 | 人家 | 大家 | 各位)"
    r"(说 | 提到 | 表示 | 觉得 | 认为 | 回复 | 问 | 讲 | 提问 | 说明 | 告诉 | 回答 | 回应 | 喊道)",
    re.IGNORECASE,
)

_NOUN_PATTERNS = re.compile(
    r"^[\u4e00-\u9fff0-9a-zA-Z]"  # 必须以字母/数字/汉字开头
    r".*"
    r"[\u4e00-\u9fff0-9a-zA-Z+\-_/]$"  # 必须以字母/数字/汉字结尾
)

# 常见无意义后缀/片段
_MEANINGLESS_SUFFIXES = frozenset({
    "味咖啡", "味茶", "味的", "的事情", "的情况", "的问题",
})


def _is_bad_entity_phrase(s: str) -> bool:
    """
    过滤口语元信息/句式碎片，避免把[用户提到][比较喜欢]当实体。
    只保留名词性片段：人名、地名、组织、时间、具体概念/物品。
    """
    if not s:
        return True
    t = s.strip()

    # 长度检查
    if len(t) < 2 or len(t) > 28:
        return True

    # 包含明显标注/结构符号的片段
    if any(x in t for x in ("[", "]", "：", "\n", "\t", "\"", "'", "[", "]", "[", "]")):
        return True

    # 人称 + 说话行为 / 句式元信息
    if _BAD_SENTENCE_PATTERNS.match(t):
        return True

    # 明显不是实体的抽象句式词
    if t in _BAD_EXACT_PHRASES:
        return True

    # 连词/副词开头的碎片
    if _BAD_PREFIX_PATTERNS.match(t):
        return True

    # 无意义后缀片段（但允许更具体的长短语，如「水果味咖啡」）
    for suffix in _MEANINGLESS_SUFFIXES:
        if t == suffix:
            return True
        # 仅当超出部分很短且无实际意义时才过滤
        if t.endswith(suffix) and len(t) <= len(suffix) + 1:
            return True

    # 必须看起来像名词性短语：以实词开头和结尾
    if not _NOUN_PATTERNS.match(t):
        return True

    # 排除纯标点/空白（Python re 不支持\p{P}，用常见标点范围替代）
    if re.match(r"^[\s\W_]+$", t):
        return True

    # 排除纯功能词（的、了、是等）
    if t in {"的", "了", "是", "在", "有", "和", "与", "或", "就", "都", "也", "还"}:
        return True

    return False


def _compress_entities(entities: List[str], max_n: int = MAX_ENTITIES) -> List[str]:
    """Normalization + dedup + containment compression."""
    # 抽象/元概念词，优先过滤
    _ABSTRACT_CONCEPTS = frozenset({
        "事情", "情况", "问题", "方式", "方法", "东西", "对象", "内容", "形式",
        "状态", "程度", "水平", "标准", "结果", "过程", "原因", "目的", "意义",
        "态度", "想法", "看法", "意见", "建议", "计划", "安排", "活动", "行为",
        "现象", "趋势", "方向", "目标", "任务", "工作", "事业", "产业", "行业",
        "领域", "范围", "程度", "级别", "层次", "阶段", "环节", "步骤", "程序",
        "系统", "平台", "渠道", "资源", "信息", "数据", "技术", "能力", "水平",
        "质量", "数量", "规模", "速度", "效率", "效益", "效果", "作用", "功能",
        "角色", "身份", "地位", "关系", "结构", "体系", "制度", "机制", "政策",
        "措施", "办法", "策略", "方案", "模式", "风格", "特点", "特性", "特征",
        "印象", "感觉", "感情", "情绪", "心情", "精神", "意识", "思维", "思想",
        "理念", "观念", "观点", "立场", "原则", "信念", "信仰", "理想", "梦想",
        "希望", "愿望", "期待", "期望", "要求", "需求", "需要", "必要", "重要",
        "关键", "重点", "核心", "基础", "前提", "条件", "环境", "背景", "场景",
        "话题", "主题", "题目", "标题", "名称", "名字", "名义", "名义上", "实际上",
    })

    cleaned: List[str] = []
    for e in entities:
        t = str(e or "").strip()
        if not t:
            continue
        t = re.sub(r'^[\s""""<>,;:,.!?!?]+|[\s""""<>,;:,.!?!?]+$', "", t)
        if _is_bad_entity_phrase(t):
            continue
        # 排除纯抽象概念词（但保留具体复合词如[水果味咖啡]）
        if t in _ABSTRACT_CONCEPTS and len(t) <= 4:
            continue
        if t not in cleaned:
            cleaned.append(t)

    kept: List[str] = []
    for e in cleaned:
        skip = False
        for i, k in enumerate(list(kept)):
            if e == k:
                skip = True
                break
            # e 更具体：替换旧的短片段
            if len(e) >= 3 and k in e and len(e) > len(k):
                kept[i] = e
                skip = True
                break
            # 旧的更具体：跳过 e
            if len(k) >= 3 and e in k and len(k) > len(e):
                skip = True
                break
        if not skip:
            kept.append(e)
        if len(kept) >= max_n:
            break
    return kept[:max_n]


def _extract_json_object(text: str) -> Dict[str, Any]:
    """从模型输出中尝试解析 JSON 对象（跳过思考段落里的无效大括号）。"""
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1).strip())
            except json.JSONDecodeError:
                pass

    starts = [i for i, c in enumerate(s) if c == "{"]
    for start in reversed(starts):
        depth = 0
        for i in range(start, len(s)):
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    chunk = s[start : i + 1]
                    try:
                        obj = json.loads(chunk)
                        if isinstance(obj, dict) and (
                            "entities" in obj or "memory_kind" in obj or "salience" in obj
                        ):
                            return obj
                    except json.JSONDecodeError:
                        break
    raise ValueError("无可用 JSON 对象")


def _normalize_analysis(raw: Dict[str, Any], text: str) -> Dict[str, Any]:
    """标准化 LLM 分析结果，包含实体归一化"""
    entities = raw.get("entities") or []
    if isinstance(entities, str):
        entities = [x.strip() for x in entities.split(",") if x.strip()]
    if not isinstance(entities, list):
        entities = []

    entity_type_hints: Dict[str, str] = {}
    flat_entities: List[str] = []
    for e in entities:
        if isinstance(e, dict):
            t = e.get("text") or e.get("name") or e.get("entity")
            if not t:
                continue
            t = str(t).strip()
            if not t:
                continue
            typ = normalize_llm_entity_type(e.get("type"))
            if typ:
                entity_type_hints[t] = typ
            flat_entities.append(t)
        elif e is not None:
            flat_entities.append(str(e).strip())

    # 基础过滤
    flat_entities = _compress_entities(flat_entities, max_n=MAX_ENTITIES)

    # 实体归一化（新增）
    if ENTITY_NORMALIZATION_ENABLED and flat_entities:
        # 使用归一化模块进行二次过滤和标准化
        normalized = normalize_entities(flat_entities, entity_type_hints)
        # 过滤掉归一化后为空的实体
        flat_entities = [e for e in normalized if e and not is_blacklisted(e)]
        # 更新类型提示
        for e in flat_entities:
            if e not in entity_type_hints and is_whitelisted(e):
                # 白名单实体，推断类型
                if any(e.endswith(s) for s in ["省", "市", "县", "区", "镇", "村"]):
                    entity_type_hints[e] = "place"
                elif any(e.endswith(s) for s in ["公司", "集团", "学校", "医院", "银行"]):
                    entity_type_hints[e] = "org"

    emo = raw.get("emotion")
    if isinstance(emo, dict):
        try:
            valence = float(emo.get("valence", 0.0))
        except (TypeError, ValueError):
            valence = 0.0
        try:
            arousal = float(emo.get("arousal", 0.0))
        except (TypeError, ValueError):
            arousal = 0.0
    elif isinstance(emo, str):
        e_low = emo.lower()
        valence = 0.0
        arousal = 0.25
        if any(x in e_low for x in ("焦虑", "紧张", "愤怒", "害怕", "excited", "angry")):
            arousal = 0.75
            valence = -0.35
        if any(x in e_low for x in ("积极", "开心", "happy", "positive")):
            valence = 0.45
            arousal = max(arousal, 0.45)
        if "neutral" in e_low or "中性" in emo:
            valence = 0.0
            arousal = 0.2
    else:
        valence = 0.0
        arousal = 0.0
    valence = max(-1.0, min(1.0, valence))
    arousal = max(0.0, min(1.0, arousal))

    try:
        salience = float(raw.get("salience", 0.5))
    except (TypeError, ValueError):
        salience = 0.5
    salience = max(0.0, min(1.0, salience))

    kind = str(raw.get("memory_kind", "episode")).strip().lower()
    if kind in ("task", "todo", "约定"):
        kind = "commitment"
    if kind not in _VALID_KINDS:
        kind = "episode"

    if not flat_entities:
        flat_entities = _compress_entities(simple_extract_entities(text), max_n=MAX_ENTITIES)

    return {
        "entities": flat_entities,
        "entity_types": entity_type_hints,
        "emotion_valence": valence,
        "emotion_arousal": arousal,
        "salience": salience,
        "memory_kind": kind,
    }


def simple_analyze_memory(text: str) -> Dict[str, Any]:
    """无 LLM：规则实体 + 中性情绪/默认显著性。"""
    exclam = text.count("！") + text.count("!")
    arousal = min(1.0, 0.12 + 0.08 * exclam)
    return {
        "entities": simple_extract_entities(text),
        "entity_types": {},
        "emotion_valence": 0.0,
        "emotion_arousal": arousal,
        "salience": 0.48,
        "memory_kind": "episode",
    }


def analyze_memory_with_llm(text: str) -> Dict[str, Any]:
    """LLM 结构化分析（支持 reasoning_content）。"""
    api_key = get_llm_api_key()
    if not api_key:
        print("  ⚠ 未配置 LLM API 密钥，使用规则分析")
        return simple_analyze_memory(text)

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    json_example = (
        '{"entities":[{"text":"小李","type":"person"},{"text":"星巴克","type":"place"},'
        '{"text":"头疼","type":"concept"}],"emotion":{"valence":-0.2,"arousal":0.4},'
        '"salience":0.65,"memory_kind":"episode"}'
    )
    data = {
        "model": LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是记忆结构化助手。只输出一个 JSON 对象，不要 Markdown、不要思考过程。"
                    '先理解：实体必须是"可长期复用的名词性节点"，如人名、地点、时间、药名、疾病、饮品、事件主题。'
                    '不要把句式、态度、副词、说话行为当实体。'
                    '错误示例（不要输出）：用户提到、比较喜欢、有点难受、味咖啡、我觉得。'
                    '优先保留更完整的短语：例如输出"水果味咖啡"，不要只输出"味咖啡"。'
                    'entities：优先用对象数组，每项 {"text":"","type":""}；'
                    'type 只能是 person|place|time|concept|organization|topic 之一；'
                    '若做不到对象数组，可退化为字符串数组（系统将仅用规则猜类型）。'
                    'emotion：{"valence":-1 到 1,"arousal":0 到 1}；'
                    'salience：0 到 1；'
                    'memory_kind：episode|fact|commitment|preference|meta|smalltalk。'
                    '宁少勿滥：只列对后续联想真正有用的实体，通常不超过 8 个。'
                    f"合法示例：{json_example}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前日期（请把[今天/昨天/明天]等相对日落到具体 YYYY-MM-DD，"
                    f"entities 里时间请用该格式）：{date.today().isoformat()}\n\n"
                    f"分析下面这句话，用于记忆图谱：\n{text}"
                ),
            },
        ],
        "max_tokens": 2048,
        "temperature": 0.2,
    }
    # OpenAI 兼容服务若支持 json 模式，可显著提高可解析率
    data_json = {**data, "response_format": {"type": "json_object"}}

    print(f"  🤖 记忆分析 LLM: {LLM_MODEL}")
    try:
        response = requests.post(url, headers=headers, json=data_json, timeout=120)
        if response.status_code >= 400:
            response = requests.post(url, headers=headers, json=data, timeout=120)
        response.raise_for_status()
        result = response.json()
        message = result["choices"][0]["message"]
        content = message.get("content")
        reasoning = message.get("reasoning_content")
        blob = "\n".join(str(t) for t in (content, reasoning) if t)
        if not blob.strip():
            return simple_analyze_memory(text)
        peek = (content or reasoning or "")[:120]
        print(f"  📝 LLM 输出（截断）：{peek}...")
        content_s = str(content).strip() if content else ""
        try:
            raw = _extract_json_object(blob)
        except (ValueError, json.JSONDecodeError):
            try:
                if not content_s:
                    raise ValueError("empty content")
                raw = _extract_json_object(content_s)
            except (ValueError, json.JSONDecodeError):
                raw = _extract_json_object(str(reasoning or ""))
        return _normalize_analysis(raw, text)
    except Exception as e:
        print(f"  ⚠ LLM 分析失败：{e}，回退规则")
        return simple_analyze_memory(text)


# 批量分析缓存：key=text 哈希，value=分析结果
_analyze_cache: Dict[str, Dict[str, Any]] = {}
_analyze_cache_maxsize = 256


def analyze_memory(text: str) -> Dict[str, Any]:
    """根据 ENTITY_EXTRACTOR 选择分析方式，带缓存。"""
    # 短文本缓存 key
    cache_key = text[:200] if len(text) < 2000 else None

    if cache_key and cache_key in _analyze_cache:
        return _analyze_cache[cache_key]

    if ENTITY_EXTRACTOR == "simple":
        result = simple_analyze_memory(text)
    elif ENTITY_EXTRACTOR == "hybrid":
        rule = simple_analyze_memory(text)
        llm = analyze_memory_with_llm(text)
        merged = list(dict.fromkeys(rule["entities"] + llm["entities"]))[:MAX_ENTITIES]
        result = {**llm, "entities": merged, "entity_types": llm.get("entity_types") or {}}
    else:
        result = analyze_memory_with_llm(text)

    # 写入缓存
    if cache_key:
        _analyze_cache[cache_key] = result
        # 简单 LRU：超出大小删除最旧
        if len(_analyze_cache) > _analyze_cache_maxsize:
            oldest_key = next(iter(_analyze_cache))
            del _analyze_cache[oldest_key]

    return result


def analyze_memories_batch(texts: List[str]) -> List[Dict[str, Any]]:
    """
    批量分析多段文本，优化 LLM 调用：
    - 先检查缓存
    - 对未命中的文本，尝试用一次 LLM 调用分析所有（如果 ENTITY_EXTRACTOR=simple）
    - 否则逐条分析

    Args:
        texts: 待分析的文本列表

    Returns:
        每段文本的分析结果列表
    """
    if not texts:
        return []

    results: List[Dict[str, Any]] = []
    uncached_texts: List[Tuple[int, str]] = []  # (原始索引，文本)

    # 第一步：检查缓存
    for i, text in enumerate(texts):
        cache_key = text[:200] if len(text) < 2000 else None
        if cache_key and cache_key in _analyze_cache:
            results.append(_analyze_cache[cache_key])
        else:
            results.append({})  # 占位
            uncached_texts.append((i, text))

    if not uncached_texts:
        return results

    # 第二步：逐条分析未缓存的文本
    # 未来可扩展：调用支持 batch 的 LLM 接口，一次分析多条
    for idx, text in uncached_texts:
        result = analyze_memory(text)
        results[idx] = result

    return results


def extract_entities(text: str) -> List[str]:
    """兼容旧 API：仅返回实体列表。"""
    return analyze_memory(text)["entities"]


def _event_entity_tier(memory_kind: str) -> str:
    if memory_kind in ("commitment", "preference"):
        return "slow"
    if memory_kind == "smalltalk":
        return "fast"
    return "normal"


def _co_occur_tier(memory_kind: str, salience: float) -> str:
    if memory_kind == "smalltalk" or salience < 0.38:
        return "fast"
    if memory_kind in ("commitment", "preference") and salience >= 0.55:
        return "slow"
    return "normal"


def _should_full_mesh(memory_kind: str, salience: float) -> bool:
    if memory_kind in SKIP_MESH_KINDS and salience < SALIENCE_FULL_MESH:
        return False
    return salience >= SALIENCE_FULL_MESH


def _event_link_weight(salience: float, arousal: float) -> float:
    base = 0.52 + 0.38 * salience + 0.22 * arousal
    return max(0.15, min(0.95, base))


_EVENT_NAME_MAX = 72


def _event_label_for_storage(text: str, display_name: Optional[str]) -> str:
    """
    图节点 `name`（便于浏览）；完整内容始终在 `full_text`。
    - 显式 display_name：批量抽取的短标题等。
    - `[对话]…` 旧转写：用用户首句作[对话·…]，避免节点名被助手客套话占满。
    """
    if display_name:
        dn = display_name.strip()
        if dn:
            if len(dn) > _EVENT_NAME_MAX:
                return dn[: _EVENT_NAME_MAX - 1].rstrip() + "…"
            return dn
    if text.startswith("[对话]"):
        m = re.match(r"[对话]我：(.+?)(?:\n助手：|\Z)", text, re.DOTALL)
        if m:
            u = re.sub(r"\s+", " ", m.group(1).strip())
            if u:
                prefix = "对话·"
                budget = _EVENT_NAME_MAX - len(prefix)
                if len(u) > budget:
                    u = u[: max(1, budget - 1)].rstrip() + "…"
                return (prefix + u)[:_EVENT_NAME_MAX]
    if len(text) > 50:
        return text[:50] + "..."
    return text


def store_memory(
    text: str,
    graph: Optional[MemoryGraph] = None,
    node_type: str = "event",
    extra_properties: Optional[dict] = None,
    display_name: Optional[str] = None,
) -> str:
    """存储一段记忆：写入情绪/显著性/类型，并按策略连边。"""
    if graph is None:
        graph = get_graph()

    analysis = analyze_memory(text)
    entities, entity_type_hints = normalize_temporal_entities(
        analysis["entities"],
        analysis.get("entity_types") or {},
        date.today(),
    )
    valence = analysis["emotion_valence"]
    arousal = analysis["emotion_arousal"]
    salience = analysis["salience"]
    memory_kind = analysis["memory_kind"]

    ts = datetime.now().isoformat()
    props = {
        "full_text": text,
        "timestamp": ts,
        "emotion_valence": valence,
        "emotion_arousal": arousal,
        "salience": salience,
        "memory_kind": memory_kind,
        **(extra_properties or {}),
    }

    if STORE_EMBEDDING:
        from embeddings import embed_text

        vec = embed_text(text)
        if vec is not None:
            props["embedding"] = vec

    event_id = graph.create_node(
        name=_event_label_for_storage(text, display_name),
        node_type=node_type,
        properties=props,
    )

    ev_tier = _event_entity_tier(memory_kind)
    ew = _event_link_weight(salience, arousal)

    entity_ids: List[str] = []
    for entity in entities:
        etype = guess_entity_node_type(entity, entity_type_hints.get(entity))
        entity_id = graph.get_or_create_node(entity, node_type=etype)
        entity_ids.append(entity_id)
        graph.create_edge(event_id, entity_id, weight=ew, tier=ev_tier)

    if len(entity_ids) > 1:
        names = entities[: len(entity_ids)]
        co_tier = _co_occur_tier(memory_kind, salience)
        w_co = DEFAULT_EDGE_WEIGHT * (0.55 + 0.45 * salience)
        if _should_full_mesh(memory_kind, salience):
            graph.connect_node_ids(entity_ids, weight=w_co, tier=co_tier)
        else:
            hub_id = entity_ids[0]
            for i in range(1, len(entity_ids)):
                graph.create_edge(
                    hub_id, entity_ids[i], weight=w_co * 0.75, tier=co_tier
                )

    entity_str = ", ".join(entities[:5])
    print(
        f"✓ 存储记忆：{text[:30]}... "
        f"(实体：{entity_str} | kind={memory_kind} | sal={salience:.2f})"
    )
    return event_id


def store_event(
    participants: List[str],
    concepts: List[str],
    timestamp: Optional[str] = None,
    emotion: float = 0.5,
    graph: Optional[MemoryGraph] = None,
) -> str:
    """存储结构化事件（沿用较慢衰减的边）。"""
    if graph is None:
        graph = get_graph()

    timestamp = timestamp or datetime.now().isoformat()
    event_summary = f"{'_'.join(participants[:2])}_{'_'.join(concepts[:2])}"
    event_id = graph.create_node(
        name=event_summary,
        node_type="event",
        properties={
            "participants": participants,
            "concepts": concepts,
            "timestamp": timestamp,
            "emotion": emotion,
            "emotion_valence": 0.0,
            "emotion_arousal": min(1.0, max(0.0, float(emotion))),
            "salience": 0.62,
            "memory_kind": "episode",
        },
    )

    person_ids: List[str] = []
    for person in participants:
        pid = graph.get_or_create_node(person, node_type="person")
        person_ids.append(pid)
        graph.create_edge(event_id, pid, weight=0.8, tier="normal")

    concept_ids: List[str] = []
    for concept in concepts:
        cid = graph.get_or_create_node(concept, node_type="concept")
        concept_ids.append(cid)
        graph.create_edge(event_id, cid, weight=0.6, tier="normal")

    time_id = graph.get_or_create_node(timestamp[:10], node_type="time")
    graph.create_edge(event_id, time_id, weight=0.5, tier="normal")

    all_ids = person_ids + concept_ids + [time_id]
    if len(all_ids) > 1:
        graph.connect_node_ids(all_ids, weight=DEFAULT_EDGE_WEIGHT, tier="normal")

    print(f"✓ 存储事件：{event_summary}")
    return event_id
