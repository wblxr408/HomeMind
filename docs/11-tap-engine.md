# TAP Engine 定时自动化 — 面试拷打指南

## 模块定位

TAP（Time/condition/Action/Priority）Engine 在每次用户交互时检查已注册的定时/条件规则，自动触发设备控制或场景切换。

```
每次 process() 调用
        ↓
TAPEngine.evaluate(context, rules)
        ↓
[匹配规则]
        ↓
执行设备控制 / 场景切换
        ↓
返回 TAP 执行结果
```

---

## 核心接口

### `TAPEngine.evaluate(context, rules, now) -> List[Dict]`
返回所有匹配且未被冲突解决的规则。

---

## 触发器类型

```python
def _trigger_matches(trigger, context, now):
    if type == "time":
        return trigger["at"] == now.strftime("%H:%M")  # 精确到分钟

    if type == "temperature":
        return compare(context.temperature, op, trigger["value"])

    if type == "humidity":
        return compare(context.humidity, op, trigger["value"])

    if type == "occupancy":
        return compare(context.members_home, op, trigger["value"])

    if type == "scene":
        return context.current_scene == trigger["equals"]

    if type == "day_of_week":
        return now.weekday() in {int(d) for d in trigger["days"]}
        # 0=周一, 6=周日

    if type == "holiday":
        return now.month == trigger["month"] and now.day == trigger["day"]
```

---

## 条件评估

```python
def _conditions_match(conditions, context):
    for condition in conditions:
        if type == "occupancy":
            if not compare(context.members_home, op, value):
                return False

        if type == "scene":
            if context.current_scene != condition.equals:
                return False

        if type == "device_status":
            if devices[device] != expected:
                return False
    return True
```

---

## 冲突解决

```python
def _resolve_conflicts(matched):
    seen = set()
    accepted = []
    for item in matched:
        key = _conflict_key(command)
        if key in seen: continue  # 同一设备只执行一次
        seen.add(key)
        accepted.append(item)
    return accepted
```

**冲突策略：先匹配先执行，同设备只执行最高优先级规则。**

---

## 面试核心问题

### 1. 为什么冲突解决用先到先得而非最高优先级？
```python
# 当前实现：遍历排序后的 rules
for rule in sorted(rules, key=lambda r: priority, reverse=True):
    ...
# 实际上只是去重，不是严格按优先级
```
当前实现按优先级排序，但同一设备只执行一次（去重）。这意味着：
- 同一设备，优先级高的规则执行，优先级低的被跳过
- 不同设备可以同时执行

### 2. 为什么触发器用精确时间匹配？
```python
if type == "time":
    return trigger["at"] == now.strftime("%H:%M")
```
- 每分钟调用一次 evaluate，所以只需匹配"HH:MM"即可
- 不需要秒级精度，智能家居场景分钟级足够

### 3. 为什么假期需要外部日历服务？
```python
# 本地只支持固定日期（5月1日）
# 可移动假期（春节/端午）需要农历转换
```
- 固定假期：五一/国庆/元旦
- 农历假期：春节/清明/端午/中秋 — 无法本地计算

### 4. day_of_week 如何对应星期？
```python
# Python: 0=周一, 6=周日
# JSON配置: [5, 6] = 周六、周日
return now.weekday() in {int(d) for d in trigger["days"]}
```
测试用例：`day_of_week=6`（周日）匹配周六、周日。
