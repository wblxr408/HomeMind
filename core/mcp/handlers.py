"""
MCP 工具调用处理器 — 桥接 MCP 协议与 HomeMind 内部组件

由 main.py / web/server.py 在初始化时注册具体的处理函数。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# HomeMindAgent 实例引用，由外部注入
_agent_instance: Optional[Any] = None


def register_agent_instance(agent: Any):
    """注入 HomeMindAgent 实例，使 MCP 工具处理器可调用真实逻辑。"""
    global _agent_instance
    _agent_instance = agent


def get_agent() -> Optional[Any]:
    return _agent_instance


# ── 工具处理器实现 ──────────────────────────────────────────────────────────


async def handle_device_control(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    device = args.get("device", "")
    action = args.get("action", "")
    params = args.get("params", {})
    return _agent_instance.device_ctrl.execute(device, action, params)


async def handle_trigger_scene(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    scene = args.get("scene", "")
    return _agent_instance.scene_switcher.execute(scene)


async def handle_query_context(args: Dict[str, Any]) -> Dict[str, Any]:
    if _agent_instance is None:
        return {"error": "HomeMind Agent not initialized"}
    scope = args.get("scope", "all")

    result = {}
    if scope in ("devices", "all"):
        result["devices"] = _agent_instance.device_ctrl.get_all_state()
    if scope in ("scene", "all"):
        result["current_scene"] = _agent_instance.session_store.get_current_scene()
    if scope in ("preferences", "all"):
        result["preferences"] = _agent_instance.preference_store.get_cloud_preference_summary()
    if scope == "all":
        result["environment"] = {
            "temperature": getattr(_agent_instance.info_query, "temperature", 0),
            "humidity": getattr(_agent_instance.info_query, "humidity", 0),
        }
    return result


async def handle_info_query(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    query_type = args.get("query_type", "")
    params = args.get("params", {})
    return _agent_instance.info_query.execute(query_type, params)


async def handle_nl_to_scene_rule(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    nl = args.get("natural_language", "")
    try:
        rule = _agent_instance.nl_to_tap.parse(nl)
        return f"规则已创建：{rule.get('name', '未命名')}"
    except Exception as exc:
        return f"规则创建失败：{exc}"


async def handle_kb_query(args: Dict[str, Any]) -> list:
    if _agent_instance is None:
        return []
    query = args.get("query", "")
    top_k = int(args.get("top_k", 3))
    category = args.get("category")
    results = _agent_instance.kb.query(query, top_k=top_k, category=category)
    return [
        {"content": r.get("content", ""), "category": r.get("category", "")}
        for r in results
    ]


async def handle_kb_add(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    content = args.get("content", "")
    category = args.get("category", "用户习惯")
    accepted = bool(args.get("accepted", True))
    _agent_instance.kb.add(content, category=category, accepted=accepted)
    return f"知识已写入：{content[:50]}..."


async def handle_rule_list(args: Dict[str, Any]) -> list:
    if _agent_instance is None:
        return []
    rules = _agent_instance.tap_rule_store.list_rules()
    return [
        {
            "id": r.get("id", ""),
            "name": r.get("name", ""),
            "trigger": r.get("trigger", ""),
            "enabled": r.get("enabled", True),
        }
        for r in rules
    ]


async def handle_rule_toggle(args: Dict[str, Any]) -> str:
    if _agent_instance is None:
        return "HomeMind Agent not initialized"
    rule_id = args.get("rule_id", "")
    enabled = bool(args.get("enabled", True))
    ok = _agent_instance.tap_rule_store.toggle_rule(rule_id, enabled)
    return f"规则 {'启用' if enabled else '禁用'}成功" if ok else f"规则 {rule_id} 未找到"


# ── 处理器注册 ──────────────────────────────────────────────────────────────

from core.mcp.tools import register_tool_handler

register_tool_handler("device_control", handle_device_control)
register_tool_handler("trigger_scene", handle_trigger_scene)
register_tool_handler("query_context", handle_query_context)
register_tool_handler("info_query", handle_info_query)
register_tool_handler("nl_to_scene_rule", handle_nl_to_scene_rule)
register_tool_handler("kb_query", handle_kb_query)
register_tool_handler("kb_add", handle_kb_add)
register_tool_handler("rule_list", handle_rule_list)
register_tool_handler("rule_toggle", handle_rule_toggle)
