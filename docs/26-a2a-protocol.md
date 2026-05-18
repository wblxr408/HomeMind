# A2A 协议 — 面试拷打指南

## 核心问题：A2A 和 MCP 的关系是什么？为什么两个都要？

**场景**：Claude Desktop 连接 HomeMind，用 MCP 调用 device_control 工具。这是 Agent→Tool 模式。

但如果 HomeMind（一个 Agent）需要向另一个 Agent（如"餐厅推荐 Agent"）发消息问"附近有什么餐厅"，这不是工具调用，而是 Agent 之间的对话协作。

---

## MCP vs A2A：功能边界对比

| 维度 | **MCP（最终）** | **A2A（最终）** |
|------|----------------|----------------|
| **通信方向** | Agent → Tool | Agent → Agent |
| **交互模式** | 一次调用，有结果返回 | 多轮对话，有状态 |
| **协议定义** | Anthropic MCP SDK | Google/Linux Foundation A2A |
| **核心概念** | Tool（工具） | Task（任务）、Message（消息）、AgentCard |
| **状态管理** | 无状态 | 有状态（Task 有生命周期）|
| **适用场景** | 调用设备、查知识库 | 委托任务、协作推理 |
| **协议层级** | 协议层 | 应用层 |

**两者互补**：MCP 是底层通信协议，A2A 是上层协作框架。一个 HomeMind 节点可以同时是 MCP Server（被 Claude 调用）和 A2A Client（调用其他 Agent）。

---

## Agent Card：为什么需要标准化元数据卡片？

```python
@dataclass
class AgentCard:
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: List[str]  # ["device_control", "scene_management"]
    skills: List[Dict[str, str]]  # [{"id": "device_ctrl", "name": "设备控制"}]
    authentication: str  # "none" | "jws" | "bearer"
    tags: List[str]
```

### 对比

| 方案 | 服务发现 | 元数据丰富度 | 标准化 |
|------|----------|------------|--------|
| 直接 HTTP GET /info | 支持 | 低 | 无 |
| OpenAPI/Swagger | 高 | 高 | 有 | **A2A Agent Card** |
| 直接 URL 硬编码 | 无 | 无 | 无 |

**选择 A2A Agent Card 的原因**：
- 服务发现：`discover_agent(url)` → 自动解析 Agent 元数据
- 能力描述：`capabilities` 告诉调用方"我能做什么"
- 技能清单：`skills` 描述每个技能的 ID 和名称，与 MCP 工具对应
- 标准化：符合 Linux Foundation A2A 规范，可与任何 A2A 兼容 Agent 互通

---

## Task：有状态工作单元 vs 无状态请求

### 对比

| 方案 | 多轮对话 | 中间状态可见 | 取消能力 |
|------|----------|-------------|----------|
| REST API（无状态）| 需自行维护 session | 无 | 无 |
| WebSocket 流 | 原生支持 | 有 | 困难 |
| **A2A Task（最终）** | **原生支持** | **有（status 追踪）** | **有（CANCELED）** |

**A2A Task 的状态机**：

```
SUBMITTED → WORKING → COMPLETED
                ↓
         INPUT_REQUIRED（需用户输入）
                ↓
              FAILED / CANCELED
```

**为什么需要状态机？** 智能家居 Agent 协作可能很复杂：
1. 用户说"帮我找个餐厅" → Task SUBMITTED
2. 餐厅 Agent 开始搜索 → Task WORKING
3. 找到3个选项，需要用户选择 → Task INPUT_REQUIRED
4. 用户回复"第二个" → Task WORKING
5. 确认预订 → Task COMPLETED

每一步状态都记录在 Task 中，可查询、可取消。

---

## Task vs Event：什么时候用哪个？

```python
# Event：通知型，轻量，不需要回复
await bus.publish(Event(type=EventType.DEVICE_STATE_CHANGE, source="DeviceAgent", ...))

# Task：有状态工作单元，需要追踪进度和结果
task_id = await a2a.submit_task(agent_card, "帮我找个餐厅")
```

| 维度 | **Event（最终）** | **Task（最终）** |
|------|-------------------|------------------|
| **目的** | 通知 | 委托 |
| **回复期望** | 无 | 有 |
| **生命周期** | 即时 | 可长（跨多轮）|
| **取消能力** | 无 | 有（CANCELED）|
| **适用场景** | 状态变化、日志 | 跨 Agent 协作请求 |

---

## A2A 和 MeshTransport 的关系

```
A2AProtocol.submit_task()
  └─ POST /a2a/tasks
  └─ 走 HTTP/WebSocket
  └─ MeshTransport 处理传输

LocalDiscovery.discover_agent()
  └─ mDNS 发现节点
  └─ 获取 .well-known/agent.json
  └─ 解析为 AgentCard
```

**为什么不直接用 Mesh Transport 传输 A2A 消息？** A2A 定义的是应用层协议（Task 状态机、Message 格式），Mesh Transport 定义的是传输层（WS/HTTP）。两者正交——Mesh Transport 可以承载 A2A 消息。

---

## 与 MCP 的集成点

```
Claude Desktop
  └─ MCP stdio → HomeMind（调用 device_control 工具）
                   └─ A2A → MeshTransport → 另一个 HomeMind 节点
```

**典型的多节点协作流**：
1. 用户在手机上说"帮我关客厅灯"
2. 手机 HomeMind（MCP Server）处理，调用 device_control
3. 客厅的 HomeMind 节点（通过 A2A 收到指令）
4. 客厅 HomeMind 执行 scene_control（设备控制）

---

## 面试追问

**Q: A2A 协议支持流式响应吗（SSE/streaming）？**

A2A 规范支持 SSE 用于 Task 状态推送。当前实现中 `A2AProtocol` 使用 `requests.post` 的同步调用，适合短任务。长任务（如"帮我安排一周的日程"）应使用 SSE 流式更新状态，MeshTransport 的 WebSocket 连接可以承载 SSE 事件。

**Q: Agent Card 的 authentication 字段有哪些选项？**

A2A 规范定义了三种认证方式：
- `none`：无认证（局域网内同设备）
- `jws`：JSON Web Signature，适合跨组织安全认证
- `bearer`：Bearer Token，适合 API Key 场景

HomeMind 当前默认 `none`，局域网内信任。生产环境建议用 `bearer`（预共享 API Key）。

**Q: 如果远端 Agent 不响应，Task 会一直卡着吗？**

当前实现中 `submit_task` 有 10 秒 HTTP timeout，超时后 Task 进入 FAILED 状态。MeshTransport 的 Store-and-Forward 机制可以暂存离线消息，但 Task 的超时管理还需要上层（CoordinatorAgent）处理。
