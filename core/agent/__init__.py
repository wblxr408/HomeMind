"""
Agent 协作模块 — 面试问答索引

├── bus.py       — Event Bus (进程内 Pub/Sub)
├── base.py      — Agent 基类
├── coordinator.py — 主协调 Agent
├── specialist.py — 专业 Agent (Device/Scene/Memory/Info)
└── protocols/
    ├── a2a.py   — A2A v1.0 协议实现
    └── admin.py — Agent Card 管理

A2A 协议要点：
- 与 MCP 互补：MCP=Agent→Tool，A2A=Agent→Agent
- Agent Card 暴露能力元数据
- Task 有状态，支持 SSE 推送
- 2026年3月 Linux Foundation 发布 v1.0
"""

from core.agent.bus import EventBus, EventType, Event, get_event_bus
from core.agent.base import AgentRole, AgentResponse, BaseAgent
from core.agent.coordinator import CoordinatorAgent
from core.agent.protocols.a2a import A2AProtocol, AgentCard, Task, TaskStatus
from core.agent.protocols.admin import AgentCardRegistry, get_card_registry

__all__ = [
    "EventBus",
    "EventType",
    "Event",
    "get_event_bus",
    "AgentRole",
    "AgentResponse",
    "BaseAgent",
    "CoordinatorAgent",
    "A2AProtocol",
    "AgentCard",
    "Task",
    "TaskStatus",
    "AgentCardRegistry",
    "get_card_registry",
]
