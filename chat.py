"""
交互助手：多轮对话 + 图记忆检索；可选把「整轮对话」写入记忆图（用户与助手各一句）。
"""
from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import (
    CHAT_HISTORY_MAX_TURNS,
    LLM_API_BASE,
    LLM_MODEL,
    MEMORY_BATCH_ENABLED,
    MEMORY_BATCH_TURNS,
    get_llm_api_key,
)

SYSTEM_PROMPT = """你是 MiniMem，用户的私人对话助手。

你会收到一些从记忆库检索到的片段（可能不全或不相关）。
- 记忆里有能对上号的，就自然提一句；对不上就不要硬扯，正常接话。

【输出格式 — 必须遵守】
- 只输出给用户看的对话正文：口语化中文，一两段即可，像朋友发消息。
- 严禁输出：Thinking Process、Analyze the Request、步骤编号（1. 2. …）、Draft、Final Polish、Self-Correction、英文长段分析、Markdown 标题、引号包一整段「模拟回复」。
- 不要先写推理再写回复；不要解释你在遵守什么规则。第一个字就应该是给用户的话。"""


def _session_local_time_hint() -> str:
    """给模型本地日期/星期，减少「我没日历」式冗长推理。"""
    now = datetime.now()
    wk = "一二三四五六日"[now.weekday()]
    return (
        f"【当前本地时间（用户问「今天」「周几」等请直接据此回答）】"
        f"{now.strftime('%Y-%m-%d')} 星期{wk}。"
    )


# 模型只吐 CoT、无 Final Polish / 无中文定稿时，不把整页英文推给前端
_FALLBACK_WHEN_COULD_NOT_STRIP_COT = (
    "糟糕，这一整条基本都是模型自己在心里推演，没截出能给你看的正常对话。"
    "麻烦你再用白话问一遍？比如「用两三句话说说我们刚才聊了啥」——我会按聊天记录接。"
)


def _content_blocks_to_text(content: Any) -> str:
    """兼容 OpenAI 兼容网关：content 可为 str、null，或 [{type,text}, ...]。"""
    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                t = item.get("text")
                if t is not None and str(t).strip():
                    parts.append(str(t).strip())
                    continue
                inner = item.get("content")
                if inner is not None:
                    sub = _content_blocks_to_text(inner)
                    if sub:
                        parts.append(sub)
            elif isinstance(item, str) and item.strip():
                parts.append(item.strip())
        return "\n".join(parts).strip()
    return str(content).strip() if str(content).strip() else ""


def _strip_explicit_think_tags(text: str) -> str:
    """
    硬过滤：部分模型会直接输出 <think>...</think> 或 <thinking>...</thinking>。
    这里先整块删除，再交给后续 CoT 清洗逻辑。
    """
    if not text:
        return text
    s = re.sub(r"(?is)<\s*(think|thinking)\b[^>]*>.*?<\s*/\s*\1\s*>", " ", text)
    # 某些网关会输出不闭合 <think>，把起始标签后的内容全部裁掉
    s = re.sub(r"(?is)<\s*(think|thinking)\b[^>]*>.*$", " ", s)
    # 也兜底处理实体转义标签
    s = re.sub(
        r"(?is)&lt;\s*(think|thinking)\b.*?(?:&lt;\s*/\s*\1\s*&gt;|$)",
        " ",
        s,
    )
    return s.strip()


def _looks_like_user_facing_chinese(s: str) -> bool:
    if not s or len(s.strip()) < 8:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    return cjk >= 8


