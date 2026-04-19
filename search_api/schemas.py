"""
Pydantic v2 请求/响应模型 — 对齐 SouWen 的 server/schemas.py 风格。
"""

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """搜索请求"""
    query: str = Field(..., min_length=1, max_length=2000, description="搜索关键词")
    mode: str = Field(
        default="auto",
        description="搜索模式: auto / fast / expert / deepsearch",
    )
    prompt_id: str | None = Field(
        default=None,
        description="提示词模板 ID（见 /v1/prompts 或 config.json）",
    )


class BatchSearchRequest(BaseModel):
    """批量搜索请求"""
    queries: list[str] = Field(
        ..., min_length=1, max_length=20, description="搜索关键词列表"
    )
    mode: str = Field(default="auto", description="搜索模式")
    prompt_id: str | None = Field(default=None, description="提示词模板 ID")
    concurrency: int = Field(default=3, ge=1, le=5, description="并发数")


class SearchResponse(BaseModel):
    """搜索响应"""
    query: str
    mode: str
    prompt_id: str = "default"
    search_queries: list[str] = Field(
        default_factory=list,
        description="Grok 实际执行的搜索词",
    )
    search_results: list[dict] = Field(
        default_factory=list,
        description="原始搜索结果（保留 Grok 返回的完整字段）",
    )
    total_results: int = 0
    total_search_queries: int = 0
    error: str | None = Field(
        default=None,
        description="错误信息（仅批量搜索时部分失败会填充）",
    )


class BatchSearchResponse(BaseModel):
    """批量搜索响应"""
    batch_size: int
    total_results: int
    results: list[SearchResponse]


class HealthResponse(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    tokens_total: int
    tokens_available: int
    version: str


class TokenStatusResponse(BaseModel):
    """Token 池状态响应"""
    total: int
    available: int
    slots: list[dict]


class PromptTemplateInfo(BaseModel):
    """提示词模板信息"""
    id: str
    name: str
    description: str = ""
    mode: str = "auto"
    template_preview: str = ""  # 前 200 字符


class PromptListResponse(BaseModel):
    """提示词模板列表"""
    total: int
    default_prompt_id: str
    templates: list[PromptTemplateInfo]


class QuotaResponse(BaseModel):
    """额度查询响应"""
    token_prefix: str
    total_remaining: int
    limits: dict


class ErrorResponse(BaseModel):
    """错误响应（对齐 SouWen）"""
    error: str
    detail: str


# ═══════════════════════════════════════════════════════════════════
# WARP 相关模型
# ═══════════════════════════════════════════════════════════════════


class WarpEnableRequest(BaseModel):
    """WARP 启用请求"""
    mode: str = Field(
        default="auto",
        description="WARP 模式: auto / wireproxy / kernel",
    )
    socks_port: int = Field(
        default=1080,
        ge=1,
        le=65535,
        description="本地 SOCKS5 监听端口",
    )
    endpoint: str | None = Field(
        default=None,
        description="自定义 WARP Endpoint（可选）",
    )


class WarpStatusResponse(BaseModel):
    """WARP 状态响应"""
    status: str = Field(description="disabled / starting / enabled / stopping / error")
    mode: str = Field(description="auto / wireproxy / kernel")
    owner: str = Field(description="none / shell / python")
    socks_port: int
    ip: str = ""
    pid: int = 0
    interface: str | None = None
    last_error: str = ""
    available_modes: dict = Field(default_factory=dict)


class SystemInfoResponse(BaseModel):
    """系统信息响应（含 HTTP 后端、代理、WARP 状态）"""
    version: str
    http_backend: str = Field(description="实际使用的 HTTP 后端")
    http_backend_config: str = Field(description="配置的 HTTP 后端值")
    curl_cffi_available: bool
    fingerprint_rotation: bool
    proxy: str = Field(default="", description="当前生效的代理 URL")
    proxy_pool_size: int = Field(default=0, description="代理池大小")
    warp_status: str = Field(default="disabled", description="WARP 代理状态")
    tokens_total: int
    tokens_available: int
