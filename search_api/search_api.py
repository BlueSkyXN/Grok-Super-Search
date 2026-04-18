"""
Grok Search API — 从 grok2api 核心提取的独立搜索服务

基于 Grok Web 的 /rest/app-chat/conversations/new 接口，
将 Grok 的搜索能力封装为标准 REST API。

灵感来源：https://github.com/chenyme/grok2api
核心差异：只做搜索结果提取，不做通用 Chat 转发。

用法:
    pip install -r requirements.txt
    cp .env.example .env   # 填入 SSO Token
    python search_api.py
"""

import asyncio
import base64
import itertools
import os
import random
import re
import string
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Optional

import orjson
import uvicorn
from curl_cffi.requests import AsyncSession
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

load_dotenv()

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════

SSO_TOKENS: list[str] = [
    t.strip() for t in os.getenv("GROK_SSO_TOKENS", "").split(",") if t.strip()
]
API_KEY: str = os.getenv("API_KEY", "")
CF_CLEARANCE: str = os.getenv("CF_CLEARANCE", "")
USER_AGENT: str = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
)
DEFAULT_MODE: str = os.getenv("DEFAULT_MODE", "auto")
PROXY_URL: str = os.getenv("PROXY_URL", "")
TIMEOUT: float = float(os.getenv("TIMEOUT", "120"))
TEMPORARY: bool = os.getenv("TEMPORARY", "true").lower() in ("true", "1", "yes")
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8000"))

# Token 轮询器
_token_cycle = itertools.cycle(SSO_TOKENS) if SSO_TOKENS else None

# 模式映射（与 grok2api 一致）
MODE_MAP = {
    "auto": "auto",
    "fast": "fast",
    "expert": "expert",
    "heavy": "heavy",
    "deepsearch": "expert",
    "deepersearch": "expert",
}


def _next_token() -> str:
    if _token_cycle is None:
        raise HTTPException(500, "未配置 GROK_SSO_TOKENS")
    return next(_token_cycle)


# ═══════════════════════════════════════════════════════════════════
# Header 构造（提取自 grok2api: headers.py）
# ═══════════════════════════════════════════════════════════════════


def _statsig_id() -> str:
    """生成 x-statsig-id（同 grok2api 的动态指纹）"""
    if random.choice((True, False)):
        rand = "".join(random.choices(string.ascii_lowercase + string.digits, k=5))
        msg = f"e:TypeError: Cannot read properties of null (reading 'children['{rand}']')"
    else:
        rand = "".join(random.choices(string.ascii_lowercase, k=10))
        msg = f"e:TypeError: Cannot read properties of undefined (reading '{rand}')"
    return base64.b64encode(msg.encode()).decode()


def _chrome_version() -> str:
    m = re.search(r"Chrome/(\d+)", USER_AGENT)
    return m.group(1) if m else "136"


def build_headers(sso_token: str) -> dict[str, str]:
    """构造请求头（对齐 grok2api 的 build_http_headers）"""
    tok = sso_token[4:] if sso_token.startswith("sso=") else sso_token
    cookie = f"sso={tok}; sso-rw={tok}"
    if CF_CLEARANCE:
        cookie += f"; cf_clearance={CF_CLEARANCE}"

    ver = _chrome_version()
    headers = {
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
        "Sec-Ch-Ua": f'"Google Chrome";v="{ver}", "Chromium";v="{ver}", "Not(A:Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Model": "",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "User-Agent": USER_AGENT,
        "Cookie": cookie,
        "x-statsig-id": _statsig_id(),
        "x-xai-request-id": str(uuid.uuid4()),
    }
    return headers


# ═══════════════════════════════════════════════════════════════════
# Payload 构造（提取自 grok2api: xai_chat.py → build_chat_payload）
# ═══════════════════════════════════════════════════════════════════


def build_search_payload(query: str, mode: str = "auto") -> dict[str, Any]:
    """
    构造搜索请求体。核心参数来自 grok2api 的 build_chat_payload，
    关键是 disableSearch=False 确保 Grok 执行网页搜索。
    """
    mode_id = MODE_MAP.get(mode.lower(), "auto")
    return {
        "temporary": TEMPORARY,
        "disableSearch": False,        # 核心：启用搜索
        "disableMemory": True,         # 搜索 API 不需要记忆
        "disableTextFollowUps": True,  # 不需要后续推荐
        "disableSelfHarmShortCircuit": False,
        "enableImageGeneration": False,
        "enableImageStreaming": False,
        "enableSideBySide": False,
        "forceConcise": True,          # 搜索场景优先简洁
        "forceSideBySide": False,
        "isAsyncChat": False,
        "returnImageBytes": False,
        "returnRawGrokInXaiRequest": False,
        "searchAllConnectors": False,
        "sendFinalMetadata": True,
        "message": query,
        "modeId": mode_id,
        "fileAttachments": [],
        "imageAttachments": [],
        "collectionIds": [],
        "connectors": [],
        "responseMetadata": {},
        "toolOverrides": {
            "gmailSearch": False,
            "googleCalendarSearch": False,
            "outlookSearch": False,
            "outlookCalendarSearch": False,
            "googleDriveSearch": False,
        },
        "deviceEnvInfo": {
            "darkModeEnabled": False,
            "devicePixelRatio": 2,
            "screenHeight": 1329,
            "screenWidth": 2056,
            "viewportHeight": 1083,
            "viewportWidth": 2056,
        },
        "imageGenerationCount": 0,
    }


