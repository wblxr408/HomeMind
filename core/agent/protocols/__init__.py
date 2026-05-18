"""Agent Card 协议子模块。"""
from core.agent.protocols.a2a import A2AProtocol, AgentCard, Task, TaskStatus
from core.agent.protocols.admin import AgentCardRegistry, get_card_registry

__all__ = ["A2AProtocol", "AgentCard", "Task", "TaskStatus", "AgentCardRegistry", "get_card_registry"]
