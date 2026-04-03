"""
entity_normalization.py - 实体归一化、黑名单/白名单过滤

功能：
1. 从黑名单文件加载过滤词
2. 从白名单文件加载保留词
3. 实体归一化（人称统一、地名单位统一等）
4. 相似实体合并
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from config import (
    ENTITY_NORMALIZATION_ENABLED,
    ENTITY_BLACKLIST_FILE,
    ENTITY_WHITELIST_FILE,
    PROJECT_ROOT,
)


# ==================== 黑名单/白名单加载 ====================

def _load_wordlist(filepath: str) -> Set[str]:
    """加载词表文件，返回集合"""
    words: Set[str] = set()
    path = Path(PROJECT_ROOT) / filepath
    if not path.is_file():
        return words
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                words.add(s)
    except OSError:
        pass
    return words


_BLACKLIST = _load_wordlist(ENTITY_BLACKLIST_FILE)
_WHITELIST = _load_wordlist(ENTITY_WHITELIST_FILE)


# ==================== 人称归一化 ====================

# 人称代词映射：统一为第一/第二/第三人称
_PRONOUN_MAP = {
    # 第一人称
    "本人": "我",
    "自己": "我",
    "人家": "我",
    "小弟": "我",
    "小妹": "我",
    "老子": "我",
    "老娘": "我",
    "咱": "我",
    "咱们": "我们",
    # 第二人称
    "您": "你",
    "阁下": "你",
    "足下": "你",
    # 第三人称
    "此人": "他",
    "那人": "他",
    "她": "她",  # 保留性别
    "它": "它",
    "牠": "它",
}

# 人称 + 称谓 归一化模式（注意：模式和中文之间没有空格）
_HONORIFIC_PATTERNS = [
    (r"^(.+?)先生$", r"\1"),
    (r"^(.+?)女士$", r"\1"),
    (r"^(.+?)小姐$", r"\1"),
    (r"^(.+?)夫人$", r"\1"),
    (r"^(.+?)太太$", r"\1"),
    (r"^(.+?)老师$", r"\1"),
    (r"^(.+?)教授$", r"\1"),
    (r"^(.+?)博士$", r"\1"),
    (r"^(.+?)师傅$", r"\1"),
    (r"^(.+?)同志$", r"\1"),
]


# ==================== 地名归一化 ====================

# 地名单位层级（用于标准化）
_GEO_LEVELS = {
    "省": 1,
    "自治区": 1,
    "直辖市": 1,
    "市": 2,
    "地区": 2,
    "自治州": 2,
    "县": 3,
    "县级市": 3,
    "区": 3,
    "镇": 4,
    "乡": 4,
    "街道": 4,
    "村": 5,
    "社区": 5,
}


# ==================== 机构名归一化 ====================

_ORG_SUFFIXES = {
    "有限公司": "公司",
    "有限责任公司": "公司",
    "股份有限公司": "公司",
    "集团有限公司": "集团",
    "责任公司": "公司",
    "事务所": "所",
    "研究所": "所",
    "设计院": "院",
    "科学院": "院",
    "大学": "大学",
    "学院": "学院",
    "学校": "学校",
}


# ==================== 时间归一化 ====================

_TIME_RELATIVE_MAP = {
    "今天": "今日",
    "明天": "明日",
    "昨天": "昨日",
    "后天": "后日",
    "大后天": "三日后",
    "大前天": "三日前",
    "上周": "上周",
    "本周": "本周",
    "下周": "下周",
    "最近": "近日",
    "近期": "近日",
    "前些天": "日前",
    "过几天": "数日后",
}


def normalize_pronoun(entity: str) -> str:
    """归一化人称代词"""
    return _PRONOUN_MAP.get(entity, entity)


def normalize_honorific(entity: str) -> str:
    """归一化称谓（去掉先生/女士等后缀）"""
    for pattern, repl in _HONORIFIC_PATTERNS:
        match = re.match(pattern, entity)
        if match:
            return match.group(1)
    return entity


def normalize_geo(entity: str) -> str:
    """
    归一化地名：
    - 保留核心地名
    - 统一单位后缀
    """
    # 查找最长的地名单位后缀
    max_len = 0
    suffix = ""
    for s in _GEO_LEVELS:
        if entity.endswith(s) and len(s) > max_len:
            max_len = len(s)
            suffix = s

    if suffix:
        core = entity[:-max_len]
        # 保留核心地名 + 标准单位
        return core + suffix

    return entity


def normalize_org(entity: str) -> str:
    """
    归一化机构名（按后缀长度排序，优先匹配最长的后缀）。
    """
    # 按后缀长度排序，确保优先匹配最长的（如"股份有限公司"比"有限公司"长）
    sorted_suffixes = sorted(_ORG_SUFFIXES.items(), key=lambda x: -len(x[0]))
    for long_suffix, short_suffix in sorted_suffixes:
        if entity.endswith(long_suffix):
            return entity[:-len(long_suffix)] + short_suffix
    return entity


def normalize_time(entity: str) -> str:
    """归一化时间词"""
    return _TIME_RELATIVE_MAP.get(entity, entity)


def is_blacklisted(entity: str) -> bool:
    """检查实体是否在黑名单中"""
    if not ENTITY_NORMALIZATION_ENABLED:
        return False

    # 精确匹配
    if entity in _BLACKLIST:
        return True

    # 部分匹配（实体包含黑名单词）
    for black in _BLACKLIST:
        if len(black) >= 2 and black in entity:
            # 但如果在白名单中，则保留
            if any(white in entity for white in _WHITELIST):
                continue
            return True

    return False


def is_whitelisted(entity: str) -> bool:
    """检查实体是否在白名单中"""
    if entity in _WHITELIST:
        return True

    for white in _WHITELIST:
        if entity.endswith(white):
            return True

    return False


def normalize_entity(entity: str, entity_type: Optional[str] = None) -> str:
    """
    实体归一化主函数

    Args:
        entity: 原始实体
        entity_type: 可选的实体类型（person, place, org, time, concept）

    Returns:
        归一化后的实体
    """
    if not ENTITY_NORMALIZATION_ENABLED:
        return entity

    original = entity
    entity = entity.strip()

    # 黑名单实体过滤（返回空字符串表示应被过滤）
    if is_blacklisted(entity):
        return ""

    # 根据类型归一化（白名单检查在归一化之后，避免过早返回）
    if entity_type == "person" or len(entity) <= 3:
        entity = normalize_pronoun(entity)
        entity = normalize_honorific(entity)

    if entity_type == "place" or any(s in entity for s in _GEO_LEVELS):
        entity = normalize_geo(entity)

    if entity_type == "org" or any(
        s in entity for s in _ORG_SUFFIXES
    ):
        entity = normalize_org(entity)

    if entity_type == "time" or entity in _TIME_RELATIVE_MAP:
        entity = normalize_time(entity)

    # 去除首尾标点
    entity = re.sub(r'^[\s"\'"\'<>,;:,.!?!.!?]+|[\s"\'"\'<>,;:,.!?!.!?]+$', "", entity)

    # 如果归一化后为空或太短，返回空
    # 但人称代词"我"、"你"、"他"等是有效实体，保留单字人称
    if len(entity) < 2:
        # 保留常见人称代词
        if entity in ("我", "你", "他", "她", "它"):
            return entity
        return ""

    return entity


def normalize_entities(
    entities: List[str],
    entity_types: Optional[Dict[str, str]] = None,
) -> List[str]:
    """
    批量归一化实体列表

    Args:
        entities: 实体列表
        entity_types: 可选的实体类型字典 {entity: type}

    Returns:
        归一化后的实体列表（已去重和过滤）
    """
    if not ENTITY_NORMALIZATION_ENABLED:
        return entities

    normalized: List[str] = []
    seen: Set[str] = set()

    for entity in entities:
        etype = entity_types.get(entity) if entity_types else None
        norm = normalize_entity(entity, etype)

        if norm and norm not in seen:
            normalized.append(norm)
            seen.add(norm)

    return normalized


def merge_similar_entities(
    entities: List[Tuple[str, float]],
    threshold: float = 0.85,
) -> List[Tuple[str, float]]:
    """
    合并相似实体（基于字符串相似度）

    Args:
        entities: [(实体，分数), ...]
        threshold: 相似度阈值

    Returns:
        合并后的实体列表
    """

    def similarity(a: str, b: str) -> float:
        """计算两个字符串的 Jaccard 相似度"""
        set_a = set(a)
        set_b = set(b)
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    merged: List[Tuple[str, float]] = []
    used: Set[int] = set()

    for i, (entity_i, score_i) in enumerate(entities):
        if i in used:
            continue

        similar_indices = [i]
        for j, (entity_j, score_j) in enumerate(entities):
            if j <= i or j in used:
                continue
            if similarity(entity_i, entity_j) >= threshold:
                similar_indices.append(j)

        # 合并相似实体，取最高分
        best_entity = entities[i][0]
        best_score = score_i
        for j in similar_indices[1:]:
            if entities[j][1] > best_score:
                best_entity = entities[j][0]
                best_score = entities[j][1]

        merged.append((best_entity, best_score))
        for j in similar_indices:
            used.add(j)

    return merged
