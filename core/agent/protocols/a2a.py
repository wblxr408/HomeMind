"""
Agent-to-Agent 协议实现

基于 Google/Linux Foundation A2A v1.0 (2026年3月)
MCP = Agent→Tool, A2A = Agent→Agent（互补关系）

A2A 核心概念：
- Agent Card: 暴露本 Agent 能力元数据
- Task: 有状态的工作单元
- Message: 对话消息（带 Parts 附件）
- SSE: 任务状态实时推送
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── A2A 类型定义 ─────────────────────────────────────────────────────────────

class TaskStatus(Enum):
    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


@dataclass
class AgentCard:
    """Agent 元数据卡片（A2A 规范）。"""
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: List[str] = field(default_factory=list)
    skills: List[Dict[str, str]] = field(default_factory=list)
    authentication: str = "none"  # none | jws | bearer
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": self.capabilities,
            "skills": self.skills,
            "authentication": self.authentication,
            "tags": self.tags,
        }


@dataclass
class MessagePart:
    """消息附件（A2A 规范）。"""
    kind: str = "text"  # text | image | audio | file
    text: str = ""
    mime_type: str = "text/plain"


@dataclass
class A2AMessage:
    """A2A 消息。"""
    role: str = "user"  # user | agent
    parts: List[MessagePart] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "role": self.role,
            "parts": [{"kind": p.kind, "text": p.text, "mimeType": p.mime_type} for p in self.parts],
            "metadata": self.metadata,
        }


@dataclass
class Task:
    """A2A 任务。"""
    id: str = ""
    status: TaskStatus = TaskStatus.SUBMITTED
    messages: List[A2AMessage] = field(default_factory=list)
    agent_name: str = ""
    created_at: str = ""
    updated_at: str = ""
    error: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = uuid.uuid4().hex
        if not self.created_at:
            self.created_at = datetime.now().astimezone().isoformat()
        self.updated_at = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "messages": [m.to_dict() for m in self.messages],
            "agentName": self.agent_name,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "error": self.error,
        }


# ── A2A Protocol 实现 ────────────────────────────────────────────────────────

class A2AProtocol:
    """
    A2A 协议客户端（用于调用其他 Agent）。

    与 MCP 的关系：
    - MCP: Agent → Tool（工具调用）
    - A2A: Agent → Agent（协作对话）
    两者互为补充，共同构成完整的多 Agent 架构。

    用法：
        a2a = A2AProtocol()
        card = a2a.discover_agent("http://192.168.1.102:8766/.well-known/agent.json")
        task_id = await a2a.send_message(card, query)
        status = await a2a.get_task_status(task_id)
    """

    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._local_cards: Dict[str, AgentCard] = {}

    def register_local_agent(self, card: AgentCard) -> None:
        """注册本机 Agent Card。"""
        self._local_cards[card.name] = card
        logger.info("Registered A2A agent: %s", card.name)

    def get_local_agents(self) -> List[AgentCard]:
        return list(self._local_cards.values())

    def get_agent_card(self, name: str) -> Optional[AgentCard]:
        return self._local_cards.get(name)

    async def discover_agent(self, url: str) -> Optional[AgentCard]:
        """从远端发现 Agent Card。"""
        try:
            import requests
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return AgentCard(**data)
        except Exception as exc:
            logger.warning("Agent discovery failed for %s: %s", url, exc)
            return None

    async def submit_task(
        self,
        agent_card: AgentCard,
        query: str,
        role: str = "user",
    ) -> str:
        """向远端 Agent 提交任务。"""
        task = Task(
            agent_name=agent_card.name,
            status=TaskStatus.SUBMITTED,
        )
        msg = A2AMessage(
            role=role,
            parts=[MessagePart(kind="text", text=query)],
        )
        task.messages.append(msg)
        self._tasks[task.id] = task

        try:
            import requests
            resp = requests.post(
                f"{agent_card.url}/a2a/tasks",
                json={"task": task.to_dict()},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            task_id = data.get("taskId", task.id)
            if task_id != task.id:
                self._tasks[task_id] = task
            return task_id
        except Exception as exc:
            task.status = TaskStatus.FAILED
            task.error = str(exc)
            logger.error("Task submission failed: %s", exc)
            return task.id

    async def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """查询任务状态。"""
        task = self._tasks.get(task_id)
        return task.status if task else None

    def get_task_result(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务结果。"""
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    def create_agent_card(
        self,
        name: str,
        description: str,
        url: str,
        capabilities: List[str],
        skills: Optional[List[Dict[str, str]]] = None,
    ) -> AgentCard:
        """创建标准化的 Agent Card。"""
        return AgentCard(
            name=name,
            description=description,
            url=url,
            version="1.0.0",
            capabilities=capabilities,
            skills=skills or [],
        )
