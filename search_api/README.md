# Grok Search API

从 [grok2api](https://github.com/chenyme/grok2api) 核心架构提取的独立搜索服务。将 Grok Web 的搜索能力封装为 REST API，可作为 [SouWen](https://github.com/BlueSkyXN/SouWen) 搜索平台的数据源接入。

## 架构设计

```
用户请求 → FastAPI 路由 → Token 号池（轮询/冷却）→ 拼装提示词
    → Grok SSE 流（/rest/app-chat/conversations/new）
    → 解析帧 → 提取 webSearchResults → 返回 JSON
```

### 与相关项目的关系

| 项目 | 角色 | 关系 |
|------|------|------|
| **grok2api** | 通用 Grok 网关 | 提供核心参考：Header 构造、Payload、SSE 解析 |
| **SouWen** | 聚合搜索平台 | 提供架构参考：FastAPI 服务、HTTP 客户端、Provider 模式 |
| **GrokHelper.js** | 浏览器油猴脚本 | 提供数据参考：webSearchResults 提取路径 |
| **本项目** | 搜索 API 服务 | 综合以上三者，只做搜索结果提取 |

### 核心提取

从 **grok2api** 提取：
1. `build_http_headers` → Header 构造（SSO Cookie、x-statsig-id 指纹、Client Hints）
2. `build_chat_payload` → 对话请求体（`disableSearch=False` 启用搜索）
3. `classify_line` + `StreamAdapter` → SSE 流解析
4. `post_stream` → curl_cffi 浏览器指纹模拟传输

从 **SouWen** 复用：
1. FastAPI 应用结构（lifespan、中间件、异常处理）
2. httpx 异步 HTTP 客户端
3. Pydantic v2 请求/响应模型
4. 无数据库配置模式（纯环境变量）

从 **GrokHelper.js** 提取：
1. `webSearchResults` 的两条提取路径（直接 / message 嵌套）
2. URL 去重逻辑

## 快速开始

### 本地运行

```bash
cd search_api
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入 Grok SSO Token
python -m search_api
```

### Docker

```bash
cd search_api
docker build -t grok-search-api .
docker run -p 8000:8000 --env-file .env grok-search-api
```

### HuggingFace Space

1. 新建 Space（Docker SDK）
2. 上传 `search_api/` 目录内容
3. 在 Space Settings → Secrets 中添加 `GROK_SSO_TOKENS`

## 获取 SSO Token

1. 浏览器登录 [grok.com](https://grok.com)
2. DevTools → Application → Cookies → 复制 `sso` 的值
3. 填入 `.env` 的 `GROK_SSO_TOKENS`

多 Token 逗号分隔，自动轮询 + 冷却：
```
GROK_SSO_TOKENS=token1,token2,token3
```

## API 接口

### `POST /v1/search` — 单次搜索

```bash
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_api_key" \
  -d '{"query": "2024年最新的AI框架", "mode": "auto"}'
```

**响应：**
```json
{
  "query": "2024年最新的AI框架",
  "mode": "auto",
  "search_queries": ["latest AI frameworks 2024"],
  "search_results": [
    {"title": "...", "url": "https://...", "preview": "..."}
  ],
  "total_results": 15,
  "total_search_queries": 1
}
```

### `POST /v1/search/batch` — 批量搜索

```bash
curl -X POST http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -d '{
    "queries": ["AI框架对比", "LLM最新进展"],
    "mode": "auto",
    "concurrency": 3
  }'
```

### `GET /health` — 健康检查

### `GET /admin/pool` — Token 池状态

### `POST /admin/pool/enable-all` — 重新启用所有 Token

### `GET /docs` — Swagger 文档

## 文件结构

```
search_api/
├── __init__.py          # 包声明
├── __main__.py          # 入口（python -m search_api）
├── app.py               # FastAPI 应用（路由、中间件、异常处理）
├── config.py            # 配置管理（纯环境变量）
├── token_pool.py        # Token 号池（轮询、冷却、禁用）
├── grok_search.py       # 搜索核心（Payload 构造、SSE 解析、结果提取）
├── http_client.py       # HTTP 传输（httpx/curl_cffi 双后端）
├── schemas.py           # Pydantic 请求/响应模型
├── requirements.txt     # 依赖
├── .env.example         # 配置模板
├── .gitignore
├── Dockerfile           # Docker 部署
└── README.md            # 本文件
```

## 配置说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GROK_SSO_TOKENS` | SSO Token 列表（逗号分隔） | **必填** |
| `API_KEY` | API 鉴权密钥 | 空（不鉴权） |
| `CF_CLEARANCE` | Cloudflare Cookie | 空 |
| `USER_AGENT` | 浏览器 UA | Chrome 136 |
| `DEFAULT_MODE` | 默认搜索模式 | auto |
| `HTTP_BACKEND` | HTTP 后端 | httpx |
| `PROXY_URL` | HTTP/SOCKS5 代理 | 空 |
| `TIMEOUT` | 请求超时（秒） | 120 |
| `COOLDOWN` | Token 冷却（秒） | 3 |
| `TEMPORARY` | 临时对话 | true |
| `SEARCH_PROMPT_TEMPLATE` | 搜索提示词模板 | 见 .env.example |

## 作为 SouWen 数据源

本服务可以作为 SouWen 平台的自定义 Web 搜索引擎接入，
在 SouWen 的配置中添加自定义 HTTP 源指向本服务的 `/v1/search` 端点即可。

## License

MIT
