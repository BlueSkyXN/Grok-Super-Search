# Grok Super Search

将 Grok 的 DeepSearch/DeeperSearch 搜索能力发挥到极限的工具集，包含三个独立组件：

| 组件 | 类型 | 用途 |
|------|------|------|
| [GrokHelper.js](#grokhelperjs) | 油猴脚本 | 额度监控 + 搜索结果一键导出 |
| [search_api/](#search_api) | REST API 服务 | 将 Grok 搜索封装为可调用的 HTTP 接口 |
| [Prompt.md](#promptmd) | 提示词指南 | 批量搜索提示词模板与最佳实践 |

---

## GrokHelper.js

浏览器油猴脚本，安装后在 grok.com 页面右上角显示浮窗：

- **额度监控**：实时显示 Fast / Expert / Heavy 三个模型池的剩余调用次数
- **搜索结果导出**：对话结束后一键导出当前会话的 `webSearchResults` 为 JSON 或 CSV
- **Project 支持**：兼容 Grok Project 内的多轮对话导出

### 安装

1. 安装 [Tampermonkey](https://www.tampermonkey.net/)（Chrome/Edge/Firefox）
2. 点击安装链接：[安装 GrokHelper.js](https://raw.githubusercontent.com/BlueSkyXN/Grok-Super-Search/refs/heads/main/GrokHelper.js)
3. 刷新 [grok.com](https://grok.com)，右上角出现浮窗即安装成功

### 使用

- **查看额度**：悬停浮窗展开，显示三个模型池的剩余次数和等待时间
- **导出数据**：对话完成后，展开浮窗 → 点击 **JSON** 或 **CSV** 按钮

---

## search_api

将 Grok Web 搜索封装为独立 REST API 服务，适合程序化批量调用，无需浏览器。

### 核心特性

- **只取搜索结果**：解析 SSE 流提取 `webSearchResults`，忽略 AI 文字回答
- **多 Token 号池**：轮询 + 冷却 + 自动禁用 + 额度查询
- **提示词模板**：内置 6 种深度搜索模板，支持 `config.json` 自定义
- **无数据库**：纯内存状态，适合 HF Space / Docker 单实例部署
- **双 HTTP 后端**：默认 `httpx`，可切换 `curl_cffi`（TLS 指纹模拟），自动降级
- **Cloudflare WARP 代理**：wireproxy（用户态）+ kernel（WireGuard）双模式
- **浏览器指纹轮换**：每次请求随机 Chrome/Edge/Safari 指纹
- **代理池**：多代理随机选取（HTTP/SOCKS5/SOCKS4）
- **SouWen 兼容**：可直接作为 [SouWen](https://github.com/BlueSkyXN/SouWen) 搜索平台数据源

### 快速开始

```bash
cd search_api
pip install -r requirements.txt
# 可选：pip install curl_cffi  # TLS 指纹模拟
cp .env.example .env
# 编辑 .env，填入 Grok SSO Token
python -m search_api
```

### 获取 SSO Token

1. 浏览器登录 [grok.com](https://grok.com)
2. DevTools → Application → Cookies → 复制 `sso` 的值
3. 填入 `.env` 的 `GROK_SSO_TOKENS`，多个 Token 逗号分隔

### 主要接口

```bash
# 单次搜索
curl -X POST http://localhost:8000/v1/search \
  -H "Content-Type: application/json" \
  -d '{"query": "AI 框架对比 2025", "prompt_id": "deep_research"}'

# 批量搜索（最多 20 个）
curl -X POST http://localhost:8000/v1/search/batch \
  -H "Content-Type: application/json" \
  -d '{"queries": ["Rust vs Go", "WebAssembly 现状"], "concurrency": 3}'

# 查看提示词模板
curl http://localhost:8000/v1/prompts

# 查询额度
curl http://localhost:8000/admin/quota

# 系统信息（HTTP 后端、代理、WARP 状态）
curl http://localhost:8000/admin/system

# WARP 代理管理
curl http://localhost:8000/admin/warp/status
curl -X POST http://localhost:8000/admin/warp/enable -H "Content-Type: application/json" -d '{"mode": "auto"}'
```

### Docker 部署

```bash
cd search_api
docker build -t grok-search-api .
docker run -p 8000:8000 --env-file .env grok-search-api
```

详细文档见 [search_api/README.md](search_api/README.md)。完整 API 文档（输入/输出语法、SouWen 集成指南）见 [search_api/API_DOCS.md](search_api/API_DOCS.md)。

---

## Prompt.md

批量搜索提示词指南，配合 GrokHelper.js 或 search_api 使用，单次对话可获取数千条搜索结果。

内置 6 种提示词策略：

| 策略 | 适用场景 | 搜索次数 |
|------|---------|---------|
| 时间切片 | 追踪某主题的时间线 | 按天/周 |
| 地理切片 | 各省市/各国对比 | 34+ |
| 多角度裂变 | 通用全面调研 | 50 |
| 站点遍历 | 跨平台聚合 | 18 |
| 问题矩阵 | 结构化问答 | 20 |
| 组合爆炸 | 多维度交叉 | 60+ |

详细模板与使用技巧见 [Prompt.md](Prompt.md)。

---

## 典型工作流

```
Prompt.md 选模板
    ↓
① 浏览器手动模式：grok.com 粘贴提示词 → DeeperSearch → GrokHelper.js 导出 JSON/CSV
② 程序化模式：search_api POST /v1/search（prompt_id 指定内置模板）→ 直接拿 JSON
```

---

## License

MIT