def _line_looks_like_cot_header(line: str) -> bool:
    """从文件底部向上扫时，识别「仍在 CoT 区块里」的标题行。"""
    s = line.strip()
    sl = re.sub(r"^[\*\s#•]+", "", s.lower())
    if sl.startswith("thinking process"):
        return True
    if re.search(r"(?i)\bthinking\s+process\s*:", s):
        return True
    if re.search(
        r"(?i)\*\*role\*\*|\*\*input\*\*|\*\*constraints\*\*|\*\*memory\s+context\*\*"
        r"|\*\*user\s+input\*\*|\*\*context:\*\*|\*\*memory:\*\*",
        s,
    ):
        return True
    if re.search(
        r"(?i)drafting\s+(the\s+)?response|determine\s+intent|refining\s+for\s+tone",
        s,
    ):
        return True
    if re.match(r"^#{1,4}\s+\S", s):
        return True
    if re.match(r"^\d+\.\s+", s):
        cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
        if cjk < 4 and re.search(
            r"(?i)analyze|evaluate|draft|constraint|memory|role\b|determine|polish",
            s,
        ):
            return True
    return False


def _line_looks_like_cot_meta(line: str) -> bool:
    """模型自检尾注：*Wait*、*Final Plan*、*Correction* 等，不应当作用户可见正文。"""
    t = line.strip()
    low = t.lower()
    if re.search(
        r"(?i)^[\s\*•]*\*+\s*(wait\b|self[- ]?correction|final\s+plan|okay,\s*ready"
        r"|one\s+more\s+check|constraint\s+check|i\s+need\s+to|i\s+must|better\s+approach"
        r"|check\s+constraints|must\s+ensure|ready\s+to\s+output|correction\s*:"
        r"|actually\b|input\s+structure)",
        low,
    ):
        return True
    if re.match(
        r"(?i)^[\s\*•]*\*+\s*wait,\s*(no\.|looking|one\s+thing|constraint|i\s+need)",
        low,
    ):
        return True
    # 缩进列表里的 *Wait, looking at...*
    if re.match(r"(?i)^[\s\*•]*\*+\s*wait,\s*looking", low):
        return True
    return False


def _line_is_dense_english_cot(line: str) -> bool:
    """长段英文推理行：遇到则停止向上合并，保留已收集的尾部中文。"""
    s = line.strip()
    if len(s) < 28:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", s))
    return cjk < 4


def _para_starts_like_english_meta(para: str) -> bool:
    """Final Polish 节里常见的英文指令段首。"""
    first = para.strip().split("\n", 1)[0].strip().lower()
    if len(first) > 120:
        first = first[:120]
    if re.match(
        r"(?i)^(revised|make it|let\'?s go|okay,|actually,|wait,|the prompt|standard practice"
        r"|i don\'t have|since i don\'t|i am an ai|decision\s*:|best approach"
        r"|\*+wait|\*+actually|\*+correction|safe bet|alternative\s*:)",
        first,
    ):
        return True
    if re.match(r"(?i)^[\"']", first) and re.search(
        r"(?i)\b(make it sound|more like a friend)\b", first
    ):
        return True
    return False


def _para_is_user_chinese_reply(para: str) -> bool:
    """以中文为主、可当作给用户看的段落。"""
    p = para.strip()
    if len(p) < 4:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", p))
    if cjk < 6:
        return False
    eng_tokens = len(re.findall(r"[a-zA-Z]{4,}", p))
    if eng_tokens >= 4 and cjk < eng_tokens * 3:
        return False
    return True


def _lines_chinese_from_first_cjk_until_meta(blob: str) -> str:
    """单段内「Make it…」与中文仅单换行分隔时：从首行含足够汉字的行起，遇 Wait/Revised 等则停。"""
    lines = blob.splitlines()
    out: List[str] = []
    started = False
    for raw in lines:
        ln = raw.strip()
        if not ln:
            if started:
                out.append("")
            continue
        ll = ln.lower()
        cjk = len(re.findall(r"[\u4e00-\u9fff]", ln))
        if not started:
            if re.match(r"(?i)^(make it|revised|wait,|okay,|actually|let\'?s go)\b", ll):
                continue
            if cjk < 4:
                continue
            started = True
            ln = re.sub(r'^[\s"\'\u201c\u201d]+|[\s"\'\u201c\u201d]+$', "", ln).strip()
            out.append(ln)
            continue
        if re.match(
            r"(?i)^(wait,|revised|okay,|actually|make it|let\'?s go|\*+wait|\*+actually)\b",
            ll,
        ):
            break
        if _line_is_dense_english_cot(ln) and cjk < 5:
            break
        out.append(ln)
    return "\n".join(out).strip()


