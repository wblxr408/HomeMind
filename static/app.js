// HomeMind 前端交互逻辑
// 支持 SSE 流式输出、流水线可视化、候选动作展示

let currentRecommendation = null;
let isProcessing = false;

const STAGE_ORDER = ["input", "bsr", "lsr", "llm", "action", "rag"];

function $(id) {
    return document.getElementById(id);
}

// 初始化
document.addEventListener("DOMContentLoaded", () => {
    initContext();
    initChatForm();
});

// 获取当前上下文
async function initContext() {
    try {
        const resp = await fetch("/api/context");
        const data = await resp.json();
        updateContextUI(data);
        const kbResp = await fetch("/api/kb/count");
        const kbData = await kbResp.json();
        $("ctx-kb").textContent = kbData.count + " 条";
    } catch (e) {
        console.warn("上下文加载失败:", e);
    }
}

// 更新上下文 UI
function updateContextUI(ctx) {
    if (!ctx) return;
    $("ctx-hour").textContent = String(ctx.hour).padStart(2, "0") + ":00";
    $("ctx-temp").textContent = ctx.temperature + "°C";
    $("ctx-humidity").textContent = ctx.humidity + "%";
    $("ctx-members").textContent = ctx.members_home + "人";

    const sceneNames = {0: "睡眠模式", 1: "待客模式", 2: "离家模式", 3: "观影模式", 4: "起床模式"};
    $("ctx-scene").textContent = ctx.last_scene >= 0 ? (sceneNames[ctx.last_scene] || "-") : "-";

    if (ctx.devices) {
        const deviceList = $("device-list");
        deviceList.innerHTML = "";
        for (const [name, state] of Object.entries(ctx.devices)) {
            const div = document.createElement("div");
            div.className = "device-item";
            const isOn = state.status === "开" || state === "开";
            div.innerHTML = `<span>${name}</span><span class="status ${isOn ? "on" : "off"}">${state.status || state}</span>`;
            deviceList.appendChild(div);
        }
    }
}

// 聊天表单
function initChatForm() {
    const form = $("chat-form");
    const input = $("chat-input");

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        const query = input.value.trim();
        if (!query || isProcessing) return;

        isProcessing = true;
        $("send-btn").disabled = true;
        input.value = "";

        resetPipeline();
        addUserMessage(query);

        try {
            const response = await fetch("/api/chat", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({query}),
            });

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const {done, value} = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, {stream: true});
                const lines = buffer.split("\n");
                buffer = lines.pop() || "";

                for (const line of lines) {
                    if (line.startsWith("data: ")) {
                        const raw = line.slice(6).trim();
                        if (raw === "[DONE]") continue;
                        try {
                            const data = JSON.parse(raw);
                            handleSSEMessage(data);
                        } catch (e) {
                            console.warn("SSE 解析失败:", raw);
                        }
                    }
                }
            }
        } catch (e) {
            addAssistantMessage("抱歉，处理过程中出现错误。", null);
        } finally {
            isProcessing = false;
            $("send-btn").disabled = false;
            input.focus();
            initContext();
        }
    });
}

// SSE 消息处理
function handleSSEMessage(data) {
    const {stage, status, message, data: payload} = data;

    if (STAGE_ORDER.includes(stage)) {
        updatePipelineStage(stage, status);
    }

    if (stage === "input" && status === "processing") {
        addPipelineDetail(stage, message);
    }
    if (stage === "bsr") {
        addPipelineDetail(stage, message);
        if (payload && payload.candidates) {
            updateCandidates(payload.candidates);
        }
    }
    if (stage === "lsr") {
        addPipelineDetail(stage, message);
        if (payload && payload.ranked) {
            updateCandidates(payload.ranked.map(r => r.action));
        }
    }
    if (stage === "llm") {
        addPipelineDetail(stage, message);
        if (payload && payload.reasoning) {
            addReasoning(payload.reasoning);
        }
    }
    if (stage === "action") {
        addPipelineDetail(stage, message);
        if (payload && payload.result) {
            addAssistantMessage(payload.result, payload);
        }
        if (payload && payload.needs_clarify) {
            addAssistantMessage(payload.result, {needs_clarify: true});
        }
    }
    if (stage === "rag") {
        addPipelineDetail(stage, message);
    }
    if (stage === "done") {
        addPipelineDetail("done", message);
        updatePipelineStage("done", "done");
    }
}

// 流水线阶段更新
function updatePipelineStage(stageId, status) {
    const stageMap = {
        "input": "stage-input", "bsr": "stage-bsr", "lsr": "stage-lsr",
        "llm": "stage-llm", "action": "stage-action", "rag": "stage-rag", "done": "stage-rag"
    };

    const elId = stageMap[stageId];
    if (!elId) return;

    const el = $(elId);
    el.className = "stage " + status;

    const lineMap = {
        "stage-input": "line-1", "stage-bsr": "line-2", "stage-lsr": "line-3",
        "stage-llm": "line-4", "stage-action": "line-5"
    };

    if (lineMap[elId] && status === "done") {
        const lineEl = $(lineMap[elId]);
        if (lineEl) lineEl.style.background = "var(--accent-green)";
    }
}

