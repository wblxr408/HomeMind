# HomeMind 面试拷打文档索引

本目录包含 HomeMind `core/` 下每个模块的面试问答指南，帮助理解和准备相关技术面试。

所有文档采用**技术选型对比**格式：不讲基础设计，只讲为什么这么选、为什么不选别的、trade-off 是什么。

## 文档列表

### 核心主链路

| 文档 | 模块 | 说明 |
|------|------|------|
| [01-bsr-candidate-recall.md](./01-bsr-candidate-recall.md) | BSR 候选召回 | 规则/向量/历史三路召回，去重合并，ACTION_POOL |
| [02-lsr-precision-ranking.md](./02-lsr-precision-ranking.md) | LSR 精排 | 5维加权评分，显式上下文注入，多轮追踪 |
| [03-llm-decision.md](./03-llm-decision.md) | LLM 决策层 | plan_intent + decide_local/cloud，Mock 逻辑，后端降级链 |
| [04-dqn-policy.md](./04-dqn-policy.md) | DQN 主动策略 | Double DQN 网络，冷启动合成数据，Epsilon-Greedy |
| [05-command-validator.md](./05-command-validator.md) | 命令校验层 | 参数边界，风险等级，速率限制 |

### 安全与治理

| 文档 | 模块 | 说明 |
|------|------|------|
| [08-safety-detection.md](./08-safety-detection.md) | Safety 安全检测 | 门锁/安防/燃气阀关键词检测 |
| [13-injection-detector.md](./13-injection-detector.md) | 提示注入检测 | 角色劫持/系统指令/编码绕过 |
| [14-runtime-security.md](./14-runtime-security.md) | 运行时安全链 | 四层防御，Zero-Trust 身份，模拟模式 |
| [16-policy-governance.md](./16-policy-governance.md) | 策略引擎 | 组织级访问控制，allow/confirm/deny |

### 记忆与知识

| 文档 | 模块 | 说明 |
|------|------|------|
| [06-rag-knowledge-base.md](./06-rag-knowledge-base.md) | RAG 知识库 | 三层存储，冲突检测，信任体系 |
| [09-memory-stores.md](./09-memory-stores.md) | 记忆存储 | SessionStore 短期，PreferenceStore 长期偏好 |
| [10-privacy-redactor.md](./10-privacy-redactor.md) | 隐私过滤 | 云端上下文最小化，敏感信息脱敏 |

### 自动化与场景

| 文档 | 模块 | 说明 |
|------|------|------|
| [11-tap-engine.md](./11-tap-engine.md) | TAP 自动化引擎 | 时间/温度/湿度/场景触发器，冲突解决 |
| [12-nl-to-tap.md](./12-nl-to-tap.md) | NL→TAP 转换 | 自然语言转自动化规则 |
| [18-scene-store.md](./18-scene-store.md) | 场景存储 | 场景配置持久化，CRUD |
| [19-autonomy-manager.md](./19-autonomy-manager.md) | 渐进式自主权 | 5级自主权，升级/降级策略 |

### 语言与路由

| 文档 | 模块 | 说明 |
|------|------|------|
| [07-inference-router.md](./07-inference-router.md) | 推理路由 | classify_intent + decide_route，阈值设计 |
| [15-language-normalizer.md](./15-language-normalizer.md) | 语言规范化 | 中英文/方言归一化，置信度决策 |

### 系统总览

| 文档 | 模块 | 说明 |
|------|------|------|
| [17-system-architecture.md](./17-system-architecture.md) | 整体架构 | 完整架构图，请求流程，设计哲学，扩展指南 |
| [20-observability.md](./20-observability.md) | 可观测性 | Metrics / Alerter / Tracer，Prometheus 格式，告警策略 |

### 架构升级（MCP / 多Agent / 分布式）

| 文档 | 模块 | 说明 |
|------|------|------|
| [21-mcp-integration.md](./21-mcp-integration.md) | MCP 双向集成 | Server+Client，stdio/HTTP 双模式，9个工具暴露，Claude Desktop 集成 |
| [22-hierarchical-kv.md](./22-hierarchical-kv.md) | 分层 KV Store | L1热(dict)/L2温(SQLite)/L3冷(ChromaDB)，TTL+LRU 双驱逐，asyncio 锁 |
| [23-context-compressor.md](./23-context-compressor.md) | 上下文压缩 | Query-Conditioned 四层压缩，SoftSelector 语义评分，LLM 摘要 |
| [24-event-bus-multi-agent.md](./24-event-bus-multi-agent.md) | Event Bus + Multi-Agent | Pub/Sub，Coordinator+Specialist，asyncio 并行分发，fire-and-forget |
| [25-distributed-communication.md](./25-distributed-communication.md) | 分布式通信 | mDNS/DNS-SD 发现，WebSocket+HTTP Mesh 双传输，Store-and-Forward |
| [26-a2a-protocol.md](./26-a2a-protocol.md) | A2A 协议 | Agent Card，Task 状态机，Agent→Agent 协作（与 MCP 互补）|

---

## 推荐阅读顺序

1. 先读 [17-system-architecture.md](./17-system-architecture.md) 了解全貌
2. 按主链路顺序阅读 01-05
3. 根据面试岗位选择对应模块深入
4. 安全方向 → 08/13/14/16，记忆方向 → 06/09/10，自动化方向 → 11/12/18/19
5. 架构升级方向 → 21-26（全部，按顺序）

## 面试高频问题分类

### 系统设计类
- 为什么用 BSR→LSR→LLM 三阶段而非端到端？
- 本地优先的架构如何保证隐私？
- 多层安全防御如何协同工作？

### 算法类
- DQN vs Q-Learning 的区别？Double DQN 解决什么问题？
- LSR 的 5 个特征权重如何确定？
- 向量召回的 cosine threshold 为什么是 0.25？

### 工程类
- 各模块的降级策略是什么？
- 如何扩展支持新设备？
- 冷启动问题如何解决？

### 架构升级类（21-26）
- MCP vs 自定义 JSON-RPC vs OpenAI Tool Format：为什么选 MCP？
- stdio vs Streamable HTTP：MCP Server 两种传输方式的取舍
- 三层 KV 为什么不用 Redis/LMDB？
- SoftSelector 的 0.7/0.3 权重怎么来的？
- Event Bus 的 fire-and-forget 会不会丢消息？
- mDNS vs NDP vs 手动配置：服务发现的选型
- A2A 和 MCP 的边界在哪里？
