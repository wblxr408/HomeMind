"""Dynamic tool registry for structured agent actions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


DEFAULT_TOOL_SPECS = [
    {
        "name": "device_control",
        "action_type": "设备控制",
        "binding": "device_control",
        "call_style": "device_control",
        "description": "控制单个模拟设备",
        "required_fields": ["device", "device_action"],
    },
    {
        "name": "scene_switch",
        "action_type": "场景切换",
        "binding": "scene_switcher",
        "call_style": "scene_switch",
        "description": "执行场景批量切换",
        "required_fields": ["scene"],
    },
    {
        "name": "info_query",
        "action_type": "信息查询",
        "binding": "info_query",
        "call_style": "info_query",
        "description": "查询环境与历史信息",
        "required_fields": ["query_type"],
    },
]


@dataclass
class ToolSpec:
    name: str
    action_type: str
    binding: str
    call_style: str
    description: str = ""
    enabled: bool = True
    required_fields: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ToolRegistry:
    """Configuration-driven runtime tool registry."""

    def __init__(self, config_path: str = "config/tool_registry.json"):
        self.config_path = Path(config_path)
        self._tools_by_action: Dict[str, ToolSpec] = {}
        self._bindings: Dict[str, Any] = {}
        self._load_specs()

    def _load_specs(self) -> None:
        specs = []
        if self.config_path.exists():
            try:
                data = json.loads(self.config_path.read_text(encoding="utf-8"))
                specs = list(data.get("tools", []))
            except Exception as exc:
                logger.warning("ToolRegistry: failed to load %s: %s", self.config_path, exc)
        if not specs:
            specs = list(DEFAULT_TOOL_SPECS)

        self._tools_by_action.clear()
        for item in specs:
            spec = ToolSpec(
                name=str(item.get("name", "")).strip(),
                action_type=str(item.get("action_type", "")).strip(),
                binding=str(item.get("binding", "")).strip(),
                call_style=str(item.get("call_style", "")).strip(),
                description=str(item.get("description", "")).strip(),
                enabled=bool(item.get("enabled", True)),
                required_fields=list(item.get("required_fields", []) or []),
                metadata=dict(item.get("metadata", {}) or {}),
            )
            if not spec.name or not spec.action_type or not spec.binding or not spec.call_style:
                continue
            self._tools_by_action[spec.action_type] = spec

    def bind(self, binding: str, instance: Any) -> None:
        self._bindings[str(binding or "").strip()] = instance

    def bind_many(self, bindings: Dict[str, Any]) -> None:
        for key, value in (bindings or {}).items():
            self.bind(key, value)

    def get_tool(self, action_type: str) -> Optional[ToolSpec]:
        spec = self._tools_by_action.get(str(action_type or "").strip())
        if spec and spec.enabled:
            return spec
        return None

    def list_tools(self) -> List[Dict[str, Any]]:
        result = []
        for spec in self._tools_by_action.values():
            result.append(
                {
                    "name": spec.name,
                    "action_type": spec.action_type,
                    "binding": spec.binding,
                    "call_style": spec.call_style,
                    "description": spec.description,
                    "enabled": spec.enabled,
                    "required_fields": list(spec.required_fields),
                }
            )
        return result

    def execute(self, command: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(command or {})
        action_type = str(normalized.get("action", "")).strip()
        spec = self.get_tool(action_type)
        if spec is None:
            return {
                "status": "unsupported",
                "error": f"no tool registered for action={action_type}",
                "command": normalized,
            }

        missing = [field for field in spec.required_fields if not normalized.get(field)]
        if missing:
            return {
                "status": "invalid",
                "error": f"missing required fields: {', '.join(missing)}",
                "command": normalized,
                "tool": spec.name,
            }

        handler = self._bindings.get(spec.binding)
        if handler is None:
            return {
                "status": "unsupported",
                "error": f"binding not available: {spec.binding}",
                "command": normalized,
                "tool": spec.name,
            }

        try:
            if spec.call_style == "device_control":
                response = handler.execute(
                    normalized.get("device", ""),
                    normalized.get("device_action", ""),
                    dict(normalized.get("params", {}) or {}),
                )
                return {
                    "status": "success",
                    "tool": spec.name,
                    "action": f"{normalized.get('device', '')}_{normalized.get('device_action', '')}",
                    "response": response,
                    "command": normalized,
                }
            if spec.call_style == "scene_switch":
                response = handler.execute(normalized.get("scene", ""))
                return {
                    "status": "success",
                    "tool": spec.name,
                    "action": "scene_switch",
                    "response": response,
                    "command": normalized,
                }
            if spec.call_style == "info_query":
                response = handler.execute(
                    normalized.get("query_type", ""),
                    dict(normalized.get("params", {}) or {}),
                )
                return {
                    "status": "success",
                    "tool": spec.name,
                    "action": "info_query",
                    "response": response,
                    "command": normalized,
                }
            return {
                "status": "unsupported",
                "error": f"unknown call style: {spec.call_style}",
                "command": normalized,
                "tool": spec.name,
            }
        except Exception as exc:
            logger.warning("ToolRegistry: tool %s failed: %s", spec.name, exc)
            return {
                "status": "error",
                "error": str(exc),
                "command": normalized,
                "tool": spec.name,
            }
