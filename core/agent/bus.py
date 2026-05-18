"""
Event Bus — 进程内异步 Pub/Sub 消息总线

用于 HomeMind 多 Agent 之间的进程内通信。
支持：
- 类型安全的 event 订阅
- 异步 handler 执行（fire-and-forget）
- handler 错误隔离（单个 handler 失败不影响其他 handler）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventType(Enum):
    USER_QUERY = "user_query"
    DEVICE_STATE_CHANGE = "device_state_change"
    SCENE_ACTIVATED = "scene_activated"
    SCENE_DEACTIVATED = "scene_deactivated"
    AGENT_HANDOVER = "agent_handover"
    CONTEXT_UPDATED = "context_updated"
    PEER_MESSAGE = "peer_message"
    RULE_TRIGGERED = "rule_triggered"
    ALERT = "alert"


@dataclass
class Event:
    """事件载体。"""
    type: EventType
    source: str
    payload: Dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().astimezone().isoformat()
        if not self.trace_id:
            self.trace_id = uuid.uuid4().hex[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value,
            "source": self.source,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "timestamp": self.timestamp,
        }


class EventBus:
    """
    进程内异步 Pub/Sub 消息总线。

    用法：
        bus = EventBus()

        # 订阅
        def on_device_change(event: Event):
            print(f"Device changed: {event.payload}")

        bus.subscribe(EventType.DEVICE_STATE_CHANGE, on_device_change)

        # 发布
        await bus.publish(Event(
            type=EventType.DEVICE_STATE_CHANGE,
            source="DeviceAgent",
            payload={"device": "空调", "status": "on"},
        ))

    特性：
    - 单例模式：通过 get_event_bus() 获取全局实例
    - 线程安全：asyncio.Lock 保护订阅表
    - 错误隔离：单个 handler 异常不影响其他 handler
    - 异步执行：handler 以 asyncio.create_task 异步运行
    """

    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._lock = asyncio.Lock()
        self._event_history: List[Event] = []
        self._max_history = 100

    async def publish(self, event: Event) -> None:
        """发布事件，所有订阅的 handler 异步执行。"""
        async with self._lock:
            handlers = list(self._subscribers.get(event.type, []))
            # 记录历史
            self._event_history.append(event)
            if len(self._event_history) > self._max_history:
                self._event_history = self._event_history[-self._max_history:]

        # 异步执行 handler（不在 lock 内）
        for handler in handlers:
            asyncio.create_task(self._safe_handler(handler, event))

    async def _safe_handler(self, handler: Callable, event: Event) -> None:
        """执行 handler，异常不向上传播。"""
        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(event)
            else:
                handler(event)
        except Exception as exc:
            logger.warning(
                "Event handler %s failed for event %s: %s",
                handler.__name__ if hasattr(handler, "__name__") else str(handler),
                event.type.value,
                exc,
            )

    def subscribe(self, event_type: EventType, handler: Callable) -> None:
        """订阅事件类型。"""
        self._subscribers.setdefault(event_type, []).append(handler)
        logger.debug("Subscribed %s to %s", handler, event_type.value)

    def unsubscribe(self, event_type: EventType, handler: Callable) -> bool:
        """取消订阅。"""
        handlers = self._subscribers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)
            return True
        return False

    def unsubscribe_all(self, event_type: EventType) -> None:
        """取消某类型的所有订阅。"""
        self._subscribers[event_type] = []

    def get_history(self, event_type: Optional[EventType] = None, limit: int = 20) -> List[Event]:
        """返回事件历史。"""
        if event_type:
            return [e for e in self._event_history[-limit:] if e.type == event_type]
        return self._event_history[-limit:]

    def get_subscriber_count(self, event_type: EventType) -> int:
        return len(self._subscribers.get(event_type, []))


# ── 全局单例 ────────────────────────────────────────────────────────────────

_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