def _trim_final_polish_blob_to_chinese(after: str) -> str:
    """
    Final Polish 后常见「英文评语 + 引号草稿 + Wait + 多段中文」；
    只保留从**最后一段合格中文**向上连续的中文段，去掉尾部/夹杂的英文元信息。
    """
    blob = re.sub(r"(?m)^[ \t]+", "", after.strip())
    if not blob:
        return ""
    parts = [p.strip() for p in re.split(r"\n{2,}", blob) if p.strip()]
    rev = list(reversed(parts))
    i = 0
    while i < len(rev) and _para_starts_like_english_meta(rev[i]):
        i += 1
    if i >= len(rev):
        return _lines_chinese_from_first_cjk_until_meta(blob)
    picked: List[str] = []
    while i < len(rev):
        p = rev[i]
        if picked and _para_starts_like_english_meta(p):
            break
        if _para_is_user_chinese_reply(p):
            picked.append(p)
            i += 1
            continue
        if not picked:
            i += 1
            continue
        break
    if not picked:
        return _lines_chinese_from_first_cjk_until_meta(blob)
    merged = "\n\n".join(reversed(picked)).strip()
    if _looks_like_user_facing_chinese(merged):
        return merged
    alt = _lines_chinese_from_first_cjk_until_meta(blob)
    return alt if _looks_like_user_facing_chinese(alt) else merged


def _extract_after_last_final_polish(text: str) -> Optional[str]:
    """
    推理模型多次 *Final Polish:* 后接中文；取最后一次，并截断到 *Wait* / *Final Plan* 等元行之前。
    """
    low = text.lower()
    key = "final polish"
    idx = low.rfind(key)
    if idx == -1:
        return None
    tail = text[idx:]
    # 匹配 "Final Polish:**" / "*Final Polish:*" / 前可有编号 "5. **"
    m = re.search(r"(?is)final\s+polish[\s\*]*:[\s\*]*\n\s*", tail)
    if not m:
        return None
    after = tail[m.end() :]
    cut = re.search(
        r"(?m)^\s*(?:\*+\s*)?(?:Wait\b|Wait,\s*(?:I\s+need|one\s+more|constraint)|"
        r"Self[- ]?Correction|Final\s+Plan|Okay,\s*ready|Okay,\s*final"
        r"|One\s+more\s+check|I\s+need\s+to|Constraint\s+check|Must\s+ensure"
        r"|Ready\s+to\s+output|Better\s+approach|Correction\s*:|Actually\b|Let\'?s go)",
        after,
        re.IGNORECASE,
    )
    if cut:
        after = after[: cut.start()]
    cand = _trim_final_polish_blob_to_chinese(after)
    if _looks_like_user_facing_chinese(cand):
        return cand[:8000]
    return None


def _extract_user_reply_tail(text: str) -> Optional[str]:
    """
    模型常把「Thinking Process」与正文用单换行粘在同一段里，导致按 \\n\\n 分段失效。
    从最后一行向上收集中文回复，遇 CoT 标题行或长英文推理行则截断。
    尾部 *Wait* / *Final Plan:* 等先整行跳过，避免只剩半句吐槽。
    """
    lines = text.splitlines()
    i = len(lines) - 1
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped:
            i -= 1
            continue
        if _line_looks_like_cot_meta(stripped):
            i -= 1
            continue
        break

    body: List[str] = []
    while i >= 0:
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            i -= 1
            continue
        if _line_looks_like_cot_header(stripped):
            break
        if _line_is_dense_english_cot(stripped):
            break
        if _line_looks_like_cot_meta(stripped):
            if body:
                break
            i -= 1
            continue
        body.append(raw)
        i -= 1
    if not body:
        return None
    cand = "\n".join(reversed(body)).strip()
    if _looks_like_user_facing_chinese(cand):
        return cand[:8000]
    cjk = len(re.findall(r"[\u4e00-\u9fff]", cand))
    if len(cand) >= 10 and cjk >= 6:
        return cand[:8000]
    return None