# ═══════════════════════════════════════════════════════════════════
# SSE 流解析（提取自 grok2api: xai_chat.py → classify_line + StreamAdapter）
# ═══════════════════════════════════════════════════════════════════


def classify_line(line: str) -> tuple[str, str]:
    """
    分类 SSE 行（对齐 grok2api 的 classify_line）。
    返回 (event_type, data)：'data' | 'done' | 'skip'
    """
    line = line.strip()
    if not line:
        return "skip", ""
    if line.startswith("data:"):
        data = line[5:].strip()
        if data == "[DONE]":
            return "done", ""
        return "data", data
    if line.startswith("event:"):
        return "skip", ""
    if line.startswith("{"):
        return "data", line
    return "skip", ""


def extract_search_results_from_frame(data: str) -> tuple[list[dict], str, list[dict]]:
    """
    从单个 SSE data 帧中提取搜索结果和文本 token。

    返回: (web_search_results, text_token, tool_usages)

    对应 grok2api 的 StreamAdapter.feed() 中处理
    webSearchResults 和 tool_usage_card 的逻辑。
    """
    try:
        obj = orjson.loads(data)
    except (orjson.JSONDecodeError, ValueError, TypeError):
        return [], "", []

    result = obj.get("result")
    if not result:
        return [], "", []
    resp = result.get("response")
    if not resp:
        return [], "", []

    web_results = []
    text_token = ""
    tool_usages = []

    # 提取 webSearchResults（核心搜索数据）
    wsr = resp.get("webSearchResults")
    if wsr and isinstance(wsr, list):
        web_results = wsr

    # 提取文本 token（仅 final 标签）
    token = resp.get("token")
    tag = resp.get("messageTag")
    is_thinking = resp.get("isThinking")
    if token is not None and not is_thinking and tag == "final":
        text_token = str(token)

    # 提取工具调用信息（web_search 的查询词等）
    if tag == "tool_usage_card":
        card = resp.get("toolUsageCard")
        if card and isinstance(card, dict):
            for key, value in card.items():
                if key == "toolUsageCardId" or not isinstance(value, dict):
                    continue
                tool_name = re.sub(r"(?<!^)([A-Z])", r"_\1", key).lower()
                args = value.get("args", {})
                tool_usages.append({"tool": tool_name, "args": args})

    return web_results, text_token, tool_usages


# ═══════════════════════════════════════════════════════════════════
# HTTP 传输层（提取自 grok2api: http.py → post_stream）
# ═══════════════════════════════════════════════════════════════════

CHAT_URL = "https://grok.com/rest/app-chat/conversations/new"


async def grok_search_stream(
    query: str,
    token: str,
    mode: str = "auto",
) -> dict[str, Any]:
    """
    向 Grok 发送搜索请求并收集完整结果。

    对齐 grok2api 的流程：
    1. build_chat_payload → 构造请求
    2. post_stream → 发起 SSE 请求
    3. classify_line + StreamAdapter.feed → 解析每一帧
    4. 收集 webSearchResults + 文本回复
    """
    headers = build_headers(token)
    payload = build_search_payload(query, mode)
    payload_bytes = orjson.dumps(payload)

    # 构造 curl_cffi 会话（对齐 grok2api 的 ResettableSession）
    chrome_ver = _chrome_version()
    session_kwargs: dict[str, Any] = {"impersonate": f"chrome{chrome_ver}"}
    if PROXY_URL:
        session_kwargs["proxies"] = {"http": PROXY_URL, "https": PROXY_URL}

    all_search_results: list[dict] = []
    text_chunks: list[str] = []
    tool_calls: list[dict] = []
    seen_urls: set[str] = set()

    async with AsyncSession(**session_kwargs) as session:
        response = await session.post(
            CHAT_URL,
            headers=headers,
            data=payload_bytes,
            timeout=TIMEOUT,
            stream=True,
        )

        if response.status_code != 200:
            body = ""
            try:
                body = response.content.decode("utf-8", "replace")[:500]
            except Exception:
                pass
            raise HTTPException(
                502,
                f"Grok upstream returned {response.status_code}: {body}",
            )

        # 逐行解析 SSE 流
        async for raw_line in response.aiter_lines():
            event_type, data = classify_line(raw_line)
            if event_type == "done":
                break
            if event_type != "data":
                continue

            web_results, text_token, tools = extract_search_results_from_frame(data)

            # 收集搜索结果（去重）
            for item in web_results:
                url = item.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_search_results.append(item)

            if text_token:
                text_chunks.append(text_token)

            tool_calls.extend(tools)

    # 提取搜索查询词
    search_queries = []
    for tc in tool_calls:
        if tc["tool"] == "web_search":
            q = tc["args"].get("query") or tc["args"].get("q", "")
            if q and q not in search_queries:
                search_queries.append(q)

    return {
        "query": query,
        "mode": mode,
        "search_results": all_search_results,
        "search_queries": search_queries,
        "answer": "".join(text_chunks),
        "total_results": len(all_search_results),
        "total_search_queries": len(search_queries),
    }


