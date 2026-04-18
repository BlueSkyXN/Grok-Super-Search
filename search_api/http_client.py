"""
HTTP 传输层 — 对齐 SouWen 的 http_client.py 和 grok2api 的 transport/http.py。

支持两种后端：
- httpx（默认，轻量，SouWen 主用）
- curl_cffi（可选，TLS 指纹模拟，grok2api 主用）

所有请求都通过 GrokHttpClient 统一调度。
"""

import base64
import logging
import random
import re
import string
import uuid
from typing import Any, AsyncIterator

from .config import get_settings

logger = logging.getLogger("grok_search")

# 上游错误响应截断长度
MAX_ERROR_BODY_LENGTH = 500


# ═══════════════════════════════════════════════════════════════════
# Header 构造（提取自 grok2api: headers.py）
# ═══════════════════════════════════════════════════════════════════


def _statsig_id() -> str:
    """生成 x-statsig-id 指纹（对齐 grok2api 的 _statsig_id，模拟浏览器 JS 错误堆栈）"""
    if random.choice((True, False)):
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        msg = f"e:TypeError: Cannot read properties of null (reading 'children['{rand}']')"
    else:
        rand = "".join(random.choices(string.ascii_lowercase, k=10))
        msg = f"e:TypeError: Cannot read properties of undefined (reading '{rand}')"
    return base64.b64encode(msg.encode()).decode()


def _chrome_version(user_agent: str) -> str:
    m = re.search(r"Chrome/(\d+)", user_agent)
    return m.group(1) if m else "136"


def build_grok_headers(sso_token: str) -> dict[str, str]:
    """
    构造 Grok 请求头。
    对齐 grok2api 的 build_http_headers，包含完整的浏览器指纹。
    """
    settings = get_settings()
    tok = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    cookie = f"sso={tok}; sso-rw={tok}"
    if settings.cf_clearance:
        cookie += f"; cf_clearance={settings.cf_clearance}"

    ver = _chrome_version(settings.user_agent)
    return {
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
        "Priority": "u=1, i",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Ch-Ua": (
            f'"Google Chrome";v="{ver}", '
            f'"Chromium";v="{ver}", '
            '"Not(A:Brand";v="24"'
        ),
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Model": "",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "User-Agent": settings.user_agent,
        "Cookie": cookie,
        "x-statsig-id": _statsig_id(),
        "x-xai-request-id": str(uuid.uuid4()),
    }


# ═══════════════════════════════════════════════════════════════════
# httpx 后端（默认，对齐 SouWen）
# ═══════════════════════════════════════════════════════════════════


async def _httpx_stream(
    url: str,
    headers: dict[str, str],
    data: bytes,
    timeout: float,
    proxy: str,
) -> AsyncIterator[str]:
    """使用 httpx 发起 SSE 流式请求"""
    import httpx

    transport_kwargs: dict[str, Any] = {}
    if proxy:
        transport_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=15.0),
        follow_redirects=True,
        **transport_kwargs,
    ) as client:
        async with client.stream(
            "POST",
            url,
            headers=headers,
            content=data,
        ) as response:
            if response.status_code != 200:
                body = (await response.aread()).decode("utf-8", "replace")[:MAX_ERROR_BODY_LENGTH]
                raise GrokUpstreamError(response.status_code, body)
            async for line in response.aiter_lines():
                yield line


# ═══════════════════════════════════════════════════════════════════
# curl_cffi 后端（可选，TLS 指纹模拟）
# ═══════════════════════════════════════════════════════════════════


async def _curl_cffi_stream(
    url: str,
    headers: dict[str, str],
    data: bytes,
    timeout: float,
    proxy: str,
) -> AsyncIterator[str]:
    """使用 curl_cffi 发起 SSE 流式请求（浏览器指纹模拟）"""
    from curl_cffi.requests import AsyncSession

    ver = _chrome_version(get_settings().user_agent)
    session_kwargs: dict[str, Any] = {"impersonate": f"chrome{ver}"}
    if proxy:
        session_kwargs["proxies"] = {"http": proxy, "https": proxy}

    async with AsyncSession(**session_kwargs) as session:
        response = await session.post(
            url,
            headers=headers,
            data=data,
            timeout=timeout,
            stream=True,
        )
        if response.status_code != 200:
            body = ""
            try:
                body = response.content.decode("utf-8", "replace")[:MAX_ERROR_BODY_LENGTH]
            except Exception:
                pass
            raise GrokUpstreamError(response.status_code, body)
        async for line in response.aiter_lines():
            yield line


# ═══════════════════════════════════════════════════════════════════
# 统一 HTTP 客户端
# ═══════════════════════════════════════════════════════════════════


class GrokUpstreamError(Exception):
    """Grok 上游请求错误"""
    def __init__(self, status_code: int, body: str = ""):
        self.status_code = status_code
        self.body = body
        super().__init__(f"Grok upstream returned {status_code}: {body[:200]}")


async def grok_stream_request(
    url: str,
    sso_token: str,
    payload: bytes,
) -> AsyncIterator[str]:
    """
    向 Grok 发起 SSE 流式请求。

    根据配置自动选择 httpx 或 curl_cffi 后端。
    对齐 grok2api 的 post_stream + SouWen 的 http_client。
    """
    settings = get_settings()
    headers = build_grok_headers(sso_token)

    backend = settings.http_backend.lower()
    if backend == "curl_cffi":
        logger.debug("using curl_cffi backend")
        async for line in _curl_cffi_stream(
            url, headers, payload, settings.timeout, settings.proxy_url
        ):
            yield line
    else:
        logger.debug("using httpx backend")
        async for line in _httpx_stream(
            url, headers, payload, settings.timeout, settings.proxy_url
        ):
            yield line
