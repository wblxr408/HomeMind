# 可观测性 — 面试拷打指南

## 模块定位

可观测性子系统包含三个独立模块，提供完整的监控告警和全链路追踪能力：

```
Agent 执行
  ↓
AgentTracer.span("bsr") / .span("lsr") / .span("decision")
  ↓
AgentMetrics.record_llm_request() / .record_latency() / .record_error()
  ↓
Alerter.check() → [Alert] → 日志 / WebSocket
```

---

## AgentMetrics — 指标收集器

### 四大监控维度

| 维度 | 指标 | 类型 |
|------|------|------|
| LLM 经济性 | `llm_requests_local/cloud`, `llm_tokens_total` | Counter |
| 推理延迟 | p50 / p95 / p99 | Gauge（滚动窗口1000样本） |
| 消息管道 | `protocol_connected`, `mqtt_connected`, `queue_depth` | Gauge |
| 运营健康 | `memory_usage_percent`, `device_online_rate`, `rule_trigger_success_rate` | Gauge |

### Prometheus 文本格式输出

```python
def render_prometheus(self) -> str:
    # 输出 Prometheus scrape 格式
    lines = [
        "# HELP Homemind_agent_uptime_seconds 运行时间（秒）",
        "# TYPE Homemind_agent_uptime_seconds gauge",
        f"Homemind_agent_uptime_seconds {uptime}",
        ...
    ]
    return "\n".join(lines)
```

### 百分位计算

```python
def _percentile(self, data: list, p: float) -> float:
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100.0)
    return sorted_data[idx]
```

**注意**: 用了 `int(len * p / 100)` 而非插值法，在小样本时精度有限但实现简洁。

---

## Alerter — 告警管理器

### 告警触发条件

```python
memory_percent >= 80% → "critical"
memory_percent >= 70% → "warning"
p95_latency >= 8000ms → "warning"
cloud_ratio >= 30% → "warning"
protocol_disconnected → "warning"
```

### 告警去重

```python
# 只对新告警触发，避免重复通知
if key not in self._active_alerts:
    self._fire(alert)
    self._active_alerts[key] = alert
```

### 告警恢复

```python
# 指标恢复正常 → 自动清除活跃告警
for key in list(self._active_alerts.keys()):
    if key not in metric_alerts:
        del self._active_alerts[key]
```

### 输出通道

```python
# 1. 写日志 (critical → logger.critical, warning → logger.warning)
# 2. 写文件 (logs/alerts.log, JSON 格式)
# 3. WebSocket 推送 (注册回调，可扩展)
```

---

## AgentTracer — 全链路追踪

### OpenTelemetry 风格设计

```python
tracer = AgentTracer()
with tracer.trace("process"):
    with tracer.span("bsr"):
        candidates = bsr.recall(...)
        tracer.span_set("candidate_count", len(candidates))
    with tracer.span("lsr"):
        ranked = lsr.rank(...)
```

### Span 树结构

```python
@dataclass
class Span:
    name: str
    span_id: str       # uuid[:16]
    parent_id: str      # 父 span id
    trace_id: str       # 根 span id
    start_time: float
    end_time: float
    duration_ms: float
    attributes: Dict    # 自定义属性
    status: str         # OK | ERROR
    children: List[Span]
```

### ContextVar 实现线程隔离

```python
_tracer_context: ContextVar[Optional["_TracerContext"]] = ContextVar(
    "tracer_context", default=None
)

def start_trace(self, operation_name):
    ctx = _TracerContext()
    _tracer_context.set(ctx)  # 线程局部存储
    ctx.start_span(operation_name)
```

**为什么用 ContextVar 而非 threading.local？**
- ContextVar 支持**异步上下文**（asyncio）
- 每个协程可有独立追踪上下文
- 比 threading.local 更适合异步应用

### NoOp 模式

```python
class _NoOpSpan:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def __setattr__(self, name, value): pass  # 静默丢弃所有属性

# enabled=False 时所有追踪操作无开销
def span(self, name, **attributes):
    if not self.enabled:
        return _NoOpSpan()
```

---

## 面试核心问题

### 1. 为什么延迟用滚动窗口而非无限累积？
- 内存有限，滚动窗口 `maxlen=1000` 足够统计近期延迟
- 太久远的延迟数据对实时监控无意义
- 防止内存泄漏

### 2. Prometheus 文本格式的优势？
- 无需安装客户端库，curl 即可拉取
- 兼容 Prometheus / Grafana / Datadog 等所有主流监控平台
- 简单够用，避免依赖重量级 SDK

### 3. 为什么告警用 JSON 而非结构化日志？
- JSON 可被日志收集器（ELK/Loki）解析
- 包含完整字段（level/message/timestamp/metric/value/threshold）
- 比纯文本日志更易查询和聚合

### 4. 告警去重策略的实现细节？
```python
# 指标恢复正常后清除
if key not in metric_alerts:
    del self._active_alerts[key]
```
- 同一告警只触发一次
- 恢复后不再告警
- 再次触发会重新触发（正确行为）

### 5. 追踪数据的保留策略？
```python
retention_days = OBSERVABILITY_CONFIG["trace_retention_days"]  # 默认 7 天
cutoff = time.time() - retention_days * 86400
for path in traces_dir.glob("trace_*.jsonl"):
    if path.stat().st_mtime < cutoff:
        path.unlink()
```
- 按时间清理，而非按大小
- 7 天足够调试和问题追溯

### 6. 指标收集的线程安全？
```python
with self._lock:  # threading.Lock
    self.llm_requests_local += 1
    self._latencies.append(latency_ms)
```
- 所有写操作加锁
- 读操作（snapshot/render_prometheus）也加锁，保证一致性

### 7. 追踪和指标的区别？
| | 追踪 (Tracing) | 指标 (Metrics) |
|---|---|---|
| 粒度 | 每个请求 | 聚合统计 |
| 用途 | 调试/根因分析 | 监控/告警 |
| 数据量 | 大（每个请求一条） | 小（几个数字） |
| 保留 | 7天 | 永久 |
