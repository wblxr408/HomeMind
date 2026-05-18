# Privacy Redactor 隐私过滤 — 面试拷打指南

## 模块定位

PrivacyRedactor 在用户数据发送到云端 LLM 之前，对上下文进行**最小化过滤**，移除个人隐私信息，仅保留智能家居决策所需的最小信息集。

```
用户输入 + 上下文
      ↓
PrivacyRedactor.build_cloud_context()
      ↓
云端 LLM 决策
```

---

## 核心接口

### `build_cloud_context(context, candidates, session_store, preference_store) -> Dict`

构建发送到云端的最小上下文：

```python
payload = {
    "hour": context.hour,
    "temperature": context.temperature,
    "humidity": context.humidity,
    "occupancy": context.members_home,       # 在家人数
    "scene": current_scene,
    "top_candidates": [...],                 # 候选动作名
    "preference_summary": {...},             # 偏好摘要
}
```

---

## 场景来源优先级

```python
# 1. 优先从 context 获取
current_scene = getattr(context, "current_scene", "")

# 2. Fallback: session_store
if not current_scene:
    current_scene = session_store.get_current_scene()

# 3. Fallback: last_scene 索引
if not current_scene:
    last_scene = getattr(context, "last_scene", -1)
    current_scene = SCENE_NAMES.get(last_scene, "")
```

---

## 敏感信息过滤

### `redact_text(text)` — 文本隐私过滤
```python
re.sub(r"\b1\d{10}\b", "[PHONE]", text)       # 手机号
re.sub(r"\b[\w.-]+@[\w.-]+\.\w+\b", "[EMAIL]", text)  # 邮箱
re.sub(r"\b\d{15,18}[0-9Xx]\b", "[ID]", text)     # 身份证
```

---

## 面试核心问题

### 1. 为什么要隐私过滤？
- 云端 LLM 处理用户数据需要最小化暴露
- 手机号、邮箱、身份证绝不发送到云端
- 智能家居决策只需要：时间、温度、在家人数、场景

### 2. 为什么不直接发送完整上下文？
- 违反隐私最小化原则
- 云端数据泄露风险
- 法规合规（GDPR、个人信息保护法）

### 3. preference_summary 包含什么？
```python
{
    "preferred_ac_temp": 26,        # 空调偏好温度
    "preferred_light_brightness": 70,  # 灯光偏好亮度
    "preferred_scene": "睡眠模式",    # 最常接受场景
}
```
只有统计聚合值，无原始交互数据。
