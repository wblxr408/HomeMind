# NL to TAP 自然语言转自动化规则 — 面试拷打指南

## 模块定位

NLToTAP 将用户的自然语言自动化描述（如"每天早上7点打开空调"）转换为结构化的 TAP 规则，存入 SceneStore 供 TAPEngine 执行。

```
用户: "每天早上7点打开空调"
        ↓
NLToTAPConverter.convert("每天早上7点打开空调")
        ↓
{
    "trigger": {"type": "time", "at": "07:00"},
    "conditions": [],
    "action": {"type": "device_control", "device": "空调", "device_action": "on", "params": {}},
    "priority": 50,
    "enabled": True,
    "name": "用户创建的自动化规则",
    "description": "每天早上7点打开空调"
}
        ↓
SceneStore 保存
```

---

## 核心接口

### `NLToTAPConverter.convert(nl_text) -> Dict`
将自然语言转换为 TAP 规则字典。

---

## 时间解析

```python
TIME_PATTERNS = [
    (r"每天早上(\d+)点", "time"),      # "每天早上7点"
    (r"每天晚上(\d+)点", "time"),      # "每天晚上8点"
    (r"每天(\d+)点(\d+)分", "time"),   # "每天7点30分"
    (r"每小时的第(\d+)分", "time"),    # "每小时的第30分"
    (r"每隔(\d+)分钟", "interval"),    # "每隔10分钟"
]
```

---

## 设备/动作解析

```python
DEVICE_MAP = {
    "空调": ("空调", ["打开", "关闭", "调高", "调低"]),
    "灯光": ("灯光", ["打开", "关闭", "调亮", "调暗"]),
    ...
}
```

---

## 面试核心问题

### 1. 为什么需要 NL → TAP 转换？
- 用户不熟悉结构化规则语法
- 自然语言更直观易用
- 降低自动化创建门槛

### 2. 局限性？
- 当前实现解析能力有限，只能识别预定义模式
- 无法处理复杂条件（如"温度高于28度时"）
- 复杂规则仍需要手动编辑

### 3. 转换失败的策略？
- 返回空规则 + 错误提示
- 引导用户使用更简洁的表达
