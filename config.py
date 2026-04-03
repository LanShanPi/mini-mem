"""
Neo4j / LLM / 类人记忆参数

LLM 调用请使用 get_llm_api_key()（每次从磁盘重读）。
来源顺序：环境变量 → 重新解析 .env / MINIMEM_ENV_FILE → MINIMEM_API_KEY_FILE → api_key.local
"""
import os
from pathlib import Path

from dotenv import dotenv_values, load_dotenv


def _parse_env_lines(path: Path) -> dict:
    """逐行解析 .env，utf-8-sig 去 BOM；不依赖 python-dotenv，避免个别格式解析失败。"""
    out = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key and val:
            out[key] = val
    return out


_PROJECT_ROOT = Path(__file__).resolve().parent
_DEFAULT_ENV = _PROJECT_ROOT / ".env"
_EXTRA_ENV = (os.getenv("MINIMEM_ENV_FILE") or "").strip()
_EXTRA_ENV_PATH = Path(_EXTRA_ENV).expanduser() if _EXTRA_ENV else None

# 供诊断接口展示
PROJECT_ROOT = str(_PROJECT_ROOT)
ENV_FILE_PATH = str(_DEFAULT_ENV)
ENV_EXTRA_FILE_PATH = str(_EXTRA_ENV_PATH) if _EXTRA_ENV_PATH else ""

# 合并「文件里」的键值（主 .env + 可选额外文件，后者覆盖同名键）
_FILE_MERGED: dict = {}
_FILE_MERGED.update(_parse_env_lines(_DEFAULT_ENV))
if _EXTRA_ENV_PATH and _EXTRA_ENV_PATH.is_file():
    _FILE_MERGED.update(_parse_env_lines(_EXTRA_ENV_PATH))

load_dotenv(_DEFAULT_ENV)
if _EXTRA_ENV_PATH and _EXTRA_ENV_PATH.is_file():
    load_dotenv(_EXTRA_ENV_PATH)
load_dotenv()

_DOTENV_FILE = dotenv_values(_DEFAULT_ENV)


def _str_env(name: str, file_fallback: str = "") -> str:
    v = (os.getenv(name) or "").strip()
    if v:
        return v
    fv = (_DOTENV_FILE.get(name) or "").strip()
    if fv:
        return fv
    fv2 = (_FILE_MERGED.get(name) or "").strip()
    if fv2:
        return fv2
    return file_fallback


def _read_key_file(path: Path) -> str:
    """单行密钥文件：取第一行非空内容。"""
    if not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                return s
    except OSError:
        pass
    return ""


def _merged_env_for_llm() -> dict:
    """合并多份 .env：项目根、MINIMEM_ENV_FILE、当前工作目录（若与项目根不同）。"""
    merged: dict = {}
    merged.update(_parse_env_lines(_DEFAULT_ENV))
    if _EXTRA_ENV_PATH and _EXTRA_ENV_PATH.is_file():
        merged.update(_parse_env_lines(_EXTRA_ENV_PATH))
    try:
        cwd_dot = Path.cwd() / ".env"
        if cwd_dot.is_file() and cwd_dot.resolve() != _DEFAULT_ENV.resolve():
            merged.update(_parse_env_lines(cwd_dot))
    except OSError:
        pass
    return merged


def get_llm_api_key() -> str:
    """
    每次调用都会重新从磁盘读取。
    顺序：环境变量 → 合并 .env → MINIMEM_API_KEY_FILE → 项目根 api_key.local → 当前目录 api_key.local
    """
    merged = _merged_env_for_llm()

    for key in ("LLM_API_KEY", "OPENAI_API_KEY"):
        v = (os.getenv(key) or "").strip()
        if v:
            return v
    for key in ("LLM_API_KEY", "OPENAI_API_KEY"):
        v = (merged.get(key) or "").strip()
        if v:
            return v

    kf = (os.getenv("MINIMEM_API_KEY_FILE") or "").strip()
    if kf:
        p = Path(kf).expanduser()
        if not p.is_absolute():
            p = _PROJECT_ROOT / p
        v = _read_key_file(p)
        if v:
            return v

    for p in (_PROJECT_ROOT / "api_key.local", Path.cwd() / "api_key.local"):
        v = _read_key_file(p)
        if v:
            return v

    return ""


