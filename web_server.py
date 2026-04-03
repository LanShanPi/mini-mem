#!/usr/bin/env python3
"""
MiniMem 浏览器测试用 Web 服务：FastAPI + 静态前端。

启动（项目根目录）：
  python3 -m uvicorn web_server:app --reload --host 127.0.0.1 --port 8765

浏览器打开：http://127.0.0.1:8765/
Neo4j 未启动时会返回 503，页面会提示执行 ./start_neo4j.sh（需 Docker）。

默认 MEMORY_BATCH_ENABLED=true：每满 N 轮在后台批量抽取并写 Neo4j；关页 POST /api/chat/flush。
非批量时 MINIMEM_ASYNC_STORE=true 表示每轮先返回再后台 store（见 DOCUMENTATION.md §4）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from neo4j.exceptions import ServiceUnavailable
from pydantic import BaseModel, Field

import config as app_config

from chat import chat_turn, format_turn_for_memory

from auth import verify_api_key, is_auth_enabled
from backup import start_backup_service
from batch_memory import batch_flush_worker, flush_session_remainder, flush_idle_sessions
from maintenance import daily_decay, get_stats
from memory_graph import close_graph, get_graph
from memory_merge import run_memory_maintenance
from recall import recall, related_to
from store import store_memory

logger = logging.getLogger(__name__)


def _async_chat_store_enabled() -> bool:
    """默认 true：先返回对话，再在后台写 Neo4j（省一次阻塞）。设为 false 可恢复「响应前写完图」。"""
    v = (os.getenv("MINIMEM_ASYNC_STORE") or "true").strip().lower()
    return v not in ("0", "false", "no", "off")


def _background_store_conversation_turn(user_message: str, assistant_reply: str) -> None:
    try:
        store_memory(format_turn_for_memory(user_message, assistant_reply))
    except Exception:
        logger.exception("后台写入记忆图失败（客户端已收到 reply）")


def _idle_flush_worker(interval: int = 60) -> None:
    """
    后台线程：每分钟检查一次是否有会话空闲超时，需要 flush。
    """
    while True:
        try:
            time.sleep(interval)
            flush_idle_sessions()
        except Exception:
            logger.exception("_idle_flush_worker 失败")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 延迟连接：首次 API 调用时再连 Neo4j，避免图未启动时页面都打不开
    # 启动后台空闲 flush 线程
    idle_thread = threading.Thread(target=_idle_flush_worker, daemon=True)
    idle_thread.start()
    # 启动自动备份服务
    start_backup_service()
    yield
    close_graph()


app = FastAPI(title="MiniMem Web", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _require_localhost(request: Request) -> None:
    """防止把「写密钥」接口暴露到公网。"""
    if (os.getenv("MINIMEM_ALLOW_REMOTE_KEY_SETUP") or "").lower() in ("1", "true", "yes"):
        return
    host = (request.client.host if request.client else "") or ""
    if host in ("127.0.0.1", "::1", "localhost"):
        return
    xf = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    if xf in ("127.0.0.1", "::1"):
        return
    raise HTTPException(
        status_code=403,
        detail="仅允许本机保存密钥。远程使用请用环境变量或 SSH；或显式设置 MINIMEM_ALLOW_REMOTE_KEY_SETUP=1（不推荐）。",
    )


class ApiKeySaveBody(BaseModel):
    api_key: str = Field(..., min_length=12, description="OpenAI 兼容 API Key")


@app.post("/api/settings/save-api-key")
def api_save_api_key(request: Request, body: ApiKeySaveBody):
    """
    把密钥写入项目根目录 api_key.local（已 gitignore）。
    解决部分编辑器/环境无法把密钥真正写入 .env 的问题。
    """
    _require_localhost(request)
    try:
        path = app_config.save_api_key_local(body.api_key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {
        "ok": True,
        "saved_to": str(path),
        "llm_api_key_configured": bool(app_config.get_llm_api_key()),
    }


@app.get("/health")
def api_health_check():
    """健康检查端点，用于负载均衡器和容器编排"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/api/config-status")
