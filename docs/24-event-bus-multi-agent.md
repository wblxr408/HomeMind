# Event Bus + Multi-Agent — 面试拷打指南

## 核心问题：为什么需要 Event Bus？

**场景**：HomeMind 有 DeviceAgent、SceneAgent、MemoryAgent、InfoAgent 多个专家 Agent。Coordinator 需要把任务分发给它们，然后聚合结果。问题是：如何让 Agent 之间松耦合地通信？

**不用 Event Bus 的方案**：
- 直接调用：`result = await scene_agent.handle(query)` → 硬耦合，Coordinator 必须知道所有 Agent
- 回调函数：每个 Agent 注册回调 → 类型不安全，难以管理生命周期

---

## 技术选型：进程内通信方案对比

| 维度 | 直接调用 | 回调函数 | **Event Bus（最终）** |
|------|----------|----------|----------------------|
| **耦合度** | 高 | 中 | **低** |
| **类型安全** | 强 | 弱 | **中（Enum EventType）** |
| **广播能力** | 无 | 困难 | **原生支持** |
| **错误隔离** | 失败向上传播 | 需手动 try/catch | **自动隔离** |
| **历史记录** | 无 | 无 | **有（最后100条）** |
| **异步支持** | 原生 | 需包装 | **原生** |

**选择 Event Bus 的原因**：
- Agent 解耦：发布者和订阅者互不相识，通过 EventType 字符串通信
- 错误隔离：单个 handler 异常不影响其他 handler 和主流程
- 广播能力：设备状态变化可通知多个订阅者（日志、告警、UI更新）

---

## 事件类型设计：为什么用 Enum 而不是字符串？

```python
class EventType(Enum):
    USER_QUERY = "user_query"
    DEVICE_STATE_CHANGE = "device_state_change"
    SCENE_ACTIVATED = "scene_activated"
    AGENT_HANDOVER = "agent_handover"
    PEER_MESSAGE = "peer_message"
    ...
```

### 对比

| 方案 | 类型安全 | IDE 自动补全 | 可枚举 |
|------|----------|-------------|--------|
| 字符串 "user_query" | 无 | 无 | 无 |
| **Enum（最终选择）** | 强 | **有** | **有** |
| Class 继承 | 强 | 有 | 需反射 |

**选择 Enum 的原因**：
- `EventType.USER_QUERY` 比 `"user_query"` 更安全， typos 在编译期就报错
- `EventBus.subscribe()` 参数类型是 `EventType`，运行时类型检查
- `get_history(event_type=EventType.USER_QUERY)` 参数类型约束

---

## 发布-订阅模型：为什么 handler 用 fire-and-forget？

```python
async def publish(self, event: Event) -> None:
    async with self._lock:
        handlers = list(self._subscribers.get(event.type, []))
        self._event_history.append(event)

    # fire-and-forget：不在 lock 内执行 handler
    for handler in handlers:
        asyncio.create_task(self._safe_handler(handler, event))
```

### 对比

| 方案 | 延迟 | 可靠性 | 复杂度 |
|------|------|--------|--------|
| 同步等待（全部 handler 完成） | 高 | 高 | 中 |
| **fire-and-forget（最终选择）** | **低** | 中 | 低 |
| 等待 N 个 handler | 中 | 中高 | 高 |

**选择 fire-and-forget 的原因**：
- 发布者不应等待所有订阅者完成（如日志记录不应该阻塞设备控制）
- `asyncio.create_task` 将 handler 放入事件循环，不阻塞主流程
- 错误被 `_safe_handler` 隔离，handler 异常不影响其他 handler

**潜在问题**：如果所有 handler 都是 fire-and-forget，发布者无法知道处理结果。这对 HomeMind 是可接受的——DeviceAgent 处理完成后会发布 `DEVICE_STATE_CHANGE` 事件，其他订阅者（UI 更新、告警检查）可以自行处理。

---

## CoordinatorAgent：路由分发策略

### 为什么不用一个 Agent 处理所有请求？

| 方案 | 适用场景 | 局限性 |
|------|----------|--------|
| 单一大 Agent | 简单任务 | 职责不清，难以扩展 |
| **Coordinator + Specialist（最终）** | 复杂多领域 | 需要协调层 |
| 完全对等（无 Coordinator） | 松耦合多 Agent | 结果聚合困难 |

**HomeMind 的设计**：Coordinator 负责意图分类路由（用现有 InferenceRouter），然后按路由方向选择相关的 Specialist：

```python
route_to_role = {
    "local": [AgentRole.DEVICE, AgentRole.SCENE],     # 设备/场景控制
    "cloud": [AgentRole.MEMORY, AgentRole.INFO],      # 知识/信息查询
    "clarify": [],                                    # 无需 specialist
    "fallback": list(specialists.keys()),             # 全问
}
```

### 并行分发策略

```python
tasks = [self._delegate(agent, query, context, trace_id) for agent in relevant]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

**为什么并行而不是串行？**
- "打开空调" → DeviceAgent + SceneAgent 都可以处理，并行节省时间
- `asyncio.gather(return_exceptions=True)` 确保一个 Agent 失败不影响整体结果

---

## SpecialistAgent 的 attach_bus 设计

```python
# Coordinator 初始化时
for spec in specialists:
    self.specialists[spec.role.value] = spec
    spec.attach_bus(self.bus)  # 每个 specialist 挂载 Event Bus

# Specialist 内部可以发布事件
await self.bus.publish(Event(
    type=EventType.DEVICE_STATE_CHANGE,
    source="DeviceAgent",
    payload={"device": "空调", "status": "on"},
))
```

**为什么不直接传 bus 作为参数？** 保持接口简洁——Specialist 只需要知道"有一个 bus 可以发布事件"，不需要显式注入。

---

## 与现有五层架构的关系

```
用户查询
  ↓
HomeMindAgent.process()  ← 现有链路（BSR→LSR→LLM）
  ↓
CoordinatorAgent.handle()  ← 新增：多 Agent 协调
  ├─ InferenceRouter.classify_intent()
  ├─ 并行分发到 SpecialistAgent
  └─ 聚合结果
```

**关键点**：CoordinatorAgent 不替代现有链路，而是并行运行。SpecialistAgent（如 DeviceAgent）调用的是 `DeviceController`——与现有执行层完全一致。

---

## 面试追问

**Q: 如果 DeviceAgent 和 SceneAgent 对同一请求返回冲突的决策怎么办？**

当前 `CoordinatorAgent._aggregate()` 只收集所有成功响应，不做冲突检测。冲突解决可以：
1. 在 `CoordinatorAgent` 层加决策优先级（device > scene > memory）
2. 在 Specialist 层返回置信度，Coordinator 选最高的

**Q: Event Bus 单例在多进程场景下怎么工作？**

Event Bus 是**进程内**通信，不同进程之间的通信由 `MeshTransport` 处理。Event Bus 和 Mesh Transport 组合：进程内用 Event Bus，进程间用 Mesh Transport（通过 `PEER_MESSAGE` 事件类型桥接）。

**Q: 100 条历史记录上限怎么确定的？**

经验值。HomeMind 典型 session 事件约 10-50 条，保留 100 条足够调试和审计，又不会占用过多内存。可通过 `EventBus.__init__(max_history=...)` 参数调整。
