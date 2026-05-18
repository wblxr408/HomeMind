# Hierarchical KV — 面试拷打指南

## 核心问题：为什么需要分层 KV？

**场景**：HomeMind 需要在端侧存储 session context、preference、device state。数据访问模式差异巨大：

- 设备状态 → 每秒访问数十次，只需要最近几秒
- 用户偏好 → 几分钟访问一次，需要长期保留
- 历史记录 → 偶尔查询，需要归档

用一个 Redis？端侧设备资源有限，Redis 太重。
用一个 dict？数据量大了 O(n) 扫描，且进程重启即丢失。
用 SQLite 单表？所有访问都是随机 I/O，没有热点感知。

---

## 技术选型：分层存储架构

### 方案对比

| 维度 | 单层 dict | SQLite 单库 | Redis Cluster | **三层 KV（最终选择）** |
|------|-----------|-------------|--------------|------------------------|
| **访问延迟** | O(1) 极低 | O(log n) 中等 | O(1) 低 | O(1)（L1命中）|
| **持久化** | 无 | 有 | 可配置 | L2 有，L1 无 |
| **内存占用** | 无上限 | 磁盘 | 内存+磁盘 | 分层控制 |
| **TTL 支持** | 手动 | 需额外逻辑 | 原生 | 原生（每层独立）|
| **冷热感知** | 无 | 无 | 部分（LFU）| **LRU 淘汰+TTL** |
| **端侧友好** | 最轻 | 轻量 | 过重 | 轻量（SQLite）|
| **实现复杂度** | 极低 | 低 | 高 | 中等 |

**选型理由**：
- L1 热层用 Python dict，O(1) 极低延迟，满足高频访问
- L2 温层用 SQLite，单文件、零配置、内置索引，满足持久化需求
- L3 冷层预留 ChromaDB 接口，架构可扩展
- TTL + LRU 组合确保热数据留热层，冷数据自动降级

---

## 为什么 L1 用 dict 而不是 LRUCache？

### 对比

| 维度 | `cachetools.LRUCache` | **Python dict + 自管理 LRU** |
|------|----------------------|------------------------------|
| **TTL 支持** | 无（仅 LRU） | 有（TTL + LRU 双维度）|
| **字节统计** | 按条目数 | 按实际字节大小 |
| **LRU 实现** | C 实现快 | Python 实现（稍慢但够用）|
| **定制灵活度** | 低 | 高（可改写 `_evict_lru_l1`）|
| **额外依赖** | cachetools | 无 |

**选择自管理 dict 的原因**：HomeMind 的 L1 有字节上限（200KB），需要按 `len(json.dumps(value))` 做容量控制，而 LRUCache 按条目数计数。额外依赖 cachetools 增加包体积，且无法同时满足 TTL + 字节容量双重约束。

---

## 为什么 L2 用 SQLite 而不是 LMDB/LevelDB？

### 对比

| 维度 | LevelDB | LMDB | **SQLite（最终选择）** |
|------|---------|------|----------------------|
| **写入模式** | 追加写，压缩回收 | 写时复制 | 覆盖写，事务支持 |
| **并发支持** | 单写多读 | 多读单写 | 多连接并发 |
| **Python 支持** | 需要 RocksDB binding | 需要 lmdb 包 | 原生内置 |
| **SQL 查询** | 不支持 | 不支持 | 支持（未来可扩展）|
| **磁盘占用** | 紧凑 | 紧凑 | 相对大 |
| **端侧友好** | 需编译 | 需编译 | 零依赖 |

**选型理由**：
- HomeMind 已使用 SQLite（SessionStore / AuditLog），复用同一技术栈
- SQLite 的 WAL 模式提供不错的并发性能
- `aiosqlite` 满足异步 I/O 需求
- 未来可能需要按时间范围查询（L2 TTL 清理），SQL WHERE 子句比 KV 迭代更高效

---

## 读写路径设计

### 读取路径（按热度逐层查找）

