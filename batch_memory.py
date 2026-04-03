"""
batch_memory.py - 按会话缓冲多轮对话，满 N 轮后一次性增量抽取并写入 Neo4j。

与 config 中 MEMORY_BATCH_*、CHAT_HISTORY_MAX_TURNS 配合使用。
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import (
    LLM_API_BASE,
    LLM_MODEL,
    MEMORY_BATCH_KEEP_PAIRS,
    MEMORY_BATCH_TURNS,
    MEMORY_IDLE_FLUSH_SECONDS,
    get_llm_api_key,
)
from store import store_memory


def _parse_batch_json(text: str) -> Dict[str, Any]:
    """解析批量入库 LLM 输出（不要求 entities 等记忆分析字段）。"""
    s = text.strip()
    if "```" in s:
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(1).strip())
                if isinstance(obj, dict):
                    return obj
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
                            "memories" in obj or "digest" in obj
                        ):
                            return obj
                    except json.JSONDecodeError:
                        break
    raise ValueError("无可用批量 JSON")

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_buffers: Dict[str, "SessionBuffer"] = {}


@dataclass
class SessionBuffer:
    """每个 session 一对 user/assistant 为「一轮」。"""

    pairs: List[Tuple[str, str]] = field(default_factory=list)
    digest: str = ""
    last_activity: float = field(default_factory=time.time)  # 最后活动时间戳


def append_pair(session_id: str, user: str, assistant: str) -> None:
    if not session_id:
        return
    u, a = user.strip(), assistant.strip()
    if not u or not a:
        return
    with _lock:
        if session_id not in _buffers:
            _buffers[session_id] = SessionBuffer()
        _buffers[session_id].pairs.append((u, a))
        _buffers[session_id].last_activity = time.time()  # 更新活动时间戳


def pair_count(session_id: str) -> int:
    with _lock:
        s = _buffers.get(session_id)
        return len(s.pairs) if s else 0


def _format_transcript(pairs: List[Tuple[str, str]]) -> str:
    lines = []
    for i, (u, a) in enumerate(pairs, start=1):
        lines.append(f"【第{i}轮】我：{u}\n助手：{a}")
    return "\n\n".join(lines)


def _llm_batch_extract(
    transcript: str, digest: str
) -> Tuple[str, List[Tuple[Optional[str], str]]]:
    api_key = get_llm_api_key()
    if not api_key:
        logger.warning("批量入库跳过：无 API 密钥")
        return digest or "", []

    d = (digest or "").strip() or "（尚无已入库摘要）"
    system = (
        "你是记忆图谱批量入库助手。只输出一个 JSON 对象，不要 Markdown。"
        "已入库摘要（不要在 memories 中重复相同事实，只写相对摘要的新增信息）：\n"
        f"{d}\n\n"
        "下面是连续多轮对话。请做增量提取：输出 JSON，字段为 "
        '`digest`（1～4 句中文，合并更新后的摘要，供下一批对照）与 '
        '`memories`（数组：每项推荐对象 '
        '`{"summary":"≤24字短标题（供图节点显示名）","text":"完整备忘叙述（供分析与 full_text）"}；'
        "也可简化为字符串，但字符串会整段进分析，节点名会截断）。无新增则 []）。"
        "不要重复摘要里已有的事实。"
    )
    user = f"多轮对话转写：\n\n{transcript}"

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": 3072,
        "temperature": 0.25,
    }
    data_json = {**data, "response_format": {"type": "json_object"}}

    try:
        r = requests.post(url, headers=headers, json=data_json, timeout=180)
        if r.status_code >= 400:
            r = requests.post(url, headers=headers, json=data, timeout=180)
        r.raise_for_status()
        msg = r.json()["choices"][0]["message"]
        blob = "\n".join(
            str(t) for t in (msg.get("content"), msg.get("reasoning_content")) if t
        )
        raw = _parse_batch_json(blob)
    except Exception as e:
        logger.exception("批量抽取 LLM 失败：%s", e)
        return d, []

    new_digest = raw.get("digest")
    if isinstance(new_digest, str) and new_digest.strip():
        new_digest = new_digest.strip()
    else:
        new_digest = d

    mem = raw.get("memories") or raw.get("memory") or []
    if isinstance(mem, str):
        mem = [mem]
    if not isinstance(mem, list):
        mem = []
    out_items: List[Tuple[Optional[str], str]] = []
    for item in mem:
        if isinstance(item, str) and item.strip():
            out_items.append((None, item.strip()))
        elif isinstance(item, dict):
            detail = item.get("text") or item.get("content") or item.get("detail")
            summary = item.get("summary") or item.get("title") or item.get("label")
            if detail and str(detail).strip():
                s = str(summary).strip() if summary else None
                out_items.append((s or None, str(detail).strip()))
    return new_digest, out_items[:16]


def _run_batch_store(pairs: List[Tuple[str, str]], digest: str) -> str:
    """
    批量写入核心逻辑：
    1. 格式化 transcript
    2. LLM 一次性批量抽取所有 memories（带 digest 去重）
    3. 对所有 memories 的 text 做一次批量分析（而非每条单独调用）
    4. 批量写入 Neo4j
    """
    if not pairs:
        return digest
    transcript = _format_transcript(pairs)
    new_digest, memories = _llm_batch_extract(transcript, digest)

    # 优化：批量分析所有 memories，减少 LLM 调用
    # 收集所有待分析的文本
    memory_items = []
    for disp, line in memories:
        if len(line) >= 3:
            memory_items.append((disp, line))

    if not memory_items:
        return new_digest

    # 使用批量分析接口（会自动处理缓存）
    from store import analyze_memories_batch
    lines = [item[1] for item in memory_items]
    analyses = analyze_memories_batch(lines)

    # 逐条写入 Neo4j（图操作无法批量，但分析已批量完成）
    for i, (disp, line) in enumerate(memory_items):
        try:
            # 传入预分析结果，避免 store_memory 再次调用 LLM
            _store_memory_with_analysis(line, display_name=disp, pre_analysis=analyses[i])
        except Exception as e:
            logger.exception("批量入库单条 store_memory 失败：%s…", line[:40])

    return new_digest


def flush_idle_sessions() -> int:
    """
    检查并 flush 空闲超过 MEMORY_IDLE_FLUSH_SECONDS 的会话。
    由后台定时任务调用（例如在 web_server 启动的后台线程中）。

    Returns:
        被 flush 的会话数量
    """
    if not MEMORY_IDLE_FLUSH_SECONDS:
        return 0

    flushed_count = 0
    now = time.time()
    sessions_to_flush = []

    with _lock:
        for session_id, buffer in _buffers.items():
            if buffer.pairs:  # 有未 flush 的数据
                idle_seconds = now - buffer.last_activity
                if idle_seconds >= MEMORY_IDLE_FLUSH_SECONDS:
                    sessions_to_flush.append(session_id)

    for session_id in sessions_to_flush:
        flush_session_remainder(session_id)
        flushed_count += 1

    if flushed_count > 0:
        logger.info("空闲超时触发：已 flush %d 个会话", flushed_count)

    return flushed_count


def _store_memory_with_analysis(
    text: str,
    display_name: Optional[str] = None,
    pre_analysis: Optional[Dict[str, Any]] = None,
) -> str:
    """
    使用预分析结果存储记忆，避免重复 LLM 调用。

    Args:
        text: 记忆文本
        display_name: 可选的显示名称
        pre_analysis: 可选的预分析结果（来自批量分析）

    Returns:
        事件节点 ID
    """
    from memory_graph import get_graph
    from datetime import datetime
    from store import (
        _event_label_for_storage,
        _event_entity_tier,
        _event_link_weight,
        _co_occur_tier,
        _should_full_mesh,
    )
    from memory_text import guess_entity_node_type, normalize_temporal_entities
    from config import STORE_EMBEDDING

    # 使用预分析结果或实时分析
    if pre_analysis is not None:
        analysis = pre_analysis
    else:
        from store import analyze_memory
        analysis = analyze_memory(text)

    entities, entity_type_hints = normalize_temporal_entities(
        analysis["entities"],
        analysis.get("entity_types") or {},
        datetime.now().date(),
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
    }

    if STORE_EMBEDDING:
        from embeddings import embed_text
        vec = embed_text(text)
        if vec is not None:
            props["embedding"] = vec

    graph = get_graph()

    event_id = graph.create_node(
        name=_event_label_for_storage(text, display_name),
        node_type="event",
        properties=props,
    )

    ev_tier = _event_entity_tier(memory_kind)
    ew = _event_link_weight(salience, arousal)

    entity_ids = []
    for entity in entities:
        etype = guess_entity_node_type(entity, entity_type_hints.get(entity))
        entity_id = graph.get_or_create_node(entity, node_type=etype)
        entity_ids.append(entity_id)
        graph.create_edge(event_id, entity_id, weight=ew, tier=ev_tier)

    if len(entity_ids) > 1:
        co_tier = _co_occur_tier(memory_kind, salience)
        w_co = 0.5 * (0.55 + 0.45 * salience)
        if _should_full_mesh(memory_kind, salience):
            graph.connect_node_ids(entity_ids, weight=w_co, tier=co_tier)
        else:
            hub_id = entity_ids[0]
            for j in range(1, len(entity_ids)):
                graph.create_edge(hub_id, entity_ids[j], weight=w_co * 0.75, tier=co_tier)

    logger.info(
        "✓ 存储记忆：%s... (实体：%s | kind=%s | sal=%.2f)",
        text[:30],
        ", ".join(entities[:5]),
        memory_kind,
        salience,
    )
    return event_id


def batch_flush_worker(session_id: str) -> None:
    """
    后台任务：若缓冲满 MEMORY_BATCH_TURNS，取前 TURNS 轮做增量抽取写库，
    再从缓冲中丢弃前 (TURNS - KEEP) 轮，保留最后 KEEP 轮与下一段衔接。
    """
    if not session_id:
        return
    try:
        with _lock:
            s = _buffers.get(session_id)
            if not s or len(s.pairs) < MEMORY_BATCH_TURNS:
                return
            snap = list(s.pairs[:MEMORY_BATCH_TURNS])
            d = s.digest
            drop = MEMORY_BATCH_TURNS - MEMORY_BATCH_KEEP_PAIRS
            s.pairs = s.pairs[drop:]
        nd = _run_batch_store(snap, d)
        with _lock:
            if session_id in _buffers:
                _buffers[session_id].digest = nd
        logger.info("会话 %s… 批量入库完成，保留缓冲 %d 轮", session_id[:8], pair_count(session_id))
    except Exception:
        logger.exception("batch_flush_worker sid=%s", session_id)


def flush_session_remainder(session_id: str) -> None:
    """关页/显式结束：把当前未满批的缓冲也写入（仍走增量摘要逻辑）。"""
    if not session_id:
        return
    try:
        with _lock:
            s = _buffers.get(session_id)
            if not s or not s.pairs:
                return
            snap = list(s.pairs)
            d = s.digest
            s.pairs = []
            s.digest = ""
        _run_batch_store(snap, d)
        logger.info("会话 %s… 已 flush 剩余 %d 轮", session_id[:8], len(snap))
    except Exception:
        logger.exception("flush_session_remainder sid=%s", session_id)
