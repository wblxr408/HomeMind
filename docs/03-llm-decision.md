# LLM Decision 决策层 — 面试拷打指南

## 模块定位

LLM Decision 是 HomeMind 五层架构的**第三层**，负责根据 LSR 精排后的候选选择一个结构化命令（action, device, device_action, params, confidence）。

```
用户输入
  ↓
BSR 召回 → LSR 精排
                    ↓
        LLM Decision (最终命令)
                    ↓
           执行层 (校验)
```

支持四种后端：**mock** / **ollama** / **llama_cpp** / **openai**

---

## 核心接口

### `LLMDecider.__init__(backend, model_path, api_base, api_key, cloud_model)`
```python
LLMDecider(backend="mock")                    # 开发/测试用
LLMDecider(backend="ollama", ...)             # 本地 Ollama
LLMDecider(backend="llama_cpp", ...)         # 本地 llama.cpp
LLMDecider(backend="openai", ...)            # 云端 OpenAI 兼容 API
```

### `plan_intent(query, ...) -> Dict`
**意图分类**，判断输入是：
- `chat_reply` — 寒暄回复
- `action_command` — 可执行命令
- `clarification_needed` — 需要澄清
- `automation_request` — 定时自动化请求

### `decide_local(query, candidates, context, rag_context) -> Dict`
**本地决策**（不调用云端）

### `decide_cloud(query, candidates, context, rag_context, context_summary) -> Dict`
**云端决策**（带隐私过滤后的上下文）

---

## Mock 模式意图分类逻辑

```python
def _mock_plan_intent(query, normalized_query, context):
    # 1. 安全检测优先
    safety = detect_safety_sensitive_request(raw_text)
    if safety: return clarification_needed

    # 2. 寒暄检测
    if _match_chat_reply(combined_text): return chat_reply

    # 3. 模糊表达检测
    if any(pattern in route_text for pattern in AMBIGUOUS_PATTERNS):
        return clarification_needed  # "像昨天那样"

    # 4. 自动化请求检测（有时间+动作）
    if _looks_like_automation_request(combined_text):
        return automation_request

    # 5. 动作意图检测（按优先级递减）
    if normalized_goal and _looks_like_action(normalized_goal):
        return action_command (confidence=0.92)

    if _looks_like_action(route_text):
        return action_command (confidence=0.82)

    if _looks_like_scene_switch_request(route_text):
        return action_command (confidence=0.88)

    if _looks_like_comfort_request(route_text):
        return action_command (confidence=0.80)
```

### 关键判断函数

#### `_looks_like_action(text)`
```python
# 设备关键词 OR 场景关键词 OR 显式动词
any(hint in text for hint in ACTION_HINTS)  # 打开/关闭/调高/调低...
or text in DEVICE_ACTION_MAP
or text in SCENE_ACTION_MAP
```

#### `_looks_like_comfort_request(text)`
```python
any(ck in text for ck in COMFORT_KEYWORDS)
# 热/冷/闷/亮/暗/吵/安静/困
```

#### `_looks_like_scene_switch_request(text)`
```python
any(sk in text for sk in SCENE_KEYWORDS)   # 睡眠/待客/离家...
and any(sw in text for sw in SWITCH_KEYWORDS)  # 切换/进入/开/启动
```

---

## Mock 决策逻辑

```python
def _mock_decide(query, candidates, context, rag_context):
    top = candidates[0]["action"]

    # 1. 精确设备动作映射（最高优先级）
    if top in DEVICE_ACTION_MAP:
        action, device, device_action, params = DEVICE_ACTION_MAP[top]
        return {"action": "设备控制", device, device_action, params, confidence}

    # 2. 场景动作映射
    if top in SCENE_ACTION_MAP:
        scene = SCENE_ACTION_MAP[top]
        return {"action": "场景切换", scene, device_action="scene", ...}

    # 3. 关键词兜底
    if "热" in query or "闷" in query:
        return {"action": "设备控制", device="空调", device_action="on", params: {"temperature": 26}}
    if "冷" in query:
        return {"action": "设备控制", device="空调", device_action="on", params: {"temperature": 28}}
    # ...更多关键词
```

---

## 云端决策流程

```
decide_cloud()
    ↓
构建 prompt: 上下文摘要 + RAG知识 + 候选列表
    ↓
CloudClient.complete(prompt) → 原始文本
    ↓
_parse_output(text) → 提取 JSON
    ↓
返回结构化命令

失败 → fallback decide_local()
```

---

## 后端优先级

```python
# plan_intent 中：
1. 如果 backend == "ollama" → 用 Ollama API
2. 否则如果 backend == "llama_cpp" → 用 llama_cpp
3. 否则如果 backend == "openai" 且 cloud 可用 → 用 OpenAI API
4. 最后 fallback → mock
```

**云端降级链**: `cloud → ollama → llama_cpp → mock`

---

## Prompt 构建

### 意图分类 Prompt
```
用户原始输入: {query}
归一化输入: {normalized_query}
环境摘要: {context_summary}

请判断这条输入属于哪一类：
intent_type: chat_reply | action_command | clarification_needed | automation_request
```

### 决策 Prompt
```
当前环境摘要: {cloud_context}
RAG知识: {rag_block}
用户输入: {query}
候选动作:
1. 打开空调
2. 关闭空调
...

必须包含: action, device, device_action, params, confidence, reasoning
```

---

## 面试核心问题清单

### 1. 为什么需要 plan_intent 和 decide 两个阶段？
- `plan_intent`: 判断"做什么类型的事"（聊天/执行/澄清/自动化）
- `decide`: 在候选集合中选择具体命令
- 分离的好处：可独立优化意图分类和命令选择

### 2. Mock 模式的局限性？
- 硬编码规则，无法处理新表达
- 适合开发调试，生产环境应切换 ollama/llama_cpp/openai
- 所有后端fallback最终都到mock

### 3. 如何保证云端决策的隐私安全？
- `build_cloud_context()` 只发送：时间/温度/湿度/在家人数/当前场景/top候选/偏好摘要
- **不发送**：原始用户输入全文、具体位置、门锁状态

### 4. confidence 分数的含义？
- 0.0-1.0，**由 LLM 返回或 mock 计算**
- `< confidence_threshold`（默认 0.75）→ 触发澄清
- 0.85+ → 可信执行
- 0.0 → JSON 解析失败或无法理解

### 5. 如果 LLM 返回的 JSON 格式错误怎么办？
- `_parse_output()` 用 `find("{")` + `rfind("}")` 提取
- JSON 解析失败 → 返回 `{"action": "无法理解", "confidence": 0.0}`

### 6. 不同后端的冷启动策略？
- **mock**: 即开即用，无依赖
- **ollama**: 连接失败 → 回退 mock
- **llama_cpp**: GPU 不可用 → CPU fallback，多种尝试
- **openai**: API 不可用 → 回退 decide_local()
