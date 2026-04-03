"""
auth.py - 简单的 API Key 认证中间件

使用方法：
1. 在 .env 中设置 MINIMEM_API_KEY=your-secret-key
2. 或在 api_key.local 文件中写入密钥
3. 客户端请求时带上 X-API-Key 头

无认证模式（开发）：
- 不设置 API_KEY 时，所有接口开放访问（默认）
"""
import os
from typing import Optional
from fastapi import Request, HTTPException, Security
from fastapi.security import APIKeyHeader
from starlette.status import HTTP_401_UNAUTHORIZED

from config import _parse_env_lines, PROJECT_ROOT
from pathlib import Path

_API_KEY_HEADER = APIKeyHeader(name="X-API-Key", auto_error=False)
_api_key_cache: Optional[str] = None


def _get_configured_api_key() -> Optional[str]:
    """从配置中读取 API 密钥（用于验证客户端请求）"""
    global _api_key_cache
    if _api_key_cache is not None:
        return _api_key_cache

    # 1. 环境变量
    env_key = os.getenv("MINIMEM_API_KEY", "").strip()
    if env_key:
        _api_key_cache = env_key
        return env_key

    # 2. .env 文件
    env_file = Path(PROJECT_ROOT) / ".env"
    if env_file.is_file():
        env_vars = _parse_env_lines(env_file)
        key = env_vars.get("MINIMEM_API_KEY", "").strip()
        if key:
            _api_key_cache = key
            return key

    # 3. api_key.local 文件（复用 LLM 密钥文件）
    key_file = Path(PROJECT_ROOT) / "api_key.local"
    if key_file.is_file():
        try:
            content = key_file.read_text(encoding="utf-8").strip()
            for line in content.splitlines():
                s = line.strip()
                if s and not s.startswith("#"):
                    _api_key_cache = s
                    return s
        except OSError:
            pass

    _api_key_cache = ""
    return ""


def get_api_key() -> Optional[str]:
    """
    获取已配置的服务端 API 密钥。
    如果返回 None 或空字符串，表示未启用认证。
    """
    return _get_configured_api_key()


async def verify_api_key(request: Request, api_key: Optional[str] = Security(_API_KEY_HEADER)) -> Optional[str]:
    """
    验证客户端请求的 API Key。

    - 如果服务端未配置 API_KEY，则跳过认证（开发模式）
    - 如果服务端已配置 API_KEY，则客户端必须提供正确的 X-API-Key 头

    Returns:
        有效的 API Key，或未启用认证时返回 None

    Raises:
        HTTPException: 401 未授权
    """
    server_key = _get_configured_api_key()

    # 未配置服务端密钥：跳过认证（开发模式）
    if not server_key:
        return None

    # 已配置服务端密钥：验证客户端请求
    if not api_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="缺少 X-API-Key 请求头",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    if api_key != server_key:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="无效的 API Key",
            headers={"WWW-Authenticate": "ApiKey"},
        )

    return api_key


def is_auth_enabled() -> bool:
    """检查是否启用了 API Key 认证"""
    return bool(_get_configured_api_key())


def generate_api_key() -> str:
    """生成一个随机的 API Key（可用于初始化配置）"""
    import secrets
    return f"minimem_{secrets.token_urlsafe(24)}"