```
get(key)
  ├─ L1 hit? 且 TTL 未过期 → 返回，touch() 更新 last_access
  ├─ L1 miss 或 TTL 过期
  │   └─ 删除过期条目，size_bytes -= entry.size
  │       L2 hit? 且 TTL 未过期 → promote(L2→L1)，返回
  │           L2 miss
  │               L3 hit? → promote(L3→L1)，返回
  │               L3 miss → return default
```

**为什么 promote 后要从源层删除？** 避免同一数据在多层同时存在，导致更新不一致。

### 写入路径（始终写 L1）

```
set(key, value)
  ├─ 估算 value 的 JSON 字节大小
  ├─ 替换已有 key → 先释放旧条目的 size_bytes
  ├─ 写入 L1，size_bytes += size
  └─ L1 超限？→ _evict_lru_l1()
      └─ 找到最久未访问的条目
      └─ 从 L1 移除
      └─ 同步写入 L2（INSERT OR REPLACE）
```

**为什么 eviction 时写 L2 是同步的？** 因为 `_evict_lru_l1` 在 `asyncio.Lock` 锁内调用，不能 await。SQLite 写操作极快（毫秒级），阻塞时间可接受。

---

## TTL 驱逐 vs LRU 驱逐：两种淘汰策略

### 对比

| 策略 | TTL 驱逐 | LRU 驱逐 |
|------|----------|----------|
| **触发条件** | 时间到期 | 容量超限 |
| **淘汰顺序** | 按时间，不区分热度 | 按访问频率，保留热点 |
| **适用场景** | 临时缓存（session） | 容量受限缓存（设备状态）|
| **HomeMind 用法** | 后台 `_cleanup_expired()` 定期清理 | `_evict_lru_l1()` L1 超限时触发 |

**HomeMind 的设计**：两者结合。TTL 作为时间维度淘汰，LRU 作为容量维度淘汰。热层数据即使未过期，如果容量紧张也会被 LRU 淘汰到温层。

---

## 为什么用 `asyncio.Lock` 而不是 `threading.Lock`？

```python
self._lock = asyncio.Lock()  # 异步锁

async def get(self, key: str, default: Any = None) -> Any:
    async with self._lock:  # 在 async 上下文中使用
        ...
```

**选型理由**：
- HomeMind 核心推理链路是异步的（`async def handle`）
- `asyncio.Lock` 在 async/await 上下文中非阻塞等待，`threading.Lock` 会阻塞整个事件循环
- 异步锁确保并发读写时 L1 数据结构（dict）的线程安全

---

## 字节容量限制的精妙之处

```python
L1_MAX_BYTES: int = 200 * 1024  # 200KB

def _estimate_size(self, value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode())
```

**为什么不用条目数限制？** 因为 context 数据大小差异极大：

- `"temperature": 26` → 20 字节
- 完整 session history → 50KB

按条目数限制会导致大对象挤占多个小对象的空间。按字节限制更公平。

**为什么 L2 的容量用 10MB 而不是更大？** 端侧树莓派存储有限，10MB 足够容纳数十个 session + preference 的温存储。

---

## 面试追问

**Q: L1 满了直接淘汰到 L2，但 L2 也满了怎么办？**

目前 L2 没有容量限制，只有 TTL 淘汰（1小时）。超长 TTL 的数据由后台清理任务删除。如果 L2 文件无限增长，可在 L2 驱逐时继续往 L3（ChromaDB）降级，当前架构已预留接口。

**Q: 多进程场景下 dict 和 SQLite 如何保证一致性？**

当前设计是单进程模型。L1 dict 完全在内存中，多进程共享需要通过 IPC（如 Redis）。L2 SQLite 支持多进程并发读写（WAL 模式），但同一时刻只有一个 writer。

**Q: asyncio 事件循环在多线程中运行会怎样？**

`HierarchicalKV` 在多线程中调用时，`asyncio.Lock` 需要事件循环上下文。`keys()` 方法中做了保护：如果当前线程没有运行事件循环，就用 `ThreadPoolExecutor` 包装 `asyncio.run()`。

**Q: 200KB L1 够用吗？**

对于 HomeMind 的 session context（通常几百字节到几KB），200KB 可容纳数十到数百个条目，足够日常使用。如果未来需要更大 L1，可通过 `L1_MAX_BYTES` 配置参数调整。