def api_config_status():
    """不返回密钥，只用于确认程序是否读到 LLM 配置。"""
    from pathlib import Path

    p = Path(app_config.ENV_FILE_PATH)
    diag = app_config.diagnose_llm_key_file()
    return {
        "project_root": app_config.PROJECT_ROOT,
        "default_env_file": app_config.ENV_FILE_PATH,
        "default_env_file_exists": p.is_file(),
        "extra_env_file": app_config.ENV_EXTRA_FILE_PATH or None,
        "llm_api_key_configured": bool(app_config.get_llm_api_key()),
        "llm_api_base": app_config.LLM_API_BASE,
        "api_key_local_path": str(Path(app_config.PROJECT_ROOT) / "api_key.local"),
        "auth_enabled": is_auth_enabled(),
        "memory_batch_enabled": app_config.MEMORY_BATCH_ENABLED,
        "memory_auto_backup_enabled": app_config.MEMORY_AUTO_BACKUP_ENABLED,
        "memory_auto_backup_interval_hours": app_config.MEMORY_AUTO_BACKUP_INTERVAL_HOURS,
        "memory_auto_backup_keep_days": app_config.MEMORY_AUTO_BACKUP_KEEP_DAYS,
        "memory_idle_flush_seconds": app_config.MEMORY_IDLE_FLUSH_SECONDS,
        "memory_batch_turns": app_config.MEMORY_BATCH_TURNS,
        "memory_batch_keep_pairs": app_config.MEMORY_BATCH_KEEP_PAIRS,
        "chat_history_max_turns": app_config.CHAT_HISTORY_MAX_TURNS,
        **diag,
        "hint": (
            "若 llm_api_key_configured 为 false 但 default_env_has_llm_or_openai_line 为 true："
            "说明 .env 里有 LLM_API_KEY= 行但等号后面是空的（磁盘未保存或写错行）。"
            "可改在 api_key.local 只写一行密钥。"
        ),
    }


@app.exception_handler(ServiceUnavailable)
async def neo4j_down_handler(_request: Request, exc: ServiceUnavailable):
    return JSONResponse(
        status_code=503,
        content={
            "detail": {
                "code": "NEO4J_DOWN",
                "hint": (
                    "Neo4j 未在运行：无法连接 bolt://localhost:7687。"
                    "请在项目根目录执行 ./start_neo4j.sh（需本机已装 Docker 并启动 Docker Desktop），"
                    "或自行启动 Neo4j；密码需与 .env 中 NEO4J_PASSWORD 一致（示例为 minimem123）。"
                ),
                "technical": str(exc),
            }
        },
    )


class StoreBody(BaseModel):
    text: str = Field(..., min_length=1, description="要记住的一段话")


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., min_length=1)


class ChatBody(BaseModel):
    message: str = Field(..., min_length=1, description="用户当前一句话")
    history: List[ChatMessage] = Field(default_factory=list)
    remember: bool = Field(
        True,
        description="是否把本轮「用户话 + 助手回复」写入记忆图（批量模式见 MEMORY_BATCH_ENABLED）",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="会话 id；批量写图时由前端持久化并在每轮回传",
    )


class ChatFlushBody(BaseModel):
    session_id: str = Field(..., min_length=4, description="要刷盘剩余缓冲的会话 id")