def _strip_visible_chain_of_thought(text: str) -> str:
    """
    部分推理模型会把整段英文 CoT 写进 content。尝试只保留最终中文回复；
    若无法识别则尽量退回原文，避免误删正常回复。
    """
    if not text:
        return text
    low = text.lower()
    markers = (
        "thinking process",
        "analyze the request",
        "1. **analyze",
        "**evaluate the memory",
        "drafting the reply",
        "drafting response",
        "determine intent",
        "final polish",
        "self-correction",
        "*wait,",
        "*correction:",
        "*actually,",
        "looking at the actual conversation",
        "input structure",
    )
    if not any(m in low for m in markers):
        return text

    fp = _extract_after_last_final_polish(text)
    if fp:
        return fp

    for pat in (
        # 常见「Final Polish」标题后换行才是正文（标题行可能带 5. ** 前缀）
        r"Final\s+Polish[^\n]*\n+\s*(.+?)(?=\n\n\s*\*|\n\*Wait|\n\*Self-Correction|\Z)",
        r"Revised\s+Draft:\s*\n+\s*(.+?)(?=\n\n\s*\*|\n\*Wait|\Z)",
        r"Final\s+Reply\s*:\s*\n+\s*(.+?)(?=\n\n|\Z)",
    ):
        m = re.search(pat, text, re.DOTALL | re.IGNORECASE)
        if m:
            cand = m.group(1).strip()
            cand = re.sub(r"^[\s\*\"「]+|[\"」\s]+$", "", cand)
            if _looks_like_user_facing_chinese(cand):
                return cand.strip()

    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    for p in reversed(paras):
        if len(p) < 15:
            continue
        pl = p.lower()
        if pl.startswith("thinking process"):
            continue
        if re.match(r"^[\*\#]+\s*(analyze|evaluate|draft)", pl):
            continue
        # 段落里夹了「Final Polish」标题 + 中文：取标题后第一行中文块
        if "final polish" in pl:
            sub = re.split(r"Final\s+Polish[^\n]*\n+", p, flags=re.IGNORECASE)
            if len(sub) > 1:
                tail = sub[-1].strip()
                tail = tail.split("\n\n")[0].strip()
                if _looks_like_user_facing_chinese(tail):
                    return tail[:8000]
        if re.match(r"^\d+\.\s*\*\*", p) and "final polish" not in pl:
            continue
        cjk = len(re.findall(r"[\u4e00-\u9fff]", p))
        if cjk >= 12 and cjk / max(len(p), 1) >= 0.18:
            return p.strip()[:8000]

    tail = _extract_user_reply_tail(text)
    if tail:
        return tail

    # 只有长篇 CoT、没有定稿中文（常见于 max_tokens 截断）：勿把整页英文推给用户
    if len(text) >= 200:
        cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
        if cjk < 36 or (cjk / max(len(text), 1) < 0.09):
            return _FALLBACK_WHEN_COULD_NOT_STRIP_COT
    return text


def _assistant_message_text(message: Dict[str, Any]) -> str:
    c = _content_blocks_to_text(message.get("content"))
    if c:
        c = _strip_explicit_think_tags(c)
        c = _strip_visible_chain_of_thought(c)
        return c[:8000]

    refusal = message.get("refusal")
    if refusal is not None and str(refusal).strip():
        return str(refusal).strip()[:8000]

    for key in ("text", "output_text", "response"):
        v = message.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()[:8000]

    rc = message.get("reasoning_content")
    if rc is not None and str(rc).strip():
        text = _strip_explicit_think_tags(str(rc).strip())
        text = _strip_visible_chain_of_thought(text)
        paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        if paras:
            return paras[-1][:8000]
        return text[:8000]

    # 部分网关把可见回复放在 reasoning / think 等字段
    for key in ("reasoning", "thinking", "thought"):
        v = message.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()[:8000]

    return ""