def save_api_key_local(api_key: str) -> Path:
    """
    将密钥写入项目根 api_key.local（单行）。用于网页粘贴保存，绕过部分环境不写 .env 的问题。
    """
    key = (api_key or "").strip()
    if len(key) < 12:
        raise ValueError("密钥过短或为空")
    path = _PROJECT_ROOT / "api_key.local"
    path.write_text(key + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def diagnose_llm_key_file() -> dict:
    """供 /api/config-status 使用，不含密钥内容。"""
    merged = _parse_env_lines(_DEFAULT_ENV)
    raw = _DEFAULT_ENV.read_text(encoding="utf-8-sig") if _DEFAULT_ENV.is_file() else ""
    has_equals_line = "LLM_API_KEY=" in raw or "OPENAI_API_KEY=" in raw
    parsed_nonempty = bool(
        (merged.get("LLM_API_KEY") or "").strip()
        or (merged.get("OPENAI_API_KEY") or "").strip()
    )
    local_f = _PROJECT_ROOT / "api_key.local"
    cwd_local = Path.cwd() / "api_key.local"
    return {
        "default_env_has_llm_or_openai_line": has_equals_line,
        "default_env_parsed_key_nonempty": parsed_nonempty,
        "api_key_local_exists": local_f.is_file(),
        "cwd_api_key_local_exists": cwd_local.is_file(),
        "process_cwd": str(Path.cwd()),
    }


# Neo4j 连接配置
NEO4J_URI = _str_env("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = _str_env("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _str_env("NEO4J_PASSWORD", "minimem123")

# LLM 配置（OpenAI 兼容 API）
LLM_API_BASE = _str_env("LLM_API_BASE", "https://api.openai.com/v1").rstrip("/")
# 模块导入时快照；发起 LLM 请求请用 get_llm_api_key() 以读到最新 .env / 密钥文件
LLM_API_KEY = get_llm_api_key()
LLM_MODEL = _str_env("LLM_MODEL", "gpt-4")
LLM_EMBEDDING_MODEL = _str_env("LLM_EMBEDDING_MODEL", "text-embedding-3-small")

# 实体与记忆分析
ENTITY_EXTRACTOR = _str_env("ENTITY_EXTRACTOR", "llm")
MAX_ENTITIES = int(_str_env("MAX_ENTITIES", "10"))

STORE_EMBEDDING = _str_env("STORE_EMBEDDING", "false").lower() == "true"
RECALL_USE_EMBEDDING = _str_env("RECALL_USE_EMBEDDING", "false").lower() == "true"
EMBEDDING_RECALL_TOP_N = int(_str_env("EMBEDDING_RECALL_TOP_N", "5"))
EMBEDDING_CANDIDATE_LIMIT = int(_str_env("EMBEDDING_CANDIDATE_LIMIT", "400"))

DEFAULT_EDGE_WEIGHT = float(_str_env("DEFAULT_EDGE_WEIGHT", "0.5"))
STRENGTHEN_AMOUNT = float(_str_env("STRENGTHEN_AMOUNT", "0.1"))
DECAY_RATE = float(_str_env("DECAY_RATE", "0.001"))
DECAY_RATE_SLOW = float(_str_env("DECAY_RATE_SLOW", "0.0004"))
DECAY_RATE_FAST = float(_str_env("DECAY_RATE_FAST", "0.002"))
MIN_WEIGHT = float(_str_env("MIN_WEIGHT", "0.01"))
MAX_WEIGHT = float(_str_env("MAX_WEIGHT", "1.0"))

ACTIVATION_DEPTH = int(_str_env("ACTIVATION_DEPTH", "3"))
RECALL_TOP_K = int(_str_env("RECALL_TOP_K", "10"))
DECAY_PER_HOP = float(_str_env("DECAY_PER_HOP", "0.7"))

# ====== 召回性能兜底（图变大后更稳）======
# 1) 限制 recall_query_tokens 返回的 token 数量，减少对 Neo4j 的查询次数。
RECALL_QUERY_MAX_TOKENS = int(_str_env("RECALL_QUERY_MAX_TOKENS", "16"))
# 2) 限制 recall 的入口节点数量，避免为太多入口做 activate_spread_cypher 扩散。
RECALL_ENTRY_NODE_LIMIT = int(_str_env("RECALL_ENTRY_NODE_LIMIT", "20"))
# 3) 给 activation 扩散做短 TTL 缓存（同一节点在短时间内被多次检索时能显著提速）。
RECALL_ACTIVATION_CACHE_TTL_SEC = int(
    _str_env("RECALL_ACTIVATION_CACHE_TTL_SEC", "30")
)
RECALL_ACTIVATION_CACHE_MAXSIZE = int(
    _str_env("RECALL_ACTIVATION_CACHE_MAXSIZE", "128")
)

if RECALL_QUERY_MAX_TOKENS < 4:
    RECALL_QUERY_MAX_TOKENS = 4
if RECALL_ENTRY_NODE_LIMIT < 5:
    RECALL_ENTRY_NODE_LIMIT = 5
if RECALL_ACTIVATION_CACHE_TTL_SEC < 1:
    RECALL_ACTIVATION_CACHE_TTL_SEC = 1
if RECALL_ACTIVATION_CACHE_MAXSIZE < 16:
    RECALL_ACTIVATION_CACHE_MAXSIZE = 16

RECAL_SALIENCE_WEIGHT = float(_str_env("RECAL_SALIENCE_WEIGHT", "0.55"))
RECAL_AROUSAL_WEIGHT = float(_str_env("RECAL_AROUSAL_WEIGHT", "0.35"))
RECAL_RECENCY_HALF_LIFE_DAYS = float(_str_env("RECAL_RECENCY_HALF_LIFE_DAYS", "14"))
RECAL_BOOST_MIN = float(_str_env("RECAL_BOOST_MIN", "0.55"))
RECAL_BOOST_MAX = float(_str_env("RECAL_BOOST_MAX", "2.0"))

SALIENCE_FULL_MESH = float(_str_env("SALIENCE_FULL_MESH", "0.42"))
SKIP_MESH_KINDS = frozenset(
    k.strip()
    for k in _str_env("SKIP_MESH_KINDS", "smalltalk").split(",")
    if k.strip()
)

# 对话上下文与批量写图（Web / chat_turn）
MEMORY_BATCH_ENABLED = _str_env("MEMORY_BATCH_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
MEMORY_BATCH_TURNS = int(_str_env("MEMORY_BATCH_TURNS", "5"))  # 优化：从 10 改为 5，减少延迟
MEMORY_BATCH_KEEP_PAIRS = int(_str_env("MEMORY_BATCH_KEEP_PAIRS", "3"))  # 优化：从 5 改为 3
MEMORY_BATCH_ASYNC_FLUSH = _str_env("MEMORY_BATCH_ASYNC_FLUSH", "true").lower() in (
    "1",
    "true",
    "yes",
)
CHAT_HISTORY_MAX_TURNS = int(_str_env("CHAT_HISTORY_MAX_TURNS", "10"))

# 智能 flush 触发：检测到这些关键词时立即 flush
MEMORY_FLUSH_KEYWORDS = frozenset(
    k.strip()
    for k in _str_env(
        "MEMORY_FLUSH_KEYWORDS",
        "记住，别忘了，记一下，记着，一定要记住，记住这个，别忘了这个",
    ).split(",")
    if k.strip()
)
# 用户无操作自动 flush 延迟（秒）- 3 分钟
MEMORY_IDLE_FLUSH_SECONDS = int(_str_env("MEMORY_IDLE_FLUSH_SECONDS", "180"))

# 自动备份配置
MEMORY_AUTO_BACKUP_ENABLED = _str_env("MEMORY_AUTO_BACKUP_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
MEMORY_AUTO_BACKUP_INTERVAL_HOURS = int(_str_env("MEMORY_AUTO_BACKUP_INTERVAL_HOURS", "6"))
MEMORY_AUTO_BACKUP_DIR = _str_env("MEMORY_AUTO_BACKUP_DIR", "backups")
MEMORY_AUTO_BACKUP_KEEP_DAYS = int(_str_env("MEMORY_AUTO_BACKUP_KEEP_DAYS", "7"))

if MEMORY_BATCH_TURNS < 2:
    MEMORY_BATCH_TURNS = 2
if MEMORY_BATCH_KEEP_PAIRS < 1:
    MEMORY_BATCH_KEEP_PAIRS = 1
if MEMORY_BATCH_KEEP_PAIRS >= MEMORY_BATCH_TURNS:
    MEMORY_BATCH_KEEP_PAIRS = MEMORY_BATCH_TURNS - 1

# 混合检索权重配置（保留供未来扩展，当前 recall.py 使用内部权重逻辑）
# 如需调整混合检索权重，可在 recall.py 中直接使用这些参数
HYBRID_SEARCH_KEYWORD_WEIGHT = float(_str_env("HYBRID_SEARCH_KEYWORD_WEIGHT", "0.5"))
HYBRID_SEARCH_VECTOR_WEIGHT = float(_str_env("HYBRID_SEARCH_VECTOR_WEIGHT", "0.3"))
HYBRID_SEARCH_ACTIVATION_WEIGHT = float(_str_env("HYBRID_SEARCH_ACTIVATION_WEIGHT", "0.2"))

# 验证权重和是否为 1.0（允许小范围浮点误差）
_HYBRID_WEIGHT_SUM = (
    HYBRID_SEARCH_KEYWORD_WEIGHT
    + HYBRID_SEARCH_VECTOR_WEIGHT
    + HYBRID_SEARCH_ACTIVATION_WEIGHT
)
if abs(_HYBRID_WEIGHT_SUM - 1.0) > 0.01:
    # 自动归一化权重
    _scale = 1.0 / _HYBRID_WEIGHT_SUM if _HYBRID_WEIGHT_SUM > 0 else 1.0
    HYBRID_SEARCH_KEYWORD_WEIGHT *= _scale
    HYBRID_SEARCH_VECTOR_WEIGHT *= _scale
    HYBRID_SEARCH_ACTIVATION_WEIGHT *= _scale

# 记忆合并配置
MEMORY_MERGE_ENABLED = _str_env("MEMORY_MERGE_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
MEMORY_MERGE_SIMILARITY_THRESHOLD = float(
    _str_env("MEMORY_MERGE_SIMILARITY_THRESHOLD", "0.85")
)
MEMORY_MERGE_MIN_SALIENCE = float(_str_env("MEMORY_MERGE_MIN_SALIENCE", "0.2"))
MEMORY_FORGET_THRESHOLD = float(_str_env("MEMORY_FORGET_THRESHOLD", "0.05"))

# 实体归一化配置
ENTITY_NORMALIZATION_ENABLED = _str_env("ENTITY_NORMALIZATION_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)
ENTITY_BLACKLIST_FILE = _str_env("ENTITY_BLACKLIST_FILE", "entity_blacklist.txt")
ENTITY_WHITELIST_FILE = _str_env("ENTITY_WHITELIST_FILE", "entity_whitelist.txt")
