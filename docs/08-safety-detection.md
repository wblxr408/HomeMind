# Safety 安全检测 — 面试拷打指南

## 模块定位

`safety.py` 提供最基础的安全检测能力，在用户输入进入任何处理流程之前，检测是否涉及门锁、安防、燃气等安全敏感设备。

```
用户输入
  ↓
detect_safety_sensitive_request() — 安全检测
  ↓
(返回 clarification_needed)
```

---

## 核心接口

### `detect_safety_sensitive_request(query, normalized_query) -> Optional[Dict]`
返回 `None` 表示安全通过，返回 `Dict` 表示检测到安全敏感请求。

---

## 检测范围

### 1. 安全敏感目标关键词
```python
SECURITY_SENSITIVE_TARGETS = [
    "门锁", "智能锁", "门禁", "防盗门",
    "入户门", "家门", "大门", "房门",
    "安防", "报警器", "摄像头", "监控",
    "燃气阀", "煤气阀",
]
```

### 2. 门动作关键词
```python
DOOR_SECURITY_ACTIONS = [
    "锁门", "开门", "关门", "解锁", "上锁", "反锁", "开锁",
]
```

**检测逻辑**:
```python
# 1. 包含安防设备关键词
for target in SECURITY_SENSITIVE_TARGETS:
    if target in haystack:
        return clarification_needed

# 2. 包含门 + 门动作关键词
if "门" in compact and any(token in compact for token in ("锁", "解锁", "上锁", ...)):
    return clarification_needed
```

### 3. 返回消息
```python
SAFETY_CLARIFICATION_MESSAGE = (
    "这个请求涉及门锁、安防或家庭安全设备。"
    "为避免误操作，我需要先澄清："
    "你要操作哪个具体设备、执行什么动作，以及是否确认当前环境安全？"
)
```

---

## 使用位置

`safety.py` 被以下模块调用：

1. **LLM Decision** (`core/llm/decision.py`):
   ```python
   def _mock_plan_intent(query, normalized_query, context):
       safety = detect_safety_sensitive_request(raw_text, ...)
       if safety:
           return clarification_needed
   ```

2. **InferenceRouter** (`core/router/inference_router.py`):
   ```python
   def classify_intent(query, normalized_query):
       safety = detect_safety_sensitive_request(raw_text, ...)
       if safety:
           return safety  # 直接返回，不进入精排
   ```

---

## 面试核心问题清单

### 1. 为什么安全检测要放在最前面？
- 门锁/安防误操作后果严重
- 一旦误执行可能造成安全隐患
- 前置检测避免任何后续流程浪费资源

### 2. 为什么用关键词而非模型检测？
- 关键词检测：零延迟、无模型依赖、稳定可解释
- 模型检测：可能有误判，引入不确定性
- 安全领域宁可"多拦不可放过"

### 3. 为什么同时检测"门+动作"组合？
```python
compact = "".join(haystack.split())  # 去空格
if "门" in compact and any(token in compact for token in ("锁", "解锁", ...)):
```
- 单独"门"字太常见（"大门"/"门铃"等无害）
- 组合"门+动作"才是安全风险

### 4. 返回 clarification 而非直接拒绝？
- 门锁请求不一定是恶意，可能是正常需求（如帮家人开门）
- 澄清而非拒绝，保留用户体验
- 确认后仍可执行
