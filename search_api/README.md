# Grok Search API

从 [grok2api](https://github.com/chenyme/grok2api) 核心架构提取的独立搜索服务。将 Grok Web 的搜索能力封装为 REST API，可作为 [SouWen](https://github.com/BlueSkyXN/SouWen) 搜索平台的数据源接入。

## 核心特性

- **发请求 → 抓搜索结果 → 忽略 AI 回答**：只做搜索封装，不做通用 Chat 转发
- **多 Token 号池**：轮询 + 冷却 + 自动禁用 + 额度查询
- **提示词模板系统**：config.json 配置，内置 6 种深度搜索模板（取自 Prompt.md）
- **配置分层**：敏感数据 → .env / 持久配置 → config.json / 运行时状态 → 内存
- **无数据库**：适合 HF Space / Docker 单实例部署

### 新增能力（参考 SouWen）

- **Cloudflare WARP 代理**：支持 wireproxy（用户态）和 kernel（内核 WireGuard）双模式
- **curl_cffi TLS 指纹**：可选后端，支持 JA3/TLS 指纹伪装绕过反爬检测
- **浏览器指纹轮换**：每次请求随机 Chrome/Edge/Safari 指纹（UA + Sec-CH-UA + TLS）
- **代理池**：多代理随机选取，支持 HTTP/SOCKS5/SOCKS4
- **HTTP 后端自动降级**：curl_cffi 不可用时自动降级到 httpx
- **SouWen 兼容接口**：可直接接入 SouWen 搜索平台

## 架构设计

```
用户请求 → FastAPI 路由 → Token 号池（轮询/冷却）
    → 选择提示词模板 → 拼装 {query}
    → 选择 HTTP 后端（httpx/curl_cffi 自动降级）
    → 选择代理（WARP > 代理池 > 单一代理）
    → 浏览器指纹轮换（UA + Sec-CH-UA + TLS）
    → Grok SSE 流（/rest/app-chat/conversations/new）
    → 解析帧 → 提取 webSearchResults → 返回 JSON
```

### 配置分层

| 存储位置 | 内容 | 何时变化 |
|---------|------|---------|
| `.env` | SSO Token、API Key、代理 | 部署时设置 |
| `config.json` | 提示词模板、冷却参数、模式 | 运行中可热更新 |
| 内存 | Token 用量、冷却计时器、额度缓存 | 每次请求更新 |

### 与相关项目的关系

| 项目 | 角色 | 关系 |
|------|------|------|
| **grok2api** | 通用 Grok 网关 | 提供核心参考：Header 构造、Payload、SSE 解析 |
| **SouWen** | 聚合搜索平台 | 提供架构参考：FastAPI 服务、HTTP 客户端 |
| **GrokHelper.js** | 浏览器油猴脚本 | 提供数据参考：webSearchResults 提取路径、额度查询 |
| **本项目** | 搜索 API 服务 | 综合以上三者，只做搜索结果提取 |

## 快速开始

### 本地运行

```bash
cd search_api
pip install -r requirements.txt

# 可选：安装 curl_cffi 以启用 TLS 指纹模拟
pip install curl_cffi

cp .env.example .env
# 编辑 .env，填入 Grok SSO Token
python -m search_api
# 首次运行会自动生成 config.json（含内置模板）
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
  -d '{"query": "2024年最新的AI框架", "mode": "auto", "prompt_id": "deep_research"}'
```

**参数：**
| 参数 | 类型 | 说明 |
|------|------|------|
| query | string | 搜索关键词（必填） |
| mode | string | 搜索模式: auto/fast/expert（可选） |
| prompt_id | string | 提示词模板 ID（可选，见 `/v1/prompts`） |

**响应：**
```json
{
  "query": "2024年最新的AI框架",
  "mode": "expert",
  "prompt_id": "deep_research",
  "search_queries": ["latest AI frameworks 2024", "..."],
  "search_results": [
    {"title": "...", "url": "https://...", "preview": "..."}
  ],
  "total_results": 150,
  "total_search_queries": 30
}
```

### `GET /v1/prompts` — 查看提示词模板

```bash
curl http://localhost:8000/v1/prompts
```

内置模板：
| ID | 名称 | 说明 |
|----|------|------|
| `default` | 默认搜索 | 简单搜索 |
| `deep_research` | 深度调研 | 300 子话题裂变，海量结果 |
| `multi_angle` | 多角度裂变 | 50 子话题搜索 |
| `topic_scan` | 主题扫描 | 围绕主题 30 次搜索 |
| `question_matrix` | 问题矩阵 | 20 种问句模式 |
| `site_sweep` | 站点遍历 | 18 个主流网站逐一搜索 |

### `POST /v1/search/batch` — 批量搜索

```bash
curl -X POST http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -d '{
    "queries": ["AI框架对比", "LLM最新进展"],
    "mode": "auto",
    "prompt_id": "topic_scan",
    "concurrency": 3
  }'
```

### `GET /admin/quota` — 查询额度

```bash
curl http://localhost:8000/admin/quota
# 指定 Token：
curl "http://localhost:8000/admin/quota?token_index=0"
```

返回 Fast/Expert/Heavy 三个模型池的剩余额度（移植自 GrokHelper.js）。

### `POST /admin/config/reload` — 热更新配置

```bash
curl -X POST http://localhost:8000/admin/config/reload
```

修改 `config.json` 后无需重启，调用此接口即可生效。

### 其他端点

- `GET /health` — 健康检查
- `GET /admin/system` — 系统信息（HTTP 后端、代理、WARP、curl_cffi 状态）
- `GET /admin/pool` — Token 池状态
- `POST /admin/pool/enable-all` — 重新启用所有 Token
- `GET /admin/warp/status` — WARP 代理状态
- `POST /admin/warp/enable` — 启用 WARP 代理
- `POST /admin/warp/disable` — 禁用 WARP 代理
- `GET /docs` — Swagger 文档

## 配置文件 config.json

首次运行自动生成，示例：

```json
{
  "default_mode": "auto",
  "default_prompt_id": "default",
  "temporary": true,
  "timeout": 120,
  "cooldown": 3,
  "http_backend": "httpx",
  "prompt_templates": [
    {
      "id": "default",
      "name": "默认搜索",
      "description": "简单搜索",
      "template": "请搜索以下内容并给出详细的搜索结果：\n\n{query}",
      "mode": "auto"
    },
    {
      "id": "my_custom",
      "name": "自定义模板",
      "description": "你的自定义搜索提示词",
      "template": "你的提示词... {query} ...",
      "mode": "expert"
    }
  ]
}
```

**自定义模板：** 在 `prompt_templates` 数组中添加新对象，`{query}` 会被替换为搜索关键词。

## 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `GROK_SSO_TOKENS` | SSO Token 列表（逗号分隔） | **必填** |
| `API_KEY` | API 鉴权密钥 | 空（不鉴权） |
| `CF_CLEARANCE` | Cloudflare Cookie | 空 |
| `PROXY_URL` | HTTP/SOCKS5 代理 | 空 |
| `PROXY_POOL` | 代理池（逗号分隔） | 空 |
| `HTTP_BACKEND` | HTTP 后端 (httpx/curl_cffi/auto) | httpx |
| `FINGERPRINT_ROTATION` | 浏览器指纹轮换 | true |
| `WARP_ENABLED` | 启用 WARP 代理 | false |
| `WARP_MODE` | WARP 模式 (auto/wireproxy/kernel) | auto |
| `WARP_SOCKS_PORT` | WARP SOCKS5 端口 | 1080 |
| `WARP_ENDPOINT` | 自定义 WARP endpoint | 空 |
| `WARP_CONFIG_B64` | Base64 WireGuard 配置 | 空 |
| `CONFIG_FILE` | 配置文件路径 | config.json |
| `HOST` | 监听地址 | 0.0.0.0 |
| `PORT` | 监听端口 | 8000 |

环境变量会覆盖 config.json 中的同名配置。

> 📖 **完整 API 文档**: 请参阅 [API_DOCS.md](API_DOCS.md) 了解所有端点的详细输入/输出语法、SouWen 集成指南和部署说明。

## 文件结构

```
search_api/
├── __init__.py          # 包声明
├── __main__.py          # 入口（python -m search_api）
├── app.py               # FastAPI 应用（路由、中间件、异常处理、WARP 管理端点）
├── config.py            # 配置管理（config.json + 环境变量 + WARP + 代理池）
├── token_pool.py        # Token 号池（轮询、冷却、禁用）— 内存状态
├── grok_search.py       # 搜索核心（Payload、SSE 解析、结果提取、额度查询）
├── http_client.py       # HTTP 传输（httpx/curl_cffi 双后端 + 指纹 + 自动降级）
├── fingerprint.py       # 浏览器指纹管理（TLS + UA + Sec-CH-UA 轮换）
├── warp.py              # WARP 代理管理（wireproxy + kernel 双模式）
├── schemas.py           # Pydantic 请求/响应模型
├── config.json          # 持久配置（自动生成）
├── requirements.txt     # 依赖（curl_cffi 可选）
├── .env.example         # 环境变量模板
├── API_DOCS.md          # 完整 API 文档（输入/输出语法、SouWen 集成指南）
├── .gitignore
├── Dockerfile           # Docker 部署
└── README.md            # 本文件
```

## License

MIT