def build_memory_block(user_message: str, top_k: int = 10) -> Tuple[str, List[Dict[str, Any]]]:
    from recall import recall

    rows = recall(user_message, top_k=top_k)
    if not rows:
        return "（暂无匹配的记忆节点；可能是新话题或图里还没有相关内容。）", []
    lines = []
    snippets = []
    for name, score in rows:
        lines.append(f"- {name}（相关度 {score:.2f}）")
        snippets.append({"name": name, "score": round(float(score), 4)})
    return "\n".join(lines), snippets


def trim_history_for_chat(
    history: List[Dict[str, str]], max_turns: int = CHAT_HISTORY_MAX_TURNS
) -> List[Dict[str, str]]:
    """只保留最近 max_turns 个「来回」即最多 max_turns*2 条 user/assistant 消息。"""
    mt = max(1, int(max_turns))
    cap = mt * 2
    if len(history) > cap:
        return history[-cap:]
    return history


def llm_chat(messages: List[Dict[str, str]], max_tokens: int = 2048) -> str:
    """
    调用 LLM 进行对话。

    Args:
        messages: 消息列表 [{"role": "user"|"assistant"|"system", "content": "..."}]
        max_tokens: 最大生成 token 数（默认 2048，避免回复被截断）

    Returns:
        模型回复的正文内容
    """
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError(
            "未读取到 API 密钥。请在项目根目录创建 api_key.local（只写一行 sk-…），"
            "或在 .env 里写 LLM_API_KEY= / OPENAI_API_KEY= 并保存到磁盘。"
        )

    url = f"{LLM_API_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    data = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    response = requests.post(url, headers=headers, json=data, timeout=180)
    response.raise_for_status()
    result = response.json()
    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError("模型返回无 choices，请检查网关 JSON 是否与 OpenAI 兼容")
    ch0 = choices[0]
    msg = ch0.get("message") or {}
    finish = ch0.get("finish_reason") or ""
    text = _assistant_message_text(msg if isinstance(msg, dict) else {})
    if not text and ch0.get("text"):
        text = _strip_visible_chain_of_thought(str(ch0["text"]).strip())[:8000]

    # 如果 finish_reason 是 length，说明 max_tokens 太小，尝试增加重试
    if not text and finish == "length":
        # 尝试用更大的 max_tokens 重试
        data["max_tokens"] = 4096
        response = requests.post(url, headers=headers, json=data, timeout=180)
        response.raise_for_status()
        result = response.json()
        choices = result.get("choices") or []
        if choices:
            ch0 = choices[0]
            msg = ch0.get("message") or {}
            text = _assistant_message_text(msg if isinstance(msg, dict) else {})
            if not text and ch0.get("text"):
                text = _strip_visible_chain_of_thought(str(ch0["text"]).strip())[:8000]

    if not text:
        raw_preview = str(msg)[:500] if msg else "(无 message)"
        print(
            f"  ⚠ LLM 无可用正文 finish_reason={finish!r} "
            f"message_keys={list(msg.keys()) if isinstance(msg, dict) else type(msg)} "
            f"preview={raw_preview!r}"
        )
        if isinstance(msg, dict) and msg.get("tool_calls"):
            raise RuntimeError(
                "模型只返回了 tool_calls，没有对话正文；请在网关关闭工具调用或换用纯对话模型。"
            )
        # 更友好的错误提示
        if finish == "length":
            hint = (
                f"模型输出被截断（finish_reason=length）。"
                f"当前 max_tokens={max_tokens} 可能太小，"
                f"请增加 llm_chat() 调用时的 max_tokens 参数，或检查网关配置。"
            )
        else:
            hint = (
                "模型返回为空：网关未在 message.content / reasoning_content 等字段中给出可读正文。"
                "若使用推理模型，请确认网关是否把最终回复写入 content；"
                "也可尝试换模型或检查 max_tokens 是否过小。"
            )
        if finish:
            hint += f"（finish_reason={finish}）"
        raise RuntimeError(hint)
    return text


