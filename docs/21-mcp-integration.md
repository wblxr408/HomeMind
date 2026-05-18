# MCP 双向集成 — 面试拷打指南

## 核心问题：为什么需要 MCP？

**场景**：Claude Desktop / Cursor 需要控制用户的智能家居，但它们不认识 HomeMind 的 API。反过来，HomeMind 也需要调用外部工具（如天气预报 API）。

**HomeMind 的解法**：同时实现 MCP Server 和 MCP Client。

---

## 技术选型：为什么选 MCP 而不是自己造轮子？

### 方案对比

| 维度 | 自定义 JSON-RPC API | OpenAI Tool Format | **MCP（最终选择）** |
|------|---------------------|--------------------|---------------------|
| **生态支持** | 需自建文档/SDK | 仅限 OpenAI 模型 | Anthropic/Claude/Cursor 官方支持 |
| **跨平台** | 每次集成需适配 | 模型绑定 | 开放标准，多厂商实现 |
| **双向通信** | 支持，但需自研 Client | 单向（工具调用） | Server+Client 双支持 |
| **工具发现** | 手动维护清单 | 手动维护清单 | 标准化 `list_tools` + Schema |
| **传输层** | 自选（HTTP/WS） | 模型内建 | stdio + SSE + HTTP 多协议 |
| **维护成本** | 高（协议/版本/文档全自维护） | 低 | 极低（MCP SDK 接管） |

**结论**：HomeMind 需要同时作为 Server（暴露能力）和 Client（调用外部工具）。MCP 是目前唯一原生支持双向的开放标准，且 Anthropic/Model Context Protocol 社区活跃，SDK 成熟度高（`mcp>=1.27.0`）。

---

## Server 实现：stdio vs Streamable HTTP，选哪个？

### 选型对比

| 维度 | **stdio（子进程模式）** | Streamable HTTP |
|------|------------------------|----------------|
| **适用场景** | Claude Desktop / Cursor 插件 | 远程服务 / 微服务间通信 |
| **延迟** | 极低（共享内存） | 中等（需经过 HTTP） |
| **部署复杂度** | 无需端口暴露 | 需开 HTTP 端口 |
| **会话管理** | 无状态，每请求独立 | 有状态，支持 SSE 流 |
| **实现难度** | 简单（MCP SDK 原生支持） | 复杂（需自行处理 ASGI/WSGI 适配） |

**HomeMind 的选择**：
- `python -m main mcp` → **stdio 模式**，Claude Desktop 配置 `{"command": "python", "args": ["-m", "main", "mcp"]}`
- Web 集成 → **Flask REST 端点 `/mcp/call`**，将工具调用通过 JSON-RPC 风格透传到 handlers
- MCP Streamable HTTP Server（后台线程）→ 暂不启用，原因是 `StreamableHTTPServerTransport` 是 ASGI 接口，与 Flask WSGI 不兼容

**为什么不用 FastAPI 重写 Web 层？** 现有 Flask 代码量较大，重写成本高。Flask REST 端点已经能覆盖大多数集成需求。

---

## Client 实现：两种连接方式

### stdio Client vs SSE Client

```python
# stdio：启动子进程（适合本地 MCP Server，如天气服务）
await client.connect_stdio("python", ["/path/to/weather_server.py"])

# SSE：通过 HTTP SSE 连接远程服务（适合无进程管理的服务）
await client.connect_sse("http://192.168.1.100:8766/mcp/sse")
```

**选型依据**：
- **stdio**：`Popen` 启动子进程，进程生命周期等于 Client 生命周期，无需独立服务进程，适合本地工具
- **SSE**：远程服务已在运行，通过 HTTP 长连接接收推送事件，适合第三方 API 服务

### 全局 Client 管理

```python
_global_clients: Dict[str, MCPClient] = {}

def get_mcp_client(name: str = "default") -> MCPClient:
    if name not in _global_clients:
        _global_clients[name] = MCPClient()
    return _global_clients[name]
```

**为什么用全局字典而不是单例？** 支持连接多个不同的 MCP Server（如同时连接天气服务和日历服务），按名字隔离。

---

## 工具定义：JSON Schema vs 类型提示

### 对比

| 方式 | 优点 | 缺点 |
|------|------|------|
| JSON Schema（最终选择） | MCP SDK 原生支持，Claude/MCP 生态直接消费 | 字段冗长，维护成本高 |
| Python type hints → 自动生成 | 代码简洁 | 需额外解析逻辑，MCP 不直接支持 |
| Pydantic 模型 | 验证+生成两用 | 引入新依赖 |

**选择 JSON Schema 的原因**：MCP 协议本身要求 `inputSchema` 字段为 JSON Schema 格式。直接写 JSON Schema 避免了两层转换，且 Claude Desktop 的 MCP 插件原生理解 JSON Schema。

---

## 9 个工具的暴露策略

HomeMind 暴露的工具：

```
device_control    → DeviceController.execute()
trigger_scene    → SceneSwitcher.execute()
query_context    → device_ctrl.get_all_state() + session_store + preference_store
info_query       → InfoQuery.execute()
nl_to_scene_rule → NLToTAPConverter.convert()
kb_query         → KnowledgeBase.query()
kb_add           → KnowledgeBase.add()
rule_list        → TAPRuleStore.list_rules()
rule_toggle      → TAPRuleStore.toggle_rule()
```

**设计原则**：不暴露内部复杂对象，只暴露原子操作。Agent 实例通过 `register_agent_instance()` 注入 handlers，保证解耦。

---

## 错误处理：fallback 链

```
MCP 工具调用
  ↓ handler 存在？
  ├─ 否 → "Unknown tool"
  ↓ handler 执行成功？
  ├─ 异常 → "Error: {exc}" + logger.error
  ↓ 返回结果
      └─ TextContent(type="text", text=str(result))
```

---

## 面试追问

**Q: 如果 Claude Desktop 连接的 MCP Server 挂了，怎么感知？**

MCP 协议本身没有心跳机制。HomeMind 在 `connect_stdio` 中捕获 `Popen` 的异常；SSE 连接通过 `try/except` 包裹 `sse_client` 上下文。如果连接断开，`is_connected()` 返回 `False`，后续 `call_tool` 返回 `MCPToolResult(is_error=True)`。

**Q: 工具返回的数据量很大怎么办？**

目前 handlers 直接返回 `str(result)`，大对象会被序列化为 JSON 字符串。未来可在 handlers 层增加 `max_result_bytes` 截断，或在 MCP Client 层做 response streaming。

**Q: MCP 和现有 BSR/LSR/LLM 链路的关系是什么？**

MCP 是对外的能力暴露层，和内部推理链路正交。MCP 工具（device_control / trigger_scene）最终调用的是 `DeviceController` / `SceneSwitcher` —— 这些组件本身就是五层架构执行层的核心。MCP 只是给外部 AI（Claude）提供了一个调用它们的标准化接口。
