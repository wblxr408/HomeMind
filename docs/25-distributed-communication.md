# 分布式通信 — 面试拷打指南

## 核心问题：为什么端侧设备需要分布式通信？

**场景**：HomeMind 可能部署在多个设备上（手机、树莓派、边缘网关）。这些设备需要：
1. 互相发现（不用手动配置 IP）
2. 同步设备状态（手机开的灯，树莓派上也要知道）
3. 消息可靠传递（即使对方暂时离线）

---

## 服务发现：为什么选 mDNS/DNS-SD 而不是手动配置/NDP？

### 方案对比

| 维度 | 手动配置 IP | NDP（邻居发现） | **mDNS/DNS-SD（最终）** |
|------|-------------|----------------|------------------------|
| **配置成本** | 高（每设备需配置） | 无 | **零配置** |
| **跨网段** | 支持 | 仅本地链路上 | 本地网络 |
| **生态支持** | 通用 | IPv6 原生 | macOS/iOS/Android/路由器原生支持 |
| **库支持** | socket | Linux netlink | `zeroconf` Python 包 |
| **服务元数据** | 无 | 无 | **TXT 记录携带 capabilities** |

**选择 mDNS/DNS-SD 的原因**：
- 智能家居设备通常是局域网内，mDNS 完美覆盖
- TXT 记录可以携带 `node_id`、`capabilities`、`version`，Discovery 时直接拿到元数据
- zeroconf 库封装了底层复杂性，50 行代码实现完整服务发现
- 已有 Apple Bonjour / Android NSD 等原生支持

**为什么不选 NDP？** NDP 只发现邻居，不携带服务元数据（capabilities）。HomeMind 需要知道对方是"边缘网关"还是"手机"，mDNS 的 TXT 记录天然支持这一点。

---

## 为什么 `_homemind._tcp.local.` 这个服务类型？

mDNS 服务类型格式：`_服务名._传输协议.`

- `_homemind` → HomeMind 专属服务，不与其他服务冲突
- `_tcp` → 使用 TCP 传输（Mesh Transport 用 WebSocket/TCP）
- `local.` → mDNS 本地广播域名

**为什么不直接用 `_http._tcp`？** 那会与所有 HTTP 服务冲突，无法区分 HomeMind 节点和其他 HTTP 服务。

---

## 发现回退策略：HTTP well-known

```python
async def _start_fallback(self) -> bool:
    """当 zeroconf 不可用时，扫描 HTTP well-known 端点。"""
    # 扫描 192.168.x.1-254 的 /.well-known/homemind-agent
    pass
```

**为什么需要回退？** Windows 默认不启用 mDNS 服务（macOS/iOS/Android 原生支持）。HTTP 回退通过扫描内网常见网段（192.168.x.x）探测同网段 HomeMind 节点，实现降级可用。

**为什么不用 UPnP/SSDP？** SSDP（简单服务发现协议）广播格式与 mDNS 类似，但 TXT metadata 支持不如 mDNS 灵活。优先实现 mDNS，SSDP 作为备选。

---

## MeshTransport：为什么需要多种传输方式？

### 对比

| 维度 | HTTP 轮询 | WebSocket | **双协议（最终）** |
|------|-----------|-----------|------------------|
| **实时性** | 低（秒级延迟） | 高（毫秒级） | 高 |
| **连接开销** | 每请求建连 | 长连接 | 长连接优先 |
| **断线重连** | 每次重试 | 需实现 | 需实现 |
| **后端支持** | 所有服务器 | 需要 WS 支持 | **WS优先，HTTP回退** |
| **推送能力** | 无（轮询） | 双向 | 双向 |

**MeshTransport 的策略**：
1. 优先 WebSocket 连接（实时双向）
2. WebSocket 不可用时降级 HTTP
3. 离线消息通过 Store-and-Forward 暂存，上线后投递

---

## Store-and-Forward：为什么用 SQLite？