def _format_turn_for_memory(user_message: str, assistant_reply: str, max_reply_len: int = 1200) -> str:
    u = user_message.strip()
    r = assistant_reply.strip()
    if len(r) > max_reply_len:
        r = r[:max_reply_len] + "…"
    return f"【对话】我：{u}\n助手：{r}"


def format_turn_for_memory(user_message: str, assistant_reply: str, max_reply_len: int = 1200) -> str:
    """与 `chat_turn` 写入图时使用同一拼接规则；供 Web 后台任务等复用。"""
    return _format_turn_for_memory(user_message, assistant_reply, max_reply_len)


def chat_turn(
    user_message: str,
    history: Optional[List[Dict[str, str]]] = None,
    remember: bool = True,
    memory_top_k: int = 10,
    session_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    一轮对话：先按用户这句话从图里「回想」→ 带记忆与历史调模型 → 回复。
    remember 为真且 MEMORY_BATCH_ENABLED：本轮只进会话缓冲，满 N 轮由 Web 后台批量入库；
    否则（或未启用批量）：每轮 store_memory（与 web_server 异步配置配合）。
    history: [{"role":"user"|"assistant","content":"..."}, ...]
    """
    from config import MEMORY_FLUSH_KEYWORDS

    history = trim_history_for_chat(history or [], CHAT_HISTORY_MAX_TURNS)
    memory_error: Optional[str] = None
    try:
        memory_block, snippets = build_memory_block(user_message, top_k=memory_top_k)
    except Exception as e:
        memory_block = "（记忆图数据库当前不可用，仅根据下方对话历史回复。）"
        snippets = []
        memory_error = str(e)

    sys_content = (
        f"{SYSTEM_PROMPT}\n{_session_local_time_hint()}\n\n"
        f"【相关记忆要点】\n{memory_block}"
    )
    messages: List[Dict[str, str]] = [{"role": "system", "content": sys_content}]
    for h in history:
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message.strip()})

    reply = llm_chat(messages)

    sid_in = (session_id or "").strip() or None
    session_out = sid_in
    pending_batch_flush = False
    immediate_flush = False  # 智能 flush 触发

    # 智能 flush：检测到关键词时立即写入
    if remember and MEMORY_BATCH_ENABLED:
        import batch_memory

        session_out = sid_in or secrets.token_urlsafe(14)
        batch_memory.append_pair(session_out, user_message.strip(), reply)

        # 检查是否包含 flush 关键词
        for keyword in MEMORY_FLUSH_KEYWORDS:
            if keyword in user_message:
                immediate_flush = True
                break

        if immediate_flush:
            # 立即 flush 当前缓冲
            try:
                batch_memory.flush_session_remainder(session_out)
                pending_batch_flush = False
            except Exception as ex:
                print(f"  ⚠ 智能 flush 失败：{ex}")
        elif batch_memory.pair_count(session_out) >= MEMORY_BATCH_TURNS:
            pending_batch_flush = True
    elif remember:
        try:
            from store import store_memory

            store_memory(_format_turn_for_memory(user_message, reply))
        except Exception as ex:
            print(
                f"  ⚠ 写入记忆图失败（对话仍会返回）：{ex}\n"
                f"     → 请确认 Neo4j 在运行；可试 .env 中 NEO4J_URI=bolt://127.0.0.1:7687"
            )

    out: Dict[str, Any] = {
        "reply": reply,
        "memory_snippets": snippets,
        "session_id": session_out or "",
        "pending_batch_flush": pending_batch_flush,
        "memory_batch_enabled": MEMORY_BATCH_ENABLED,
    }
    if memory_error:
        out["memory_error"] = memory_error
    return out