@app.post("/api/chat")
def api_chat(body: ChatBody, background_tasks: BackgroundTasks, _key: Optional[str] = Depends(verify_api_key)):
    """
    聊天接口。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        hist = [{"role": m.role, "content": m.content.strip()} for m in body.history]
        msg = body.message.strip()
        sid = (body.session_id or "").strip() or None

        if body.remember and app_config.MEMORY_BATCH_ENABLED:
            out = chat_turn(
                msg,
                history=hist,
                remember=True,
                session_id=sid,
            )
            if out.get("pending_batch_flush") and out.get("session_id"):
                background_tasks.add_task(batch_flush_worker, out["session_id"])
            return out

        if body.remember and _async_chat_store_enabled():
            out = chat_turn(msg, history=hist, remember=False, session_id=sid)
            background_tasks.add_task(
                _background_store_conversation_turn,
                msg,
                out["reply"],
            )
            return out
        return chat_turn(msg, history=hist, remember=body.remember, session_id=sid)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=f"请求参数错误：{str(e)}") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"数据格式错误：{str(e)}") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("未处理异常：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.post("/api/chat/flush")
def api_chat_flush(body: ChatFlushBody, _key: Optional[str] = Depends(verify_api_key)):
    """
    关页或结束会话：把未满批的缓冲也写入 Neo4j（仅批量模式有意义）。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        flush_session_remainder(body.session_id.strip())
        return {"ok": True}
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_chat_flush 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.post("/api/store")
def api_store(body: StoreBody, _key: Optional[str] = Depends(verify_api_key)):
    """
    直接存储一段话到记忆图。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        node_id = store_memory(body.text.strip())
        return {"ok": True, "event_id": node_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"数据格式错误：{str(e)}") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_store 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.get("/api/recall")
def api_recall(
    q: str = Query(..., min_length=1, description="关键词"),
    top_k: int = Query(10, ge=1, le=50),
):
    try:
        rows = recall(q.strip(), top_k=top_k)
        return {"ok": True, "query": q, "results": [{"name": n, "score": s} for n, s in rows]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"查询参数错误：{str(e)}") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_recall 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.get("/api/related")
def api_related(
    name: str = Query(..., min_length=1, description="节点名称"),
    depth: int = Query(2, ge=1, le=5),
):
    try:
        rows = related_to(name.strip(), depth=depth)
        return {"ok": True, "name": name, "results": [{"name": n, "score": s} for n, s in rows]}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"查询参数错误：{str(e)}") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_related 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.get("/api/stats")
def api_stats():
    try:
        stats = get_stats()
        return {"ok": True, **stats}
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_stats 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.post("/api/decay")
def api_decay(_key: Optional[str] = Depends(verify_api_key)):
    """
    执行日常记忆衰减。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        daily_decay()
        return {"ok": True, "message": "已执行日常衰减"}
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_decay 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.post("/api/maintenance")
def api_maintenance(_key: Optional[str] = Depends(verify_api_key)):
    """
    执行记忆维护任务（合并相似记忆、遗忘低重要性记忆、检测冲突）。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        stats = run_memory_maintenance()
        return {"ok": True, **stats}
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_maintenance 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


class ImportDataBody(BaseModel):
    data: dict = Field(..., description="导入的数据：{nodes: [...], edges: [...]}")


class ManualBackupResponse(BaseModel):
    status: str
    file: Optional[str] = None
    error: Optional[str] = None


@app.post("/api/backup")
def api_manual_backup(_key: Optional[str] = Depends(verify_api_key)) -> dict:
    """
    手动触发一次数据备份。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    from backup import run_backup
    result = run_backup()
    return result


@app.get("/api/export")
def api_export(_key: Optional[str] = Depends(verify_api_key)):
    """
    导出整个记忆图为 JSON。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        graph = get_graph()
        data = graph.export_all()
        return {"ok": True, **data}
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_export 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


@app.post("/api/import")
def api_import(body: ImportDataBody, _key: Optional[str] = Depends(verify_api_key)):
    """
    从 JSON 导入记忆图。

    当 MINIMEM_API_KEY 配置时，需要客户端提供 X-API-Key 头。
    """
    try:
        graph = get_graph()
        result = graph.import_data(body.data)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"数据格式错误：{str(e)}") from e
    except ConnectionError as e:
        raise HTTPException(status_code=503, detail=f"服务暂时不可用：{str(e)}") from e
    except Exception as e:
        logger.exception("api_import 错误：%s", e)
        raise HTTPException(status_code=500, detail=f"服务器内部错误：{str(e)}") from e


static_dir = Path(__file__).resolve().parent / "static"
if static_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
