"""
HTTP 传输层 — 对齐 SouWen 的 http_client.py 和 grok2api 的 transport/http.py。

支持两种后端：
- httpx（默认，轻量，SouWen 主用）
- curl_cffi（可选，TLS 指纹模拟，grok2api 主用）

增强特性（参考 SouWen）：
- 浏览器指纹轮换（fingerprint.py）
- curl_cffi 自动降级到 httpx
- WARP 代理自动集成
- 代理池支持
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

# curl_cffi 可用性检测（参考 SouWen 的可选依赖模式）
try:
    from curl_cffi.requests import AsyncSession as CurlAsyncSession  # noqa: F401
    _HAS_CURL_CFFI = True
except ImportError:
    _HAS_CURL_CFFI = False


def has_curl_cffi() -> bool:
    """检查 curl_cffi 是否可用"""
    return _HAS_CURL_CFFI


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

    增强（参考 SouWen）：
    - 指纹轮换模式：每次请求使用随机浏览器指纹，增加反爬规避能力
    - 固定指纹模式：使用配置的 User-Agent
    """
    settings = get_settings()
    tok = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    cookie = f"sso={tok}; sso-rw={tok}"
    if settings.cf_clearance:
        cookie += f"; cf_clearance={settings.cf_clearance}"

    # 指纹轮换（参考 SouWen 的 BrowserFingerprint）
    if settings.fingerprint_rotation:
        from .fingerprint import get_random_fingerprint
        fp = get_random_fingerprint()
        ua = fp.user_agent
        sec_ch_ua = fp.sec_ch_ua
    else:
        ua = settings.user_agent
        ver = _chrome_version(ua)
        sec_ch_ua = (
            f'"Google Chrome";v="{ver}", '
            f'"Chromium";v="{ver}", '
            '"Not(A:Brand";v="24"'
        )

    ver = _chrome_version(ua)
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
        "Sec-Ch-Ua": sec_ch_ua,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Model": "",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "User-Agent": ua,
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
    """使用 curl_cffi 发起 SSE 流式请求（浏览器指纹模拟）

    增强（参考 SouWen）：
    - 使用 BrowserFingerprint 的 impersonate 参数，而非手动拼接版本号
    - 指纹轮换模式下每次请求使用不同的 TLS 指纹
    """
    from curl_cffi.requests import AsyncSession

    settings = get_settings()
    if settings.fingerprint_rotation:
        from .fingerprint import get_random_fingerprint
        fp = get_random_fingerprint()
        impersonate = fp.impersonate
    else:
        ver = _chrome_version(settings.user_agent)
        impersonate = f"chrome{ver}"

    session_kwargs: dict[str, Any] = {"impersonate": impersonate}
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
    增强（参考 SouWen）：
    - 使用 settings.get_proxy() 支持 WARP + 代理池
    - 使用 settings.resolve_http_backend() 支持 auto 模式
    - curl_cffi 不可用时自动降级到 httpx
    """
    settings = get_settings()
    headers = build_grok_headers(sso_token)
    proxy = settings.get_proxy()

    backend = settings.resolve_http_backend()
    if backend == "curl_cffi":
        if not _HAS_CURL_CFFI:
            logger.warning("curl_cffi 不可用，降级到 httpx")
            backend = "httpx"

    if backend == "curl_cffi":
        logger.debug("using curl_cffi backend")
        async for line in _curl_cffi_stream(url, headers, payload, settings.timeout, proxy):
            yield line
    else:
        logger.debug("using httpx backend")
        async for line in _httpx_stream(url, headers, payload, settings.timeout, proxy):
            yield line


async def _httpx_post_json(
    url: str,
    headers: dict[str, str],
    data: bytes,
    timeout: float,
    proxy: str,
) -> dict:
    import httpx

    transport_kwargs: dict[str, Any] = {}
    if proxy:
        transport_kwargs["proxy"] = proxy

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=10.0),
        follow_redirects=True,
        **transport_kwargs,
    ) as client:
        response = await client.post(url, headers=headers, content=data)
        if response.status_code != 200:
            body = response.text[:MAX_ERROR_BODY_LENGTH]
            raise GrokUpstreamError(response.status_code, body)
        return response.json()


async def _curl_cffi_post_json(
    url: str,
    headers: dict[str, str],
    data: bytes,
    timeout: float,
    proxy: str,
) -> dict:
    from curl_cffi.requests import AsyncSession

    settings = get_settings()
    if settings.fingerprint_rotation:
        from .fingerprint import get_random_fingerprint
        fp = get_random_fingerprint()
        impersonate = fp.impersonate
    else:
        ver = _chrome_version(settings.user_agent)
        impersonate = f"chrome{ver}"

    session_kwargs: dict[str, Any] = {"impersonate": impersonate}
    if proxy:
        session_kwargs["proxies"] = {"http": proxy, "https": proxy}

    async with AsyncSession(**session_kwargs) as session:
        response = await session.post(url, headers=headers, data=data, timeout=timeout)
        if response.status_code != 200:
            body = ""
            try:
                body = response.content.decode("utf-8", "replace")[:MAX_ERROR_BODY_LENGTH]
            except Exception:
                pass
            raise GrokUpstreamError(response.status_code, body)
        return response.json()


async def grok_post_json(
    url: str,
    sso_token: str,
    payload: bytes,
) -> dict:
    """
    向 Grok 发起普通 POST 请求，返回 JSON。
    根据配置自动选择 httpx 或 curl_cffi 后端。
    支持 WARP + 代理池 + curl_cffi 自动降级。
    """
    settings = get_settings()
    headers = build_grok_headers(sso_token)
    proxy = settings.get_proxy()

    backend = settings.resolve_http_backend()
    if backend == "curl_cffi" and not _HAS_CURL_CFFI:
        logger.warning("curl_cffi 不可用，降级到 httpx (post_json)")
        backend = "httpx"

    if backend == "curl_cffi":
        logger.debug("using curl_cffi backend (post_json)")
        return await _curl_cffi_post_json(url, headers, payload, 30.0, proxy)
    else:
        logger.debug("using httpx backend (post_json)")
        return await _httpx_post_json(url, headers, payload, 30.0, proxy)
