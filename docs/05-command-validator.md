# Command Validator 执行校验层 — 面试拷打指南

## 模块定位

Command Validator 是 HomeMind 执行层的**第一道门**，负责在命令真正执行前做最后的安全校验。包括：动作类型、参数范围、风险等级、速率限制。

```
LLM Decision 输出结构化命令
        ↓
CommandValidator.validate(command)
        ↓ (valid=True)
   执行设备控制 / 场景切换
        ↓
  返回执行结果给用户
```

---

## 核心接口

### `CommandValidator.validate(command) -> ValidationResult`

返回 `ValidationResult` 数据类：

```python
@dataclass
class ValidationResult:
    valid: bool                          # 是否可执行
    errors: List[str]                   # 错误列表
    normalized_command: Dict             # 标准化后的命令
    risk_level: str                     # low | medium | high
    requires_confirmation: bool          # 是否需要二次确认
    warnings: List[str] = field(...)     # 警告（非阻塞）
    rate_limited: bool = False           # 是否触发速率限制
    confidence: float = 0.0
```

---

## 校验流程

```
输入命令
   ↓
_normalize_command: 补全默认值（action/device/scene/device_action/params）
   ↓
类型校验: action in {设备控制, 场景切换, 信息查询}
   ↓
confidence 范围: [0.0, 1.0]
   ↓
设备控制校验 → _validate_device_control()
   场景切换校验 → _validate_scene_switch()
   信息查询校验 → _validate_info_query()
   ↓
风险等级评估 → _risk_level()
   ↓
速率限制检查 → _rate_limiter.check()
   ↓
返回 ValidationResult
```

---

## 设备白名单

```python
DEVICE_ACTIONS = {
    "空调": {"on", "off", "adjust"},
    "灯光": {"on", "off", "adjust"},
    "电视": {"on", "off", "adjust"},
    "热水器": {"on", "off", "adjust"},
    "风扇": {"on", "off", "adjust"},
    "音响": {"on", "off", "adjust"},
    "窗户": {"open", "close"},
}
```

**不在白名单的设备直接拒绝。**

---

## 参数边界校验

```python
PARAM_RANGES = {
    ("空调", "temperature"): (16, 30),      # °C
    ("热水器", "temperature"): (30, 75),   # °C
    ("灯光", "brightness"): (0, 100),       # %
    ("电视", "volume"): (0, 100),            # %
    ("音响", "volume"): (0, 100),           # %
    ("风扇", "speed"): (1, 5),             # 档位
}
```

**超界参数 → `errors`（阻塞执行）**  
**接近边界（2°C/2%以内）→ `warnings`（提醒但不阻止）**

---

## 风险等级

```python
def _risk_level(command):
    # 热水器在高温(>=60°C)时: high
    # 热水器在合理温度: medium
    # 窗户开关: medium
    # 其他: low

    if device == "热水器":
        if temp >= 60: return "high"
        return "medium"
    if device in ("窗户",):
        return "medium"
    return "low"
```

**`risk_level == "high"` → `requires_confirmation = True`**  
**高风险命令需要用户二次确认才能执行。**

---

## 速率限制

```python
class CommandRateLimiter:
    def __init__(window_s=30, max_ops=5):
        # 30 秒内同一设备动作最多 5 次
        self._ops[key] = [timestamp, ...]

    def check(device, device_action):
        # 清理过期记录
        self._ops[key] = [t for t in self._ops[key] if t > now - window_s]
        # 检查次数
        if len(self._ops[key]) >= max_ops:
            return False, "设备 {device} 在 {window_s}s 内操作过于频繁"
        return True, ""
```

**防止设备被频繁开关（如用户快速点按）**。

---

## 场景切换校验

```python
def _validate_scene_switch(command):
    # 优先用 scene_store（动态存储）
    if self.scene_store:
        if scene not in scene_store.list_scenes() and scene not in SCENE_CONFIGS:
            return [f"场景不在白名单: {scene}"]
    else:
        if scene not in SCENE_CONFIGS:
            return [f"场景不在白名单: {scene}"]
    return []
```

---

## 面试核心问题清单

### 1. 为什么需要标准化这一步？
```python
normalized.setdefault("action", "")
normalized.setdefault("device", "")
```
- LLM 可能返回不完整的命令结构
- 标准化补全默认值，避免后续空指针

### 2. confidence 为什么要校验 [0, 1]？
- 防止 LLM 注入异常值
- 置信度超界说明 LLM 输出异常

### 3. 为什么热水器的参数边界是 30-75°C？
- 低于 30°C：热水器没意义
- 高于 75°C：烫伤风险
- 高于 60°C：`risk_level = "high"`，需要二次确认

### 4. 速率限制的窗口滑动设计？
- 固定窗口：简单但不够平滑
- 当前：固定窗口（30s 内最多 5 次）
- 可改进：滑动窗口更平滑，但实现更复杂

### 5. risk_level 和 requires_confirmation 的关系？
```python
risk_level = self._risk_level(normalized)
requires_confirmation = risk_level == "high"
```
- 只有 `high` 才触发确认
- `medium` 只记录 warning，不阻止执行

### 6. 场景白名单为什么要支持动态扩展？
```python
dynamic_scenes = set(self.scene_store.list_scenes())
```
- 用户可以创建自定义场景
- 自定义场景也需要通过校验才能执行

### 7. 校验失败后的处理？
- `valid=False` → 执行层直接返回错误，不执行设备操作
- `requires_confirmation=True` → 返回确认提示，等待用户二次确认
- `rate_limited=True` → 阻止执行，提示等待

### 8. 校验层如何防御注入攻击？
- 设备名白名单：不在列表中的设备名全部拒绝
- 参数类型校验：`isinstance(value, (int, float))`
- 动作白名单：不在 `DEVICE_ACTIONS` 中的动作拒绝
