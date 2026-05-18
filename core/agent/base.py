"""
Agent 基类 — 定义专业 Agent 的标准接口

所有 SpecialistAgent 必须实现此接口。
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentRole(Enum):
    COORDINATOR = "coordinator"
    DEVICE = "device"
    SCENE = "scene"
    MEMORY = "memory"
    COMM = "comm"
    INFO = "info"


@dataclass
class AgentResponse:
    """Agent 返回结果。"""
    success: bool
    content: Any = None
    error: str = ""
    agent_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "content": self.content,
            "error": self.error,
            "agent_name": self.agent_name,
            "metadata": self.metadata,
        }


class BaseAgent(ABC):
    """
    Agent 基类。

    所有 HomeMind Agent 必须实现：
    - name: Agent 名称
    - role: Agent 角色
    - handle(query, context): 处理查询
    """

    def __init__(self, name: str, role: AgentRole):
        self.name = name
        self.role = role
        self._bus = None  # EventBus 引用，由 Coordinator 注入

    def attach_bus(self, bus: Any) -> None:
        """注入 EventBus 引用。"""
        self._bus = bus

    @abstractmethod
    async def handle(self, query: str, context: Any) -> AgentResponse:
        """
        处理用户查询或系统事件。
        子类实现具体逻辑。
        """
        ...

    async def on_event(self, event: Any) -> None:
        """处理 EventBus 事件。可被子类覆盖。"""
        pass

    def health_check(self) -> Dict[str, Any]:
        """健康检查。"""
        return {
            "name": self.name,
            "role": self.role.value,
            "healthy": True,
        }

    def get_capabilities(self) -> List[str]:
        """返回本 Agent 支持的能力列表。子类可覆盖。"""
        return []

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name={self.name!r}, role={self.role.value})>"
