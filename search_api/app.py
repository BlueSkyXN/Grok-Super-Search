"""
FastAPI 应用 — 对齐 SouWen 的 server/app.py 结构。

入口文件，负责：
- FastAPI 生命周期管理
- 中间件注册
- 全局异常处理
- 路由挂载
"""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Optional

import orjson
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .grok_search import execute_search, GrokUpstreamError
from .schemas import (
    BatchSearchRequest,
    BatchSearchResponse,
    ErrorResponse,
    HealthResponse,
    SearchRequest,
    SearchResponse,
    TokenStatusResponse,
)
from .token_pool import get_token_pool

logger = logging.getLogger("grok_search")

VERSION = "1.0.0"


# ═══════════════════════════════════════════════════════════════════
# JSON 响应（使用 orjson，对齐 SouWen）
# ═══════════════════════════════════════════════════════════════════


class OrjsonResponse(JSONResponse):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)


# ═══════════════════════════════════════════════════════════════════
# 鉴权（对齐 SouWen 的 server/auth.py）
# ═══════════════════════════════════════════════════════════════════


def _verify_auth(authorization: Optional[str]) -> None:
    settings = get_settings()
    if not settings.api_key:
        return  # 未配置则不鉴权
    if not authorization:
        raise HTTPException(401, "Missing Authorization header")
    token = authorization.replace("Bearer ", "").strip()
    if token != settings.api_key:
        raise HTTPException(403, "Invalid API key")


# ═══════════════════════════════════════════════════════════════════
# 生命周期（对齐 SouWen 的 lifespan 模式）
# ═══════════════════════════════════════════════════════════════════


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    pool = get_token_pool()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not settings.sso_tokens:
        logger.warning("⚠️  GROK_SSO_TOKENS 未配置，请在 .env 中设置")
    else:
        logger.info("✅ 已加载 %d 个 SSO Token", pool.total)

    logger.info("🔍 Grok Search API v%s", VERSION)
    logger.info("📖 文档: http://%s:%d/docs", settings.host, settings.port)
    logger.info("🔧 HTTP 后端: %s", settings.http_backend)
    logger.info("🔄 默认模式: %s", settings.default_mode)

    yield  # 运行中

    logger.info("🛑 Grok Search API 关闭")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 实例
# ═══════════════════════════════════════════════════════════════════


app = FastAPI(
    title="Grok Search API",
    description=(
        "从 grok2api 核心提取的独立搜索服务，将 Grok Web 的搜索能力封装为 REST API。\n\n"
        "- 多 Token 号池管理（轮询 + 冷却）\n"
        "- 预设提示词模板 + 搜索词拼装\n"
        "- 仅提取 webSearchResults，无视 AI 文字回答\n"
        "- 支持 httpx / curl_cffi 双后端\n"
        "- 无数据库，适合 HF Space / Docker 部署"
    ),
    version=VERSION,
    lifespan=lifespan,
    default_response_class=OrjsonResponse,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════
# 全局异常处理（对齐 SouWen）
# ═══════════════════════════════════════════════════════════════════


@app.exception_handler(GrokUpstreamError)
async def grok_upstream_handler(request: Request, exc: GrokUpstreamError):
    # 401/403 时自动禁用对应 Token
    detail = f"Grok upstream error: {exc.status_code}"
    if exc.body:
        detail += f" - {exc.body[:200]}"
    return OrjsonResponse(
        status_code=502,
        content={"error": "upstream_error", "detail": detail},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    logger.exception("unhandled exception: %s", exc)
    return OrjsonResponse(
        status_code=500,
        content={"error": "internal_error", "detail": str(exc)},
    )


# ═══════════════════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    return {
        "name": "Grok Search API",
        "version": VERSION,
        "endpoints": {
            "search": "POST /v1/search",
            "batch_search": "POST /v1/search/batch",
            "health": "GET /health",
            "pool_status": "GET /admin/pool",
            "docs": "GET /docs",
        },
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    pool = get_token_pool()
    return HealthResponse(
        status="ok",
        tokens_total=pool.total,
        tokens_available=pool.available,
        version=VERSION,
    )


@app.post("/v1/search", response_model=SearchResponse)
async def search(
    body: SearchRequest,
    authorization: Optional[str] = Header(None),
):
    """
    搜索接口 — 核心端点。

    接收搜索关键词，拼装预设提示词后发给 Grok，
    从 SSE 流中提取 webSearchResults 并返回。
    """
    _verify_auth(authorization)

    pool = get_token_pool()
    slot = await pool.acquire()
    if slot is None:
        raise HTTPException(503, "无可用 Token（全部禁用或冷却中）")

    try:
        result = await execute_search(body.query, slot, body.mode)
        await pool.release(slot, error=False)
        return SearchResponse(**result)
    except GrokUpstreamError as e:
        await pool.release(slot, error=True)
        # 401/403 自动禁用 Token
        if e.status_code in (401, 403):
            await pool.disable(slot, f"HTTP {e.status_code}")
            logger.warning(
                "Token %s... 已禁用: %s",
                slot.token[:8],
                e.status_code,
            )
        raise
    except Exception:
        await pool.release(slot, error=True)
        raise


@app.post("/v1/search/batch", response_model=BatchSearchResponse)
async def search_batch(
    body: BatchSearchRequest,
    authorization: Optional[str] = Header(None),
):
    """
    批量搜索接口 — 并发执行多个搜索查询。
    """
    _verify_auth(authorization)

    pool = get_token_pool()
    semaphore = asyncio.Semaphore(body.concurrency)

    async def _search_one(query: str) -> dict:
        async with semaphore:
            slot = await pool.acquire()
            if slot is None:
                return {
                    "query": query,
                    "mode": body.mode,
                    "search_queries": [],
                    "search_results": [],
                    "total_results": 0,
                    "total_search_queries": 0,
                    "error": "no_available_token",
                }
            try:
                result = await execute_search(query, slot, body.mode)
                await pool.release(slot, error=False)
                return result
            except GrokUpstreamError as e:
                await pool.release(slot, error=True)
                if e.status_code in (401, 403):
                    await pool.disable(slot, f"HTTP {e.status_code}")
                return {
                    "query": query,
                    "mode": body.mode,
                    "search_queries": [],
                    "search_results": [],
                    "total_results": 0,
                    "total_search_queries": 0,
                    "error": str(e),
                }
            except Exception as e:
                await pool.release(slot, error=True)
                return {
                    "query": query,
                    "mode": body.mode,
                    "search_queries": [],
                    "search_results": [],
                    "total_results": 0,
                    "total_search_queries": 0,
                    "error": str(e),
                }

    queries = [q.strip() for q in body.queries if q.strip()]
    tasks = [_search_one(q) for q in queries]
    results = await asyncio.gather(*tasks)

    total = sum(r.get("total_results", 0) for r in results)
    return BatchSearchResponse(
        batch_size=len(results),
        total_results=total,
        results=[SearchResponse(**r) for r in results],
    )


# ── Admin 路由 ──


@app.get("/admin/pool", response_model=TokenStatusResponse)
async def pool_status(authorization: Optional[str] = Header(None)):
    """查看 Token 池状态"""
    _verify_auth(authorization)
    pool = get_token_pool()
    return TokenStatusResponse(
        total=pool.total,
        available=pool.available,
        slots=pool.status(),
    )


@app.post("/admin/pool/enable-all")
async def pool_enable_all(authorization: Optional[str] = Header(None)):
    """重新启用所有被禁用的 Token"""
    _verify_auth(authorization)
    pool = get_token_pool()
    count = await pool.enable_all()
    return {"enabled": count, "total": pool.total}
