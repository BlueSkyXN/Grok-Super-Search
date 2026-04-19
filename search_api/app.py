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
from contextlib import asynccontextmanager
from typing import Any, Optional

import orjson
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings, reload_settings
from .grok_search import execute_search, query_rate_limits, GrokUpstreamError
from .schemas import (
    BatchSearchRequest,
    BatchSearchResponse,
    ErrorResponse,
    HealthResponse,
    PromptListResponse,
    PromptTemplateInfo,
    QuotaResponse,
    SearchRequest,
    SearchResponse,
    TokenStatusResponse,
)
from .token_pool import get_token_pool

logger = logging.getLogger("grok_search")

VERSION = "2.0.0"


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
    logger.info("📝 已加载 %d 个提示词模板", len(settings.prompt_templates))
    logger.info("🎯 默认模板: %s", settings.default_prompt_id)

    yield  # 运行中

    logger.info("🛑 Grok Search API 关闭")


# ═══════════════════════════════════════════════════════════════════
# FastAPI 实例
# ═══════════════════════════════════════════════════════════════════


app = FastAPI(
    title="Grok Search API",
    description=(
        "从 grok2api 核心提取的独立搜索服务，将 Grok Web 的搜索能力封装为 REST API。\n\n"
        "- 多 Token 号池管理（轮询 + 冷却 + 额度查询）\n"
        "- 可配置提示词模板系统（config.json）\n"
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
# 核心路由
# ═══════════════════════════════════════════════════════════════════


@app.get("/")
async def root():
    return {
        "name": "Grok Search API",
        "version": VERSION,
        "endpoints": {
            "search": "POST /v1/search",
            "batch_search": "POST /v1/search/batch",
            "prompts": "GET /v1/prompts",
            "health": "GET /health",
            "quota": "GET /admin/quota",
            "pool_status": "GET /admin/pool",
            "config_reload": "POST /admin/config/reload",
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


@app.get("/v1/prompts", response_model=PromptListResponse)
async def list_prompts():
    """列出所有可用的提示词模板"""
    settings = get_settings()
    templates = [
        PromptTemplateInfo(
            id=t.id,
            name=t.name,
            description=t.description,
            mode=t.mode,
            template_preview=t.template[:200] + ("..." if len(t.template) > 200 else ""),
        )
        for t in settings.prompt_templates
    ]
    return PromptListResponse(
        total=len(templates),
        default_prompt_id=settings.default_prompt_id,
        templates=templates,
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

    可通过 prompt_id 选择不同的提示词模板（见 GET /v1/prompts）。
    """
    _verify_auth(authorization)

    pool = get_token_pool()
    slot = await pool.acquire()
    if slot is None:
        raise HTTPException(503, "无可用 Token（全部禁用或冷却中）")

    try:
        result = await execute_search(
            body.query, slot, body.mode, body.prompt_id,
        )
        await pool.release(slot, error=False)
        return SearchResponse(**result)
    except GrokUpstreamError as e:
        await pool.release(slot, error=True)
        if e.status_code in (401, 403):
            await pool.disable(slot, f"HTTP {e.status_code}")
            logger.warning(
                "Token %s... 已禁用: %s",
                slot.token[:4],
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
    """批量搜索接口 — 并发执行多个搜索查询。"""
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
                    "prompt_id": body.prompt_id or get_settings().default_prompt_id,
                    "search_queries": [],
                    "search_results": [],
                    "total_results": 0,
                    "total_search_queries": 0,
                    "error": "no_available_token",
                }
            try:
                result = await execute_search(
                    query, slot, body.mode, body.prompt_id,
                )
                await pool.release(slot, error=False)
                return result
            except GrokUpstreamError as e:
                await pool.release(slot, error=True)
                if e.status_code in (401, 403):
                    await pool.disable(slot, f"HTTP {e.status_code}")
                return {
                    "query": query,
                    "mode": body.mode,
                    "prompt_id": body.prompt_id or get_settings().default_prompt_id,
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
                    "prompt_id": body.prompt_id or get_settings().default_prompt_id,
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


# ═══════════════════════════════════════════════════════════════════
# Admin 路由
# ═══════════════════════════════════════════════════════════════════


@app.get("/admin/pool", response_model=TokenStatusResponse)
async def pool_status(authorization: Optional[str] = Header(None)):
    """查看 Token 池状态（运行时内存数据）"""
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


@app.get("/admin/quota", response_model=QuotaResponse)
async def check_quota(
    token_index: int | None = None,
    authorization: Optional[str] = Header(None),
):
    """
    查询 Grok 账号额度（移植自 GrokHelper.js 的 rate-limits 查询）。

    可指定 token_index 查询特定 Token，否则自动选一个可用的。
    返回 Fast/Expert/Heavy 三个模型池的剩余额度。
    """
    _verify_auth(authorization)

    pool = get_token_pool()

    if token_index is not None:
        slots = pool.status()
        if token_index < 0 or token_index >= len(slots):
            raise HTTPException(400, f"无效的 token_index: {token_index}")
        slot = pool.get_slot_by_index(token_index)
        if slot is None or slot.disabled:
            raise HTTPException(400, f"Token #{token_index} 已禁用")
    else:
        slot = await pool.acquire()
        if slot is None:
            raise HTTPException(503, "无可用 Token")

    try:
        result = await query_rate_limits(slot)
        if token_index is None:
            await pool.release(slot, error=False)
        return QuotaResponse(**result)
    except Exception as e:
        if token_index is None:
            await pool.release(slot, error=True)
        raise HTTPException(502, f"额度查询失败: {e}")


@app.post("/admin/config/reload")
async def config_reload(authorization: Optional[str] = Header(None)):
    """
    热重载配置文件（config.json）。

    运行时无需重启即可更新提示词模板、冷却参数等。
    注意：Token 列表变更需要重启服务。
    """
    _verify_auth(authorization)
    settings = reload_settings()
    return {
        "status": "ok",
        "templates_loaded": len(settings.prompt_templates),
        "default_mode": settings.default_mode,
        "default_prompt_id": settings.default_prompt_id,
        "cooldown": settings.cooldown,
    }
