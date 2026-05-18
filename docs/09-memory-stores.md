# Session Store & Preference Store — 面试拷打指南

## 模块定位

记忆存储层，包含两个组件：
- **SessionStore**：短期会话状态（当前场景、最近轮次、待确认项）
- **PreferenceStore**：长期用户偏好（设备习惯、场景接受率、DQN学习记录）

```
用户交互
  ↓
SessionStore — 更新 last_action / recent_turns / pending_confirmation
  ↓
PreferenceStore — 记录偏好 / DQN反馈 / 语言纠正
  ↓
KnowledgeBase — 持久化记忆
```

---

## SessionStore 核心接口

### `update_from_query(raw_text, normalized_text)`
记录用户输入，更新会话历史。

### `update_from_decision(decision, route, result)`
记录决策结果，**更新当前场景**（如果决策包含场景切换）：
```python
if decision.get("scene"):
    self.data["current_scene"] = scene
```

### `get_pending_confirmation()`
返回待确认的自动化任务（如定时规则创建）。

### `append_turn(role, text)`
追加一轮对话到 `recent_turns`，最多保留 8 轮：
```python
if len(turns) > max_recent_turns:
    turns = turns[-max_recent_turns:]  # 保留最新轮次
```

---

## PreferenceStore 核心接口

### `record_action_accept(decision, context)`
记录用户接受的决策，更新设备偏好：
```python
# 空调温度偏好
if device == "空调" and "temperature" in params:
    device_entry["preferred_temperature"] = int(round(temp))

# 场景接受率
if scene:
    scene_entry["accept_count"] += 1
    scene_entry["preferred_hour"] = context.hour
```

### `record_feedback(raw_text, normalized_text, feedback)`
记录方言纠正，更新语言模型：
```python
dialect_terms[raw_text] = normalized_text  # "有点闷" → "太热了"
```

### `get_preference_boost(candidate_action, context) -> float`
计算候选动作的偏好加成（0.0-1.0），供 LSR 使用。

---

## 加密存储

两者都使用加密存储接口：
```python
self._storage = get_encrypted_storage()
ok = self._storage.save_pickle(self.data, self.path)
```

**如果加密存储不可用**，数据不会持久化（`save()` 返回 False）。

### 兼容明文迁移
```python
def _looks_like_plaintext(self):
    with open(self.path, "rb") as f:
        prefix = f.read(32).lstrip()
    return prefix.startswith(b"{")  # 明文 JSON 以 "{" 开头
```

---

## 面试核心问题

### 1. 为什么分 Session 和 Preference 两个存储？
- Session：短期，每次会话重建
- Preference：长期，跨会话持久化
- 不同生命周期、不同访问频率

### 2. 为什么 recent_turns 限制 8 轮？
- 上下文窗口有限，太多历史反而引入噪声
- 8 轮足够理解当前会话主题
- 平衡内存占用和上下文信息量

### 3. 为什么偏好分数用 `accept_count * 0.05` 封顶？
```python
score += min(0.25, accept_count * 0.05)  # 最多 5 次后封顶
```
防止用户历史数据过多导致分数饱和，保留学习的弹性。

### 4. 明文兼容迁移的意义？
- 早期版本可能存储明文 JSON
- 升级后首次运行自动迁移到加密格式
- 用户无感知，数据不丢失
