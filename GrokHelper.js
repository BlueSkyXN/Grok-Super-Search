// ==UserScript==
// @name         Grok Helper
// @namespace    https://github.com/BlueSkyXN/Grok-Super-Search
// @version      2.2.0
// @author       BlueSkyXN
// @description  Monitor Grok rate limits + Export webSearchResults (JSON/CSV/API)
// @match        https://grok.com/*
// @grant        GM_addStyle
// @supportURL   https://github.com/BlueSkyXN/Grok-Super-Search
// @homepageURL  https://github.com/BlueSkyXN/Grok-Super-Search
// @downloadURL  https://raw.githubusercontent.com/BlueSkyXN/Grok-Super-Search/refs/heads/main/GrokHelper.js
// @updateURL    https://raw.githubusercontent.com/BlueSkyXN/Grok-Super-Search/refs/heads/main/GrokHelper.js
// ==/UserScript==

(function() {
    'use strict';

    // ========== 模型与查询配置 ==========

    // 所有需要查询的模型，key 用作内部标识
    // grok-3 有 4 种 requestKind，grok-4 系列只用 DEFAULT
    // 实测限额池只有 3 个：Fast / Expert / Heavy
    const QUERY_LIST = [
        { key: 'fast',    label: 'Fast',    modelName: 'fast',    requestKind: 'DEFAULT' },
        { key: 'expert',  label: 'Expert',  modelName: 'expert',  requestKind: 'DEFAULT' },
        { key: 'heavy',   label: 'Heavy',   modelName: 'heavy',   requestKind: 'DEFAULT' },
    ];
    const API_ENDPOINT_STORAGE_KEY = 'grok-search-api-endpoint';

    // ========== 样式 ==========

    GM_addStyle(`
        .grok-monitor {
            position: fixed;
            right: 16px;
            top: 72px;
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            font-size: 14px;
            z-index: 100;
            display: flex;
            flex-direction: column;
            align-items: flex-start;
            padding: 8px 12px;
            gap: 8px;
            border: 1px solid #ccc;
            border-radius: 8px;
            background-color: #fff;
            color: #1a1a1a;
            box-shadow: 0 2px 4px rgba(0,0,0,0.08);
            transition: all 0.2s ease;
            opacity: 0.9;
            max-height: 40px;
            overflow: hidden;
        }
        .grok-monitor:hover {
            opacity: 1;
            max-height: 600px;
        }
        .grok-monitor-summary {
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
            font-weight: 500;
            font-size: 14px;
            cursor: pointer;
        }
        .grok-monitor-indicator {
            width: 12px;
            height: 12px;
            border-radius: 50%;
            flex-shrink: 0;
            transition: background-color 0.2s ease;
        }
        .grok-monitor-details {
            display: none;
            flex-direction: column;
            gap: 4px;
            font-size: 13px;
        }
        .grok-monitor:hover .grok-monitor-details {
            display: flex;
        }
        .grok-monitor-row {
            display: flex;
            align-items: center;
            gap: 6px;
            white-space: nowrap;
        }
        .grok-monitor-row-label {
            font-weight: 600;
            min-width: 60px;
        }
        .grok-monitor-separator {
            border: none;
            border-top: 1px dashed #ddd;
            margin: 2px 0;
            width: 100%;
        }
        .grok-monitor-btn-group {
            display: flex;
            gap: 0;
            margin-top: 4px;
            width: 100%;
        }
        .grok-monitor-btn-label {
            padding: 5px 10px;
            background: #e8e8e8;
            color: #555;
            font-size: 12px;
            font-weight: 600;
            border: 1px solid #ccc;
            border-right: none;
            border-radius: 6px 0 0 6px;
            white-space: nowrap;
            line-height: 1;
        }
        .grok-monitor-btn {
            padding: 5px 12px;
            border: 1px solid #ccc;
            border-radius: 0;
            background: linear-gradient(to bottom, #fafafa, #f0f0f0);
            color: #333;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.15s;
            line-height: 1;
        }
        .grok-monitor-btn:last-child { border-radius: 0 6px 6px 0; }
        .grok-monitor-btn:hover { background: linear-gradient(to bottom, #fff, #e8e8e8); }
        .grok-monitor-btn:active { background: #ddd; }
        .grok-monitor-btn.loading {
            opacity: 0.6;
            pointer-events: none;
        }
        .grok-monitor.updating .grok-monitor-indicator {
            animation: grok-pulse 1s ease-in-out infinite;
        }
        @keyframes grok-pulse {
            0%, 100% { transform: scale(1); opacity: 1; }
            50% { transform: scale(1.2); opacity: 0.7; }
        }
        @media (prefers-color-scheme: dark) {
            .grok-monitor {
                background-color: #2b2b2b;
                color: #fff;
                border-color: #666;
            }
            .grok-monitor-separator { border-top-color: #555; }
            .grok-monitor-btn-label { background: #3a3a3a; color: #aaa; border-color: #666; }
            .grok-monitor-btn { background: linear-gradient(to bottom, #444, #3a3a3a); color: #eee; border-color: #666; }
            .grok-monitor-btn:hover { background: linear-gradient(to bottom, #555, #444); }
        }
    `);

    // ========== 工具函数 ==========

    function formatWindow(seconds) {
        if (typeof seconds !== 'number' || seconds <= 0) return '?';
        const h = Math.floor(seconds / 3600);
        const m = Math.floor((seconds % 3600) / 60);
        if (h > 0 && m > 0) return `${h}H${m}M`;
        if (h > 0) return `${h}H`;
        return `${m}M`;
    }

    function formatWait(seconds) {
        if (typeof seconds !== 'number' || seconds <= 0) return '';
        const m = Math.floor(seconds / 60);
        const s = seconds % 60;
        return m > 0 ? ` 等${m}m${s > 0 ? s + 's' : ''}` : ` 等${s}s`;
    }

    // ========== API 请求 ==========

    async function fetchRateLimit(modelName, requestKind) {
        try {
            const resp = await fetch('/rest/rate-limits', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ requestKind, modelName }),
                credentials: 'include'
            });
            if (resp.ok) return await resp.json();
            return { __error: true, status: resp.status };
        } catch (e) {
            return { __error: true, status: 0, message: String(e) };
        }
    }

    async function fetchAll() {
        const tasks = QUERY_LIST.map(q =>
            fetchRateLimit(q.modelName, q.requestKind).then(res => ({ key: q.key, res }))
        );
        const results = {};
        (await Promise.all(tasks)).forEach(({ key, res }) => { results[key] = res; });
        return results;
    }

    // ========== 格式化单条结果为文本 ==========

    function formatResult(data) {
        if (!data || data.__error) {
            const code = data?.status || '-';
            return `失败(${code})`;
        }

        const win = formatWindow(data.windowSizeSeconds);
        const hasHigh = data.highEffortRateLimits != null;
        const hasLow  = data.lowEffortRateLimits != null;

        // Auto 双路模式
        if (hasHigh && hasLow) {
            const hRem = data.highEffortRateLimits.remainingQueries ?? '?';
            const lRem = data.lowEffortRateLimits.remainingQueries ?? '?';
            const hWait = formatWait(data.highEffortRateLimits.waitTimeSeconds);
            const lWait = formatWait(data.lowEffortRateLimits.waitTimeSeconds);
            return `高${hRem}${hWait} 低${lRem}${lWait} (${win})`;
        }

        // 单路模式
        const rem = data.remainingQueries ?? '?';
        const tot = data.totalQueries ?? '?';
        const wait = formatWait(data.waitTimeSeconds);
        let text = `${rem}/${tot}${wait} (${win})`;
        // 部分模型额外返回 token 桶信息
        if (typeof data.remainingTokens === 'number') {
            text += ` T:${data.remainingTokens}/${data.totalTokens ?? '?'}`;
        }
        return text;
    }

    // 从结果中提取剩余可用次数（用于总数汇总）
    function extractRemaining(data) {
        if (!data || data.__error) return 0;
        if (data.highEffortRateLimits != null || data.lowEffortRateLimits != null) {
            const h = data.highEffortRateLimits?.remainingQueries ?? 0;
            const l = data.lowEffortRateLimits?.remainingQueries ?? 0;
            return Math.max(h, 0) + Math.max(l, 0);
        }
        return Math.max(data.remainingQueries ?? 0, 0);
    }

    // ========== UI ==========

    function createMonitor() {
        const monitor = document.createElement('div');
        monitor.className = 'grok-monitor';

        // 摘要行
        const summary = document.createElement('div');
        summary.className = 'grok-monitor-summary';
        const sumText = document.createElement('span');
        sumText.id = 'grok-mon-sum';
        sumText.textContent = '加载中...';
        const indicator = document.createElement('div');
        indicator.className = 'grok-monitor-indicator';
        indicator.id = 'grok-mon-ind';
        summary.appendChild(sumText);
        summary.appendChild(indicator);

        // 详情区
        const details = document.createElement('div');
        details.className = 'grok-monitor-details';

        QUERY_LIST.forEach((q, i) => {
            const row = document.createElement('div');
            row.className = 'grok-monitor-row';
            const labelSpan = document.createElement('span');
            labelSpan.className = 'grok-monitor-row-label';
            labelSpan.textContent = q.label;
            const infoSpan = document.createElement('span');
            infoSpan.id = `grok-mon-${q.key}`;
            infoSpan.textContent = '...';
            row.appendChild(labelSpan);
            row.appendChild(infoSpan);
            details.appendChild(row);
        });

        monitor.appendChild(summary);
        monitor.appendChild(details);

        // 导出按钮组（hover 时显示在详情下方）
        const btnGroup = document.createElement('div');
        btnGroup.className = 'grok-monitor-btn-group';

        const btnLabel = document.createElement('span');
        btnLabel.className = 'grok-monitor-btn-label';
        btnLabel.textContent = '导出';

        const btnJSON = document.createElement('button');
        btnJSON.className = 'grok-monitor-btn grok-export-btn';
        btnJSON.textContent = 'JSON';
        btnJSON.title = '导出 webSearchResults 为 JSON';
        btnJSON.addEventListener('click', exportAsJSON);

        const btnCSV = document.createElement('button');
        btnCSV.className = 'grok-monitor-btn grok-export-btn';
        btnCSV.textContent = 'CSV';
        btnCSV.title = '导出 webSearchResults 为 CSV';
        btnCSV.addEventListener('click', exportAsCSV);

        const btnAPI = document.createElement('button');
        btnAPI.className = 'grok-monitor-btn grok-export-btn';
        btnAPI.textContent = 'API';
        btnAPI.title = '导出并 POST 到搜索 API';
        btnAPI.addEventListener('click', exportAsAPI);

        btnGroup.appendChild(btnLabel);
        btnGroup.appendChild(btnJSON);
        btnGroup.appendChild(btnCSV);
        btnGroup.appendChild(btnAPI);
        details.appendChild(btnGroup);

        document.body.appendChild(monitor);
    }

    function updateUI(results) {
        const sumText = document.getElementById('grok-mon-sum');
        const indicator = document.getElementById('grok-mon-ind');
        const monitor = document.querySelector('.grok-monitor');

        monitor.classList.add('updating');

        let totalRemaining = 0;

        QUERY_LIST.forEach(q => {
            const data = results[q.key];
            const el = document.getElementById(`grok-mon-${q.key}`);
            if (el) el.textContent = formatResult(data);
            totalRemaining += extractRemaining(data);
        });

        sumText.textContent = `剩余总数: ${totalRemaining}`;

        if (totalRemaining === 0) {
            indicator.style.backgroundColor = '#EF4444';
        } else if (totalRemaining < 15) {
            indicator.style.backgroundColor = '#F59E0B';
        } else {
            indicator.style.backgroundColor = '#10B981';
        }

        setTimeout(() => monitor.classList.remove('updating'), 1000);
    }

    // ========== 导出 webSearchResults ==========

    function getConversationId() {
        const isValidConversationId = id => typeof id === 'string' && /^[a-f0-9]+(?:-[a-f0-9]+)*$/i.test(id);
        // 形式 1：独立对话 /c/{conversationId}
        const m1 = window.location.pathname.match(/^\/c\/([^/]+)/i);
        if (m1 && isValidConversationId(m1[1])) return m1[1];

        // 形式 2：Project 内对话 /project/{projectId}?chat={conversationId}&rid=...
        if (/^\/project\//i.test(window.location.pathname)) {
            const chat = new URLSearchParams(window.location.search).get('chat');
            if (isValidConversationId(chat)) return chat;
        }
        return null;
    }

    async function fetchResponseNodes(convId) {
        const resp = await fetch(`/rest/app-chat/conversations/${convId}/response-node?includeThreads=true`, {
            credentials: 'include'
        });
        if (!resp.ok) throw new Error(`response-node failed: ${resp.status}`);
        return await resp.json();
    }

    // 递归收集所有 responseId
    function collectResponseIds(node) {
        const ids = [];
        if (node.responseId) ids.push(node.responseId);
        if (Array.isArray(node.children)) {
            for (const child of node.children) {
                ids.push(...collectResponseIds(child));
            }
        }
        // response-node API 返回 { responseNodes: [...] }
        if (Array.isArray(node.responseNodes)) {
            for (const child of node.responseNodes) {
                ids.push(...collectResponseIds(child));
            }
        }
        return ids;
    }

    async function fetchLoadResponses(convId, responseIds) {
        const resp = await fetch(`/rest/app-chat/conversations/${convId}/load-responses`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ responseIds }),
            credentials: 'include'
        });
        if (!resp.ok) throw new Error(`load-responses failed: ${resp.status}`);
        return await resp.json();
    }

    function normalizeSearchResult(sr) {
        if (!sr || typeof sr !== 'object') return null;
        const title = sr.title || sr.name || sr.headline || '';
        const url = sr.url || sr.link || sr.href || '';
        const preview = sr.preview || sr.snippet || sr.description || '';
        const source = sr.site || sr.domain || sr.source || '';
        const publishedTime = sr.publishedTime || sr.publishedDate || sr.time || '';
        return {
            title: String(title || ''),
            url: String(url || ''),
            preview: String(preview || ''),
            source: String(source || ''),
            publishedTime: String(publishedTime || ''),
            raw: sr
        };
    }

    function buildSearchApiPayload(result) {
        const { convId, responseIds, allSearchResults } = result;
        const items = [];
        for (const item of allSearchResults) {
            item.searchResults.forEach((sr, idx) => {
                items.push({
                    conversationId: convId,
                    turn: item.turn,
                    responseId: item.responseId,
                    rank: idx + 1,
                    ...sr
                });
            });
        }
        return {
            conversationId: convId,
            exportTime: new Date().toISOString(),
            totalResponses: responseIds.length,
            searchTurnCount: allSearchResults.length,
            searchResultCount: items.length,
            turns: allSearchResults.map(item => ({
                turn: item.turn,
                responseId: item.responseId,
                searchResults: item.searchResults
            })),
            items
        };
    }

    async function postSearchPayload(endpoint, payload) {
        const resp = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            credentials: 'omit'
        });
        const text = await resp.text();
        if (!resp.ok) {
            const snippet = text.length > 300 ? `${text.slice(0, 300)}...` : text;
            throw new Error(`API POST failed: ${resp.status} ${snippet}`);
        }
        return { status: resp.status, body: text };
    }

    function downloadFile(content, filename, mimeType) {
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    // 收集搜索结果的通用逻辑
    async function gatherSearchResults(options = {}) {
        const { conversationId = null, silent = false } = options;
        const convId = conversationId || getConversationId();
        if (!convId) {
            if (!silent) alert('请先打开一个对话（URL 需为 /c/{id} 或 /project/{id}?chat={id}）');
            return null;
        }

        // 1. 获取对话树节点
        const tree = await fetchResponseNodes(convId);

        // 2. 收集所有 responseId
        let responseIds = [];
        if (Array.isArray(tree)) {
            tree.forEach(n => responseIds.push(...collectResponseIds(n)));
        } else {
            responseIds = collectResponseIds(tree);
        }

        if (responseIds.length === 0) {
            if (!silent) alert('未找到任何 response 节点');
            return null;
        }

        // 3. 加载完整 response 数据
        const responses = await fetchLoadResponses(convId, responseIds);

        // turn 按 responseIds 收集顺序编号（从 1 起），load-responses 返回顺序不一定和收集顺序一致
        const turnMap = new Map();
        responseIds.forEach((id, idx) => turnMap.set(id, idx + 1));

        // 4. 提取 webSearchResults（两路择一，避免同一 response 被 push 两次）
        const allSearchResults = [];
        const responseArray = Array.isArray(responses) ? responses : (responses.responses || [responses]);

        for (const r of responseArray) {
            const results = (r.webSearchResults?.length ? r.webSearchResults
                : r.message?.webSearchResults?.length ? r.message.webSearchResults
                : null);
            if (!results) continue;
            const responseId = r.responseId || r.id || null;
            const normalized = results
                .map(normalizeSearchResult)
                .filter(sr => sr && (sr.url || sr.title || sr.preview));
            allSearchResults.push({
                turn: turnMap.get(responseId) ?? null,
                responseId,
                webSearchResults: results,
                searchResults: normalized
            });
        }

        // 按 turn 升序，让多轮对话的结果有稳定、可读的顺序
        allSearchResults.sort((a, b) => (a.turn ?? Infinity) - (b.turn ?? Infinity));

        if (allSearchResults.length === 0) {
            if (!silent) alert('此对话没有 webSearchResults 数据（可能不是搜索模式的对话）');
            return null;
        }

        return { convId, responseIds, allSearchResults };
    }

    function setExportLoading(loading) {
        const btns = document.querySelectorAll('.grok-export-btn');
        btns.forEach(b => {
            if (loading) { b.classList.add('loading'); }
            else { b.classList.remove('loading'); }
        });
    }

    async function exportAsJSON() {
        setExportLoading(true);
        try {
            const result = await gatherSearchResults();
            if (!result) return;
            const payload = buildSearchApiPayload(result);
            const { convId, responseIds, allSearchResults } = result;
            const filename = `grok-search-${convId.slice(0, 8)}-${Date.now()}.json`;
            downloadFile(JSON.stringify({
                ...payload,
                // 兼容旧版导出字段
                data: allSearchResults,
                legacySearchTurnCount: allSearchResults.length
            }, null, 2), filename, 'application/json');
        } catch (e) {
            console.error('Export JSON failed:', e);
            alert('导出失败: ' + e.message);
        } finally {
            setExportLoading(false);
        }
    }

    function escapeCsv(val) {
        const s = String(val ?? '');
        if (s.includes(',') || s.includes('"') || s.includes('\n')) {
            return '"' + s.replace(/"/g, '""') + '"';
        }
        return s;
    }

    async function exportAsCSV() {
        setExportLoading(true);
        try {
            const result = await gatherSearchResults();
            if (!result) return;
            const { convId, allSearchResults } = result;

            const rows = [['turn', 'responseId', 'title', 'url', 'preview'].join(',')];
            for (const item of allSearchResults) {
                for (const sr of item.searchResults) {
                    rows.push([
                        escapeCsv(item.turn ?? ''),
                        escapeCsv(item.responseId ?? ''),
                        escapeCsv(sr.title),
                        escapeCsv(sr.url),
                        escapeCsv(sr.preview)
                    ].join(','));
                }
            }

            const filename = `grok-search-${convId.slice(0, 8)}-${Date.now()}.csv`;
            downloadFile('\uFEFF' + rows.join('\n'), filename, 'text/csv;charset=utf-8');
        } catch (e) {
            console.error('Export CSV failed:', e);
            alert('导出失败: ' + e.message);
        } finally {
            setExportLoading(false);
        }
    }

    async function exportAsAPI() {
        setExportLoading(true);
        try {
            const result = await gatherSearchResults();
            if (!result) return;
            const lastEndpoint = localStorage.getItem(API_ENDPOINT_STORAGE_KEY) || '';
            const endpoint = window.prompt('输入搜索 API Endpoint（POST JSON）', lastEndpoint || '');
            if (!endpoint) return;
            const target = endpoint.trim();
            if (!target) return;
            localStorage.setItem(API_ENDPOINT_STORAGE_KEY, target);
            const payload = buildSearchApiPayload(result);
            const resp = await postSearchPayload(target, payload);
            alert(`已推送到 API：${resp.status}`);
        } catch (e) {
            console.error('Export API failed:', e);
            alert('导出到 API 失败: ' + e.message);
        } finally {
            setExportLoading(false);
        }
    }

    function exposeSearchAPI() {
        window.GrokSearchAPI = {
            version: '1.0.0',
            getCurrentConversation: async () => await gatherSearchResults({ silent: true }),
            getConversationById: async (conversationId) => {
                if (!conversationId || !/^[a-f0-9]+(?:-[a-f0-9]+)*$/i.test(conversationId)) {
                    throw new Error('invalid conversationId');
                }
                return await gatherSearchResults({ conversationId, silent: true });
            },
            buildPayload: buildSearchApiPayload,
            postPayload: postSearchPayload
        };
    }

    // ========== 主循环 ==========

    async function tick() {
        const results = await fetchAll();
        updateUI(results);
    }

    function init() {
        createMonitor();
        exposeSearchAPI();
        tick();
        setInterval(tick, 30000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
