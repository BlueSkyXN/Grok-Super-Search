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


class SearchResultItem(BaseModel):
    """单条搜索结果"""
    title: str = ""
    url: str = ""
    preview: str = ""


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
