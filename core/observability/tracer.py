"""OpenTelemetry 全链路追踪。

为 Agent 推理流程的每个节点建立 span，
支持本地 JSON 文件导出和 OTLP 网络推送。
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

from core.config import OBSERVABILITY_CONFIG, STORAGE_CONFIG

logger = logging.getLogger(__name__)

_tracer_context: ContextVar[Optional["_TracerContext"]] = ContextVar(
    "tracer_context", default=None
)


@dataclass
class Span:
    """单个追踪跨度。"""

    name: str
    span_id: str = ""
    parent_id: str = ""
    trace_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    attributes: Dict[str, Any] = field(default_factory=dict)
    status: str = "OK"  # OK | ERROR
    error_message: str = ""
    children: List["Span"] = field(default_factory=list)

    def __post_init__(self):
        if not self.span_id:
            self.span_id = uuid.uuid4().hex[:16]
        if self.start_time and self.end_time:
            self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)

    def to_dict(self) -> Dict:
        return {
            "name": self.name,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "trace_id": self.trace_id,
            "start_time": datetime.fromtimestamp(self.start_time).isoformat(),
            "end_time": datetime.fromtimestamp(self.end_time).isoformat(),
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
            "status": self.status,
            "error_message": self.error_message,
            "children": [c.to_dict() for c in self.children],
        }


class _TracerContext:
    """追踪上下文，管理当前请求的 span 树。"""

    def __init__(self, trace_id: str = ""):
        self.trace_id = trace_id or uuid.uuid4().hex
        self.root_span: Optional[Span] = None
        self._span_stack: List[Span] = []

    def start_span(self, name: str, attributes: Dict[str, Any] = None) -> Span:
        parent_id = ""
        if self._span_stack:
            parent_id = self._span_stack[-1].span_id
        span = Span(
            name=name,
            trace_id=self.trace_id,
            parent_id=parent_id,
            start_time=time.time(),
            attributes=dict(attributes or {}),
        )
        if not self.root_span:
            self.root_span = span
        if self._span_stack:
            self._span_stack[-1].children.append(span)
        self._span_stack.append(span)
        return span

    def end_span(self, span: Span, status: str = "OK", error: str = ""):
        span.end_time = time.time()
        span.duration_ms = round((span.end_time - span.start_time) * 1000, 2)
        span.status = status
        span.error_message = error
        if self._span_stack and self._span_stack[-1] is span:
            self._span_stack.pop()

    def finish(self) -> Dict:
        if self.root_span and self._span_stack:
            for span in reversed(self._span_stack):
                self.end_span(span)
        return self.root_span.to_dict() if self.root_span else {}


class AgentTracer:
    """Agent 全链路追踪器。

    用法:
        tracer = AgentTracer()
        with tracer.trace("process"):
            with tracer.span("bsr"):
                candidates = bsr.recall(...)
                tracer.span_set("candidate_count", len(candidates))
            with tracer.span("lsr"):
                ranked = lsr.rank(...)
    """

    def __init__(self, enabled: bool = None):
        cfg = OBSERVABILITY_CONFIG
        self.enabled = enabled if enabled is not None else cfg["enabled"]
        self._log_traces = cfg["log_traces"]
        traces_dir = Path(STORAGE_CONFIG["traces_dir"])
        self._traces_dir = Path(os.environ.get("HOMEMIND_TRACES_DIR", traces_dir))
        self._lock = threading.Lock()

    def start_trace(self, operation_name: str = "agent_run") -> str:
        """开始一个新追踪，返回 trace_id。"""
        if not self.enabled:
            return ""
        ctx = _TracerContext()
        _tracer_context.set(ctx)
        ctx.start_span(operation_name)
        return ctx.trace_id

    def span(self, name: str, **attributes) -> "_SpanContext":
        """在当前追踪中创建子 span。"""
        if not self.enabled:
            return _NoOpSpan()
        ctx = _tracer_context.get()
        if ctx is None:
            return _NoOpSpan()
        return _SpanContext(ctx, name, attributes)

    def trace(self, name: str, **attributes) -> "_TraceContext":
        """完整的 trace 上下文（span + 自动结束）。"""
        if not self.enabled:
            return _NoOpTrace()
        return _TraceContext(self, name, attributes)

    def span_set(self, key: str, value: Any) -> None:
        """在当前活跃 span 上设置属性。"""
        if not self.enabled:
            return
        ctx = _tracer_context.get()
        if ctx and ctx._span_stack:
            ctx._span_stack[-1].attributes[key] = value

    def span_add_event(self, name: str, **attributes) -> None:
        """在当前活跃 span 上添加事件。"""
        if not self.enabled:
            return
        ctx = _tracer_context.get()
        if ctx and ctx._span_stack:
            events = ctx._span_stack[-1].attributes.setdefault("_events", [])
            events.append({"name": name, "timestamp": time.time(), **attributes})

    def finish_trace(self, trace_id: str = "") -> Dict:
        """结束追踪并写文件。"""
        if not self.enabled:
            return {}
        ctx = _tracer_context.get()
        _tracer_context.set(None)
        if ctx is None:
            return {}
        result = ctx.finish()
        self._write_trace(result)
        return result

    def _write_trace(self, trace: Dict) -> None:
        if not trace or not self._log_traces:
            return
        try:
            self._traces_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = self._traces_dir / f"trace_{ts}.jsonl"
            with self._lock:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(trace, ensure_ascii=False) + "\n")
            self._prune_old_traces()
            logger.debug("Trace written: %s", path)
        except Exception as exc:
            logger.warning("Failed to write trace: %s", exc)

    def _prune_old_traces(self) -> None:
        retention_days = OBSERVABILITY_CONFIG["trace_retention_days"]
        cutoff = time.time() - retention_days * 86400
        try:
            for path in self._traces_dir.glob("trace_*.jsonl"):
                if path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Trace pruning failed: %s", exc)


class _SpanContext:
    """span() 返回的上下文管理器。"""

    def __init__(self, ctx: _TracerContext, name: str, attrs: Dict):
        self._ctx = ctx
        self._name = name
        self._attrs = attrs
        self._span: Optional[Span] = None

    def __enter__(self) -> Span:
        self._span = self._ctx.start_span(self._name, self._attrs)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb):
        status = "ERROR" if exc_type else "OK"
        error = str(exc_val) if exc_type else ""
        self._ctx.end_span(self._span, status=status, error=error)


class _TraceContext:
    """trace() 返回的上下文管理器。"""

    def __init__(self, tracer: AgentTracer, name: str, attrs: Dict):
        self._tracer = tracer
        self._name = name
        self._attrs = attrs
        self._trace_id = ""

    def __enter__(self):
        self._trace_id = self._tracer.start_trace(self._name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._tracer.finish_trace(self._trace_id)


class _NoOpSpan:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def __setattr__(self, name, value):
        pass


class _NoOpTrace:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# 全局追踪器单例
_tracer: Optional[AgentTracer] = None


def get_tracer() -> AgentTracer:
    global _tracer
    if _tracer is None:
        _tracer = AgentTracer()
    return _tracer
