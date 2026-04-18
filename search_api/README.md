# Grok Search API

从 [grok2api](https://github.com/chenyme/grok2api) 核心架构提取的独立搜索服务。将 Grok Web 的搜索能力封装为标准 REST API。

## 与 grok2api 的关系

| 对比项 | grok2api | Grok Search API |
|--------|----------|-----------------|
| 定位 | 通用 Grok 网关（Chat/Image/Video） | 专注搜索结果提取 |
| 接口 | OpenAI/Anthropic 兼容 | 搜索专用 REST API |
| 认证 | 多账号池 + Admin 管理 | 单/多 SSO Token 轮询 |
| 依赖 | FastAPI + curl_cffi + Redis/MySQL/... | FastAPI + curl_cffi（极简） |
| 部署 | Docker Compose | 单文件 / Docker |

### 核心提取的内容

1. **Header 构造**（`build_http_headers`）— SSO Cookie、x-statsig-id 指纹、Client Hints
2. **Payload 构造**（`build_chat_payload`）— 对话请求体，`disableSearch=False` 启用搜索
3. **SSE 流解析**（`classify_line` + `StreamAdapter`）— 解析 Grok 的 SSE 流，提取 `webSearchResults`
4. **传输层**（`post_stream`）— curl_cffi 浏览器指纹模拟

## 快速开始

### 本地运行

```bash
cd search_api
pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，填入你的 Grok SSO Token
python search_api.py
```

### Docker

```bash
cd search_api
cp .env.example .env
# 编辑 .env
docker build -t grok-search-api .
docker run -p 8000:8000 --env-file .env grok-search-api
```

## 获取 SSO Token

1. 浏览器登录 [grok.com](https://grok.com)
2. 打开 DevTools → Application → Cookies
3. 复制 `sso` 的值（不含 `sso=` 前缀）
4. 填入 `.env` 的 `GROK_SSO_TOKENS`

支持多 Token 逗号分隔，自动轮询：
```
GROK_SSO_TOKENS=token1,token2,token3
```

## API 接口

### POST /v1/search

单次搜索。

**请求：**
```bash
curl http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_api_key" \
  -d '{
    "query": "2024年最新的AI框架有哪些",
    "mode": "auto"
  }'
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| query | string | ✅ | 搜索关键词 |
| mode | string | ❌ | 搜索模式：`auto`（默认）/ `fast` / `expert` / `deepsearch` |

**响应：**
```json
{
  "query": "2024年最新的AI框架有哪些",
  "mode": "auto",
  "search_results": [
    {
      "title": "Top AI Frameworks 2024",
      "url": "https://example.com/ai-frameworks",
      "preview": "..."
    }
  ],
  "search_queries": ["latest AI frameworks 2024", "new AI tools"],
  "answer": "Grok 的文字回复...",
  "total_results": 15,
  "total_search_queries": 2
}
```

### POST /v1/search/batch

批量搜索（并发执行多个查询）。

**请求：**
```bash
curl http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your_api_key" \
  -d '{
    "queries": ["AI框架对比", "LLM最新进展", "向量数据库选型"],
    "mode": "auto",
    "concurrency": 3
  }'
```

**参数：**
| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| queries | string[] | ✅ | 搜索关键词数组（最多 20 个） |
| mode | string | ❌ | 搜索模式 |
| concurrency | int | ❌ | 并发数（默认 3，最大 5） |

### GET /health

健康检查。

### GET /docs

Swagger UI 自动文档。

## 与 GrokHelper.js 的协作

GrokHelper.js 是浏览器端 UserScript，负责从**已有对话**导出搜索结果：
- 通过 `/rest/app-chat/conversations/{id}/response-node` 获取对话树
- 通过 `/rest/app-chat/conversations/{id}/load-responses` 加载完整响应
- 提取 `webSearchResults` 并导出为 JSON/CSV

Search API 是服务端程序，负责**主动发起新搜索**：
- 通过 `/rest/app-chat/conversations/new` 发起新对话
- 解析 SSE 流实时提取搜索结果
- 以 REST API 形式对外提供服务

两者互补：
```
用户 → Search API (POST /v1/search) → Grok SSE → 搜索结果 JSON
用户 → GrokHelper.js (浏览器) → 已有对话 → 导出 JSON/CSV
```

## 配置说明

详见 `.env.example`，关键配置：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GROK_SSO_TOKENS` | SSO Token（必填，逗号分隔） | - |
| `API_KEY` | API 鉴权密钥 | 空（不鉴权） |
| `CF_CLEARANCE` | Cloudflare Cookie | 空 |
| `USER_AGENT` | 浏览器 UA | Chrome 136 |
| `DEFAULT_MODE` | 默认搜索模式 | auto |
| `PROXY_URL` | HTTP 代理 | 空 |
| `TIMEOUT` | 请求超时秒数 | 120 |
| `TEMPORARY` | 临时对话 | true |

## License

MIT — 与 grok2api 一致。