```python
async def relay_to(self, target_node_id: str, message: MeshMessage) -> bool:
    """发送消息，若对方离线则暂存。"""
    peer = self._peers.get(target_node_id)
    if peer and peer._connected:
        return await peer.send(message)
    else:
        await self._store_for_retry(target_node_id, message)
        return True  # 离线也返回成功
```

### 对比

| 方案 | 可靠性 | 实现复杂度 | 持久化 |
|------|--------|-----------|--------|
| 内存暂存（进程重启丢失） | 低 | 低 | 无 |
| **SQLite 持久化（最终选择）** | 中 | 中 | **有** |
| 消息队列（RabbitMQ/Kafka） | 高 | 高 | 有 |
| 云存储（S3/DynamoDB） | 高 | 高 | 有 |

**选择 SQLite 的原因**：
- HomeMind 是端侧优先，RabbitMQ/Kafka 太重
- SQLite 零配置、单文件、持久化，进程重启后 relay 队列不丢失
- 已有 SessionStore / HierarchicalKV 使用 SQLite，技术栈统一
- `aiosqlite` 支持异步操作

---

## PeerConnection 的生命周期

```
MeshTransport.connect_peer(peer)
  └─ 建立 WebSocket 或 HTTP 连接
  └─ PeerConnection._connected = True
  └─ 注册到 self._peers[node_id]

MeshTransport.disconnect_peer(node_id)
  └─ 关闭 WebSocket
  └─ PeerConnection._connected = False
  └─ 从 self._peers 移除（或保留元数据用于 relay）

消息 relay
  ├─ peer._connected = True → 直接发送
  └─ peer._connected = False → Store-and-Forward
```

**为什么 disconnect 时不立即从 `_peers` 删除？** 因为 Mesh Transport 需要保留离线节点信息，用于后续的 Store-and-Forward。删除发生在节点彻底消失（mDNS 广播消失）时。

---

## 与 Event Bus 的关系

```
Event Bus (进程内)
  └─ 发布 USER_QUERY / DEVICE_STATE_CHANGE 等事件
  └─ 仅在单个进程内传播

Mesh Transport (进程间)
  └─ 发布 PEER_MESSAGE 类型事件
  └─ 通过 WebSocket/relay 跨进程传播
```

**为什么不用 Event Bus 直接跨进程？** Event Bus 是进程内的 Python `asyncio` 机制，跨进程需要序列化/反序列化、socket 传输。Mesh Transport 专门处理这些。

**桥接设计**：当 Event Bus 收到 `PEER_MESSAGE` 类型事件时，MeshTransport 负责将其序列化并发送到远端节点。远端节点收到后反序列化，再发布到本地 Event Bus。

---

## 面试追问

**Q: mDNS 在大户型/别墅的多网段环境下怎么工作？**

mDNS 默认只在单播域内有效，跨网段需要 mDNS Reflector（通常由路由器提供）。HomeMind 当前面向单网段家庭网络，多网段场景需要手动配置路由或通过 Mesh Transport 的 HTTP 回退扫描。

**Q: WebSocket 连接断开后如何感知？**

WebSocket 协议本身有心跳（ping/pong）机制。MeshTransport 通过捕获 WebSocket 的 `close` 事件更新 `PeerConnection._connected` 状态。心跳间隔由 `PingInterval` 控制，默认 30 秒。

**Q: Store-and-Forward 队列无限增长怎么办？**

当前设计没有容量限制。生产环境应添加：
- 队列条数上限（超出则丢弃最旧消息）
- 消息 TTL（如 24 小时内未投递则丢弃）
- 节点重连后批量 flush

**Q: 局域网安全怎么保证？**

HomeMind 的 Mesh Transport 目前没有内置加密，所有消息明文传输。生产环境应添加：
- TLS 双向认证（mesh_nodes 使用预共享证书）
- 消息签名（HMAC）验证来源
- mDNS 服务注册时的 JWS 签名验证

当前版本聚焦于协议和架构完整性，安全加固作为后续迭代项。
