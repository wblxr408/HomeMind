"""
SpecialistAgent — 专业 Agent 实现

每个 Agent 专注一个领域：
- DeviceAgent: 设备控制
- SceneAgent: 场景管理
- MemoryAgent: 记忆 RAG
- InfoAgent: 信息查询
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.agent.base import AgentResponse, AgentRole, BaseAgent

logger = logging.getLogger(__name__)


class DeviceAgent(BaseAgent):
    """设备控制专家。"""

    def __init__(self):
        super().__init__("DeviceAgent", AgentRole.DEVICE)
        self._controller = None

    def attach_controller(self, controller: Any) -> None:
        self._controller = controller

    def get_capabilities(self) -> List[str]:
        return ["device_control", "device_status_query", "device_batch_control"]

    async def handle(self, query: str, context: Any) -> AgentResponse:
        if self._controller is None:
            return AgentResponse(success=False, error="Device controller not attached", agent_name=self.name)

        # 从 query 提取设备名和动作（简化版，真实实现走 BSR→LLM 链路）
        device, action, params = self._parse_device_command(query)
        if not device:
            return AgentResponse(success=False, error="无法识别设备", agent_name=self.name)

        try:
            result = self._controller.execute(device, action, params)
            return AgentResponse(
                success=True,
                content=result,
                agent_name=self.name,
                metadata={"device": device, "action": action},
            )
        except Exception as exc:
            return AgentResponse(success=False, error=str(exc), agent_name=self.name)

    def _parse_device_command(self, query: str) -> tuple:
        # 简化版解析
        devices = ["空调", "灯光", "电视", "热水器", "风扇", "音响", "窗户"]
        for d in devices:
            if d in query:
                action = "on" if "开" in query or "打开" in query else ("off" if "关" in query else "adjust")
                params = {}
                if "温度" in query:
                    import re
                    m = re.search(r"\d+", query)
                    if m:
                        params["temperature"] = int(m.group())
                return d, action, params
        return "", "", {}


class SceneAgent(BaseAgent):
    """场景管理专家。"""

    def __init__(self):
        super().__init__("SceneAgent", AgentRole.SCENE)
        self._scene_store = None
        self._scene_switcher = None

    def attach_scene_store(self, store: Any) -> None:
        self._scene_store = store

    def attach_scene_switcher(self, switcher: Any) -> None:
        self._scene_switcher = switcher

    def get_capabilities(self) -> List[str]:
        return ["scene_switch", "scene_list", "scene_create", "scene_delete"]

    async def handle(self, query: str, context: Any) -> AgentResponse:
        from core.agent.bus import Event, EventType

        # 场景切换
        scene_map = {
            "睡眠": "睡眠模式", "观影": "观影模式", "离家": "离家模式",
            "回家": "回家模式", "早安": "早安模式", "晚安": "晚安模式",
            "待客": "待客模式", "阅读": "阅读模式",
        }
        for kw, scene in scene_map.items():
            if kw in query:
                if self._scene_switcher:
                    result = self._scene_switcher.execute(scene)
                    if self._bus:
                        await self._bus.publish(Event(
                            type=EventType.SCENE_ACTIVATED,
                            source=self.name,
                            payload={"scene": scene, "query": query},
                        ))
                    return AgentResponse(success=True, content=result, agent_name=self.name)
                return AgentResponse(success=True, content=f"场景 {scene} 已激活", agent_name=self.name)

        return AgentResponse(success=False, error="无法识别场景", agent_name=self.name)


class MemoryAgent(BaseAgent):
    """记忆 RAG 专家。"""

    def __init__(self):
        super().__init__("MemoryAgent", AgentRole.MEMORY)
        self._kb = None
        self._session_store = None

    def attach_kb(self, kb: Any) -> None:
        self._kb = kb

    def attach_session_store(self, store: Any) -> None:
        self._session_store = store

    def get_capabilities(self) -> List[str]:
        return ["rag_query", "memory_write", "preference_query"]

    async def handle(self, query: str, context: Any) -> AgentResponse:
        if self._kb is None:
            return AgentResponse(success=False, error="KB not attached", agent_name=self.name)

        try:
            results = self._kb.query(query, top_k=3)
            return AgentResponse(
                success=True,
                content=results,
                agent_name=self.name,
                metadata={"query": query, "count": len(results)},
            )
        except Exception as exc:
            return AgentResponse(success=False, error=str(exc), agent_name=self.name)


class InfoAgent(BaseAgent):
    """信息查询专家。"""

    def __init__(self):
        super().__init__("InfoAgent", AgentRole.INFO)
        self._info_query = None

    def attach_info_query(self, info_query: Any) -> None:
        self._info_query = info_query

    def get_capabilities(self) -> List[str]:
        return ["temperature_query", "humidity_query", "weather_query", "schedule_query"]

    async def handle(self, query: str, context: Any) -> AgentResponse:
        if self._info_query is None:
            return AgentResponse(success=False, error="InfoQuery not attached", agent_name=self.name)

        query_type = "temperature"
        if "天气" in query:
            query_type = "weather"
        elif "湿度" in query:
            query_type = "humidity"
        elif "日程" in query or "安排" in query:
            query_type = "schedule"

        try:
            result = self._info_query.execute(query_type)
            return AgentResponse(success=True, content=result, agent_name=self.name)
        except Exception as exc:
            return AgentResponse(success=False, error=str(exc), agent_name=self.name)
