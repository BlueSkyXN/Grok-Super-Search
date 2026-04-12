// ==UserScript==
// @name         Grok Helper
// @namespace    https://github.com/BlueSkyXN/Grok-Super-Search
// @version      2.0.0
// @author       BlueSkyXN
// @description  Monitor Grok rate limits (Fast/Expert/Heavy) + Export webSearchResults as JSON
// @match        https://grok.com/*
// @grant        GM_addStyle
// @supportURL   https://github.com/BlueSkyXN/Grok-Super-Search
// @homepageURL  https://github.com/BlueSkyXN/Grok-Super-Search
// @downloadURL https://github.com/BlueSkyXN/Grok-Super-Search/blob/main/GrokHelper.js
// @updateURL https://github.com/BlueSkyXN/Grok-Super-Search/blob/main/GrokHelper.js
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
        .grok-monitor-btn {
            padding: 4px 10px;
            border: 1px solid #ccc;
            border-radius: 4px;
            background: #f5f5f5;
            color: #333;
            font-size: 12px;
            cursor: pointer;
            white-space: nowrap;
            transition: background 0.15s;
        }
        .grok-monitor-btn:hover { background: #e0e0e0; }
        .grok-monitor-btn:active { background: #d0d0d0; }
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
            .grok-monitor-btn { background: #444; color: #eee; border-color: #666; }
            .grok-monitor-btn:hover { background: #555; }
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

        // 导出按钮（hover 时显示在详情下方）
        const exportBtn = document.createElement('button');
        exportBtn.id = 'grok-export-btn';
        exportBtn.className = 'grok-monitor-btn';
        exportBtn.textContent = '导出搜索';
        exportBtn.title = '导出当前对话的 webSearchResults 为 JSON';
        exportBtn.addEventListener('click', exportWebSearchResults);
        details.appendChild(exportBtn);

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
        const match = window.location.pathname.match(/^\/c\/([a-f0-9-]+)/);
        return match ? match[1] : null;
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

    function downloadJSON(data, filename) {
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    }

    async function exportWebSearchResults() {
        const convId = getConversationId();
        if (!convId) {
            alert('请先打开一个对话（URL 需包含 /c/{conversationId}）');
            return;
        }

        const btn = document.getElementById('grok-export-btn');
        if (btn) { btn.classList.add('loading'); btn.textContent = '导出中...'; }

        try {
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
                alert('未找到任何 response 节点');
                return;
            }

            // 3. 加载完整 response 数据
            const responses = await fetchLoadResponses(convId, responseIds);

            // 4. 提取 webSearchResults
            const allSearchResults = [];
            const responseArray = Array.isArray(responses) ? responses : (responses.responses || [responses]);

            for (const r of responseArray) {
                if (r.webSearchResults && r.webSearchResults.length > 0) {
                    allSearchResults.push({
                        responseId: r.responseId || r.id || null,
                        webSearchResults: r.webSearchResults
                    });
                }
                // 有些嵌套在 message 里
                if (r.message?.webSearchResults?.length > 0) {
                    allSearchResults.push({
                        responseId: r.responseId || r.id || null,
                        webSearchResults: r.message.webSearchResults
                    });
                }
            }

            if (allSearchResults.length === 0) {
                alert('此对话没有 webSearchResults 数据（可能不是搜索模式的对话）');
                return;
            }

            // 5. 下载 JSON
            const filename = `grok-search-${convId.slice(0, 8)}-${Date.now()}.json`;
            downloadJSON({
                conversationId: convId,
                exportTime: new Date().toISOString(),
                totalResponses: responseIds.length,
                searchResultCount: allSearchResults.length,
                data: allSearchResults
            }, filename);

        } catch (e) {
            console.error('Export failed:', e);
            alert('导出失败: ' + e.message);
        } finally {
            if (btn) { btn.classList.remove('loading'); btn.textContent = '导出搜索'; }
        }
    }

    // ========== 主循环 ==========

    async function tick() {
        const results = await fetchAll();
        updateUI(results);
    }

    function init() {
        createMonitor();
        tick();
        setInterval(tick, 30000);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();