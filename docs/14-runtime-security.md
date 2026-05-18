# Runtime Security Chain 运行时安全链 — 面试拷打指南

## 模块定位

RuntimeSecurityChain 是 HomeMind 的**多层安全防御层**，整合身份认证、策略评估、异常频率检测和渐进式自主权限管理。

```
命令校验通过
      ↓
RuntimeSecurityChain.evaluate(command, validation, identity, runtime_context)
      ↓
身份授权 → 策略评估 → 异常检测 → 自主权限
      ↓
返回 allowed + effect (allow / confirm / deny)
```

---

## 核心接口

### `evaluate(command, validation, identity, runtime_context) -> Dict`

返回：
```python
{
    "allowed": True/False,
    "effect": "allow" | "confirm" | "deny",
    "reason": str,
    "policy": str,
    "identity": IdentityContext,
    "autonomy_confirmation": bool,
    "advisory_only": bool,  # sim模式下的建议标记
}
```

---

## 四层安全防御

### 1. 身份授权 (Zero-Trust Identity)

```python
def authorize(identity, command, risk_level):
    if action not in identity.capabilities:
        return {"allowed": False, "reason": "capability_missing"}

    if risk_level == "high" and identity.trust_level not in {"session", "verified"}:
        return {"allowed": False, "reason": "high_risk_identity_insufficient"}

    return {"allowed": True, "reason": "authorized"}
```

- 基于能力的授权，而非角色
- 高风险命令要求 `trust_level >= "verified"`
- 默认能力：`["设备控制", "场景切换", "信息查询"]`

### 2. 策略评估 (PolicyEngine)

将命令上下文传递给 PolicyEngine，返回 `allow / confirm / deny`。

### 3. 异常频率检测

```python
# 60 秒内同一设备动作超过 8 次 → 异常
anomaly_window_s = 60
anomaly_max_ops = 8

if len(_ops[key]) > anomaly_max_ops:
    return {"detected": True, "reason": "runtime_anomaly_high_frequency"}
```

**仅在非模拟模式下触发。**

### 4. 渐进式自主权限 (AutonomyManager)

```python
autonomy_confirm = is_confirmation_required(device, risk_level)
needs_confirm = (
    validation.requires_confirmation or  # 校验层要求确认
    policy_result.effect == "confirm" or  # 策略要求确认
    autonomy_confirm  # 自主管理器要求确认
)
```

---

## 模拟模式特殊处理

```python
if mode == "simulated":
    needs_confirm = False  # 不需要确认
    return {
        "advisory_only": True,  # 标记为建议，不实际执行
    }
```

**模拟模式下所有安全检查记录但不阻止执行。**

---

## 面试核心问题

### 1. Zero-Trust 在智能家居中的意义？
- 每次命令都验证能力，而非信任会话
- 临时访客可能被限制某些设备
- 可动态调整 trust_level

### 2. 为什么异常检测只在非模拟模式触发？
- 模拟模式用于开发和测试，快速多次操作是正常的
- 生产模式需要真实保护

### 3. advisory_only 标记的作用？
```python
"advisory_only": mode == "simulated" and (confirm or anomaly or autonomy)
```
- 告知执行层：这些检查结果是"建议"，不是强制
- 模拟模式记录所有安全事件但不阻塞

### 4. 四层防御的顺序？
```
身份授权 → 策略评估 → 异常检测 → 自主权限
```
- 身份最前：无效身份直接拒绝
- 策略次之：组织级安全策略
- 异常居三：检测行为异常
- 自主最后：学习式权限渐进开放
