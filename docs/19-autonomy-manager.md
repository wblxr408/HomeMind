# AutonomyManager 渐进式自主权 — 面试拷打指南

## 模块定位

AutonomyManager 根据设备的历史执行成功率，动态调整 Agent 的自主执行权限。新设备初期强制人工确认，熟练后逐步放权。

```
Agent 执行设备控制
        ↓
AutonomyManager.is_confirmation_required(device, risk_level)
        ↓
autonomy_level < 3 → 需要确认
autonomy_level >= 3 → 直接执行
        ↓
用户反馈（接受/拒绝/忽略）
        ↓
AutonomyManager.record_operation(device, success)
        ↓
成功率达标 → 提升 autonomy_level
失败率过高 → 降低 autonomy_level
```

---

## 核心概念

### autonomy_level（自主权等级）

| 等级 | 含义 |
|------|------|
| 5 | 完全自主，无需确认 |
| 4 | 极少确认 |
| 3 | 偶尔确认 |
| 2 | 经常确认 |
| 1 | **每次都确认** |

**`is_confirmed_required()`**: `autonomy_level < 3` 时需要确认。

---

## 升级策略

```python
# 连续成功5次，且未达上限 → 升级
if successful_ops >= 5 and level < max_level:
    autonomy_level += 1
    successful_ops = 0  # 重置计数
```

---

## 降级策略

```python
# 成功率低于 60%，且操作过3次 → 降级
if total_ops >= 3 and success_rate < 0.60:
    autonomy_level = max(1, autonomy_level - 1)
```

---

## 高风险设备限制

```python
HIGH_RISK_DEVICES = {"热水器", "窗户"}
MAX_AUTONOMY = 5           # 普通设备最高5级
HIGH_RISK_MAX_AUTONOMY = 3  # 高风险设备最高3级
```

**热水器/窗户最多升到3级，即使连续成功也不会到4或5。**

---

## 面试核心问题

### 1. 为什么热水器的 autonomy_level 上限是3？
- 即使连续成功，也不允许完全自主
- 热水器高温有烫伤风险
- 窗户有安全/防盗风险
- 保留人工确认作为安全兜底

### 2. 为什么不直接用 success_rate 判断确认需求？
- 需要累计一定样本（3次）才判定
- 避免单次失败立即降级
- 连续5次成功才升级，防止频繁波动

### 3. successful_ops 为什么要重置？
```python
autonomy_level += 1
successful_ops = 0  # 重置计数器
```
- 每升一级后重置，避免连续升级太快
- 保证每次升级都有最近的5次成功记录

### 4. 如何防止记忆污染？
- 持久化：`to_dict()` / `load_from_dict()`
- 每次操作后记录到 PreferenceStore
- 系统重启后恢复自主权状态
