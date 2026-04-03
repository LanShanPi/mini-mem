"""
OpenAI 兼容 Embeddings API（如 bge-m3）
"""
from typing import List, Optional
import math
import requests

from config import LLM_API_BASE, LLM_EMBEDDING_MODEL, get_llm_api_key


def embed_text(text: str, model: Optional[str] = None) -> Optional[List[float]]:
    """单条文本转向量；失败返回 None。"""
    api_key = get_llm_api_key()
    if not api_key or not text.strip():
        return None
    m = model or LLM_EMBEDDING_MODEL
    url = f"{LLM_API_BASE}/embeddings"
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={"model": m, "input": text},
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        vec = data["data"][0]["embedding"]
        return list(vec)
    except Exception as e:
        print(f"  ⚠ embedding 失败：{e}")
        return None


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