function resetPipeline() {
    STAGE_ORDER.forEach((s, i) => {
        const map = {input: "stage-input", bsr: "stage-bsr", lsr: "stage-lsr",
                      llm: "stage-llm", action: "stage-action", rag: "stage-rag"};
        const el = $(map[s]);
        if (el) el.className = "stage";
    });
    for (let i = 1; i <= 5; i++) {
        const line = $("line-" + i);
        if (line) line.style.background = "";
    }
    $("pipeline-detail").innerHTML = "";
    $("candidates-list").innerHTML = '<div class="empty-hint">等待输入...</div>';
    $("reasoning-list").innerHTML = '<div class="empty-hint">等待推理...</div>';
}

function addPipelineDetail(stage, message) {
    const detail = $("pipeline-detail");
    const iconMap = {
        input: "📥", bsr: "🔍", lsr: "⚖️",
        llm: "🤖", action: "⚡", rag: "🧠", done: "✅"
    };
    const div = document.createElement("div");
    div.className = "step";
    div.innerHTML = `<span class="step-icon ${stage}">${iconMap[stage] || "•"}</span><span>${message}</span>`;
    detail.appendChild(div);
    detail.scrollTop = detail.scrollHeight;
}

// 聊天消息
function addUserMessage(query) {
    const container = $("chat-messages");
    const div = document.createElement("div");
    div.className = "message user";
    const time = new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"});
    div.innerHTML = `<div class="bubble">${escapeHtml(query)}</div><div class="time">${time}</div>`;
    container.appendChild(div);
    scrollChat();
}

function addAssistantMessage(text, payload) {
    const container = $("chat-messages");
    const div = document.createElement("div");
    div.className = "message assistant";

    let extras = "";
    if (payload && payload.confidence !== undefined) {
        const pct = Math.round(payload.confidence * 100);
        const color = pct >= 75 ? "var(--accent-green)" : pct >= 50 ? "var(--accent-orange)" : "var(--accent-red)";
        extras += `
            <div class="confidence-bar">
                <span>置信度</span>
                <div class="bar"><div class="fill" style="width:${pct}%;background:${color}"></div></div>
                <span style="color:${color}">${pct}%</span>
            </div>`;
    }
    if (payload && payload.reasoning) {
        extras += `<div class="reasoning-badge">🧠 ${escapeHtml(payload.reasoning)}</div>`;
    }

    const time = new Date().toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit"});
    div.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>${extras}<div class="time">${time}</div>`;
    container.appendChild(div);
    scrollChat();
}

function scrollChat() {
    const container = $("chat-messages");
    container.scrollTop = container.scrollHeight;
}

function clearChat() {
    const container = $("chat-messages");
    container.innerHTML = `
        <div class="welcome-message">
            <p>👋 欢迎使用 HomeMind 智能家居控制台</p>
            <p>你可以输入模糊指令，如「有点闷」「帮我切换睡眠模式」「像昨晚那样」</p>
        </div>`;
    resetPipeline();
}

// 候选动作
function updateCandidates(actions) {
    const list = $("candidates-list");
    if (!actions || actions.length === 0) {
        list.innerHTML = '<div class="empty-hint">无候选动作</div>';
        return;
    }

    list.innerHTML = "";
    actions.forEach((a, i) => {
        const name = typeof a === "string" ? a : a.action || a;
        const score = typeof a === "object" ? (a.score || a.final_score || 0) : null;
        const div = document.createElement("div");
        div.className = "candidate-item" + (i === 0 ? " top-1" : "");
        let scoreHtml = "";
        if (score !== null) {
            const level = score >= 0.7 ? "high" : score >= 0.4 ? "medium" : "low";
            scoreHtml = `<span class="score ${level}">${(score * 100).toFixed(0)}%</span>`;
        }
        div.innerHTML = `<span class="action-name">${escapeHtml(name)}</span>${scoreHtml}`;
        list.appendChild(div);
    });
}

// 推理过程
function addReasoning(text) {
    const list = $("reasoning-list");
    const div = document.createElement("div");
    div.className = "reasoning-item";
    div.textContent = text;
    list.appendChild(div);
    list.scrollTop = list.scrollHeight;
}

// DQN 推荐
async function dqnRecommend() {
    try {
        const resp = await fetch("/api/dqn/recommend");
        const data = await resp.json();
        if (data.recommendation) {
            $("dqn-result").textContent = data.recommendation;
            currentRecommendation = data.recommendation;
        } else {
            $("dqn-result").textContent = "当前不建议切换场景";
            currentRecommendation = null;
        }
    } catch (e) {
        $("dqn-result").textContent = "推荐获取失败";
    }
}

async function dqnRespond(response) {
    if (!currentRecommendation) return;
    try {
        const resp = await fetch("/api/dqn/respond", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({response}),
        });
        const data = await resp.json();
        $("dqn-result").textContent = data.result || "已记录";
        currentRecommendation = null;
        initContext();
    } catch (e) {
        $("dqn-result").textContent = "响应失败";
    }
}

// 工具
function escapeHtml(str) {
    if (!str) return "";
    return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
