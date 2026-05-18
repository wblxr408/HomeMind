"""
Coordinator — 主协调 Agent

使用现有 InferenceRouter 进行意图分类路由，
并行分发给多个 SpecialistAgent，聚合结果返回。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.agent.base import AgentResponse, AgentRole, BaseAgent
from core.agent.bus import Event, EventBus, EventType, get_event_bus

logger = logging.getLogger(__name__)


class CoordinatorAgent:
    """
    主协调 Agent。

    负责：
    1. 接收用户查询
    2. 调用 InferenceRouter 判断路由方向
    3. 并行分发给相关的 SpecialistAgent
    4. 聚合多个 Agent 的响应
    5. 产生最终决策

    用法：
        coordinator = CoordinatorAgent(
            specialists=[DeviceAgent(), SceneAgent(), MemoryAgent()],
            router=InferenceRouter(),
        )
        result = await coordinator.handle("打开空调", context)
    """

    def __init__(
        self,
        specialists: Optional[List[BaseAgent]] = None,
        router: Optional[Any] = None,
        event_bus: Optional[EventBus] = None,
    ):
        from core.router import InferenceRouter

        self.router = router or InferenceRouter()
        self.bus = event_bus or get_event_bus()
        self.name = "Coordinator"

        # 注册 specialists
        self.specialists: Dict[str, BaseAgent] = {}
        if specialists:
            for spec in specialists:
                self.specialists[spec.role.value] = spec
                spec.attach_bus(self.bus)

    def register_specialist(self, agent: BaseAgent) -> None:
        """注册专业 Agent。"""
        self.specialists[agent.role.value] = agent
        agent.attach_bus(self.bus)
        logger.info("Registered specialist: %s (%s)", agent.name, agent.role.value)

    def get_specialist(self, role: str) -> Optional[BaseAgent]:
        return self.specialists.get(role)

    def _get_relevant_specialists(self, route: str) -> List[BaseAgent]:
        """根据路由方向确定需要参与的专业 Agent。"""
        route_to_role = {
            "local": [AgentRole.DEVICE.value, AgentRole.SCENE.value],
            "cloud": [AgentRole.MEMORY.value, AgentRole.INFO.value],
            "clarify": [],
            "fallback": list(self.specialists.keys()),
        }
        roles = route_to_role.get(route, [AgentRole.DEVICE.value])
        return [
            self.specialists[r]
            for r in roles
            if r in self.specialists
        ]

    async def handle(self, query: str, context: Any) -> AgentResponse:
        """
        处理用户查询的主入口。
        """
        trace_id = ""
        try:
            import uuid
            trace_id = uuid.uuid4().hex[:16]
        except Exception:
            trace_id = ""

        # 1. 发布查询事件
        await self.bus.publish(Event(
            type=EventType.USER_QUERY,
            source=self.name,
            payload={"query": query},
            trace_id=trace_id,
        ))

        # 2. 路由决策
        try:
            if hasattr(self.router, "classify_intent"):
                route = self.router.classify_intent(query, context)
            elif hasattr(self.router, "decide"):
                route = self.router.decide(query, context)
            else:
                route = "local"
        except Exception as exc:
            logger.warning("Router failed: %s, falling back to local", exc)
            route = "local"

        # 3. 确定相关 Agent 并并行执行
        relevant = self._get_relevant_specialists(route)
        if not relevant:
            return AgentResponse(
                success=True,
                content=f"路由方向：{route}，无需专业 Agent 处理",
                agent_name=self.name,
            )

        # 并行分发
        tasks = [
            self._delegate(agent, query, context, trace_id)
            for agent in relevant
        ]

        import asyncio
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as exc:
            results = [exc]

        # 4. 聚合结果
        return self._aggregate(query, route, results, relevant, trace_id)

    async def _delegate(
        self,
        agent: BaseAgent,
        query: str,
        context: Any,
        trace_id: str,
    ) -> AgentResponse:
        """委托单个 Agent 处理。"""
        try:
            return await agent.handle(query, context)
        except Exception as exc:
            logger.warning("Agent %s failed: %s", agent.name, exc)
            return AgentResponse(
                success=False,
                error=str(exc),
                agent_name=agent.name,
            )

    def _aggregate(
        self,
        query: str,
        route: str,
        results: List[Any],
        agents: List[BaseAgent],
        trace_id: str,
    ) -> AgentResponse:
        """聚合多个 Agent 的响应。"""
        successes = []
        failures = []

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                failures.append(str(result))
            elif isinstance(result, AgentResponse):
                if result.success:
                    successes.append(result)
                else:
                    failures.append(f"{result.agent_name}: {result.error}")
            else:
                successes.append(result)

        content = {
            "route": route,
            "query": query,
            "trace_id": trace_id,
            "responses": [
                r.to_dict() if isinstance(r, AgentResponse) else {"content": str(r)}
                for r in successes
            ],
            "errors": failures,
            "agents_responded": [a.name for a in agents],
        }

        return AgentResponse(
            success=len(successes) > 0,
            content=content,
            agent_name=self.name,
            metadata={"route": route, "trace_id": trace_id},
        )