# ═══════════════════════════════════════════════════════════════════
# 鉴权中间件
# ═══════════════════════════════════════════════════════════════════


def verify_auth(authorization: Optional[str] = Header(None)):
    if not API_KEY:
        return  # 未配置则跳过鉴权
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.replace("Bearer ", "").strip()
    if token != API_KEY:
        raise HTTPException(403, "Invalid API key")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 应用
# ═══════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not SSO_TOKENS:
        print("⚠️  警告: 未配置 GROK_SSO_TOKENS，请在 .env 中设置")
    else:
        print(f"✅ 已加载 {len(SSO_TOKENS)} 个 SSO Token")
    print(f"🔍 Grok Search API 启动: http://{HOST}:{PORT}")
    print(f"📖 API 文档: http://{HOST}:{PORT}/docs")
    yield


app = FastAPI(
    title="Grok Search API",
    description=(
        "从 grok2api 核心提取的独立搜索服务。"
        "将 Grok Web 的搜索能力封装为 REST API。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class OrjsonResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


# ═══════════════════════════════════════════════════════════════════
# API 路由
# ═══════════════════════════════════════════════════════════════════


@app.get("/", response_class=OrjsonResponse)
async def root():
    return {
        "name": "Grok Search API",
        "version": "1.0.0",
        "endpoints": {
            "search": "POST /v1/search",
            "health": "GET /health",
            "docs": "GET /docs",
        },
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "tokens_configured": len(SSO_TOKENS),
        "timestamp": time.time(),
    }


@app.post("/v1/search", response_class=OrjsonResponse)
async def search(
    request: dict[str, Any],
    authorization: Optional[str] = Header(None),
):
    """
    搜索接口 — Grok Search API 的核心端点。

    请求体:
    ```json
    {
        "query": "搜索关键词",
        "mode": "auto"          // 可选: auto, fast, expert, deepsearch
    }
    ```

    返回:
    ```json
    {
        "query": "搜索关键词",
        "mode": "auto",
        "search_results": [
            {
                "title": "...",
                "url": "...",
                "preview": "..."
            }
        ],
        "search_queries": ["Grok 实际执行的搜索词1", "..."],
        "answer": "Grok 的文字回复",
        "total_results": 30,
        "total_search_queries": 3
    }
    ```
    """
    verify_auth(authorization)

    query = request.get("query", "").strip()
    if not query:
        raise HTTPException(400, "缺少 query 参数")

    mode = request.get("mode", DEFAULT_MODE)
    if mode not in MODE_MAP:
        raise HTTPException(400, f"无效的 mode: {mode}，可选: {list(MODE_MAP.keys())}")

    token = _next_token()

    try:
        result = await grok_search_stream(query, token, mode)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"搜索请求失败: {str(e)}")

    return result


@app.post("/v1/search/batch", response_class=OrjsonResponse)
async def search_batch(
    request: dict[str, Any],
    authorization: Optional[str] = Header(None),
):
    """
    批量搜索接口 — 并发执行多个搜索查询。

    请求体:
    ```json
    {
        "queries": ["关键词1", "关键词2", "关键词3"],
        "mode": "auto",
        "concurrency": 3
    }
    ```
    """
    verify_auth(authorization)

    queries = request.get("queries", [])
    if not queries or not isinstance(queries, list):
        raise HTTPException(400, "缺少 queries 参数（数组）")
    if len(queries) > 20:
        raise HTTPException(400, "单次批量最多 20 个查询")

    mode = request.get("mode", DEFAULT_MODE)
    concurrency = min(request.get("concurrency", 3), 5)

    semaphore = asyncio.Semaphore(concurrency)

    async def _search_one(q: str) -> dict:
        async with semaphore:
            token = _next_token()
            try:
                return await grok_search_stream(q, token, mode)
            except Exception as e:
                return {"query": q, "error": str(e), "search_results": []}

    tasks = [_search_one(q.strip()) for q in queries if q.strip()]
    results = await asyncio.gather(*tasks)

    total_results = sum(r.get("total_results", 0) for r in results)
    return {
        "batch_size": len(results),
        "total_results": total_results,
        "results": results,
    }


# ═══════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "search_api:app",
        host=HOST,
        port=PORT,
        log_level="info",
    )
