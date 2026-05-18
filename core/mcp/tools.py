"""
HomeMind MCP 工具定义

每个工具对应 HomeMind 的一个核心能力，通过 MCP JSON-RPC 暴露给外部 AI。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from mcp.types import Tool

logger = logging.getLogger(__name__)

# ── 工具定义 ────────────────────────────────────────────────────────────────

HOMEMIND_TOOLS: list[Tool] = [
    # ── 设备控制 ────────────────────────────────────────────────────────────
    Tool(
        name="device_control",
        description="控制智能家居设备（空调/灯光/电视/热水器/风扇/音响/窗户）。支持 on/off/Adjust 三种动作，可传参调节温度、亮度、音量等。",
        inputSchema={
            "type": "object",
            "properties": {
                "device": {
                    "type": "string",
                    "enum": ["空调", "灯光", "电视", "热水器", "风扇", "音响", "窗户"],
                    "description": "目标设备名称",
                },
                "action": {
                    "type": "string",
                    "enum": ["on", "off", "adjust"],
                    "description": "动作：on=开启，off=关闭，adjust=调节",
                },
                "params": {
                    "type": "object",
                    "description": "可选参数，如 temperature、brightness、volume、channel 等",
                    "properties": {
                        "temperature": {"type": "number", "description": "空调温度（°C）"},
                        "brightness": {"type": "number", "description": "灯光亮度（%）"},
                        "volume": {"type": "number", "description": "音量（0-100）"},
                        "channel": {"type": "number", "description": "电视频道"},
                        "speed": {"type": "number", "description": "风扇档位（1-3）"},
                        "mode": {"type": "string", "description": "模式，如制冷/制热/蓝牙等"},
                    },
                },
            },
            "required": ["device", "action"],
        },
    ),
    # ── 场景切换 ────────────────────────────────────────────────────────────
    Tool(
        name="trigger_scene",
        description="一键切换家居场景（如睡眠模式/观影模式/离家模式等），自动批量控制多个设备。",
        inputSchema={
            "type": "object",
            "properties": {
                "scene": {
                    "type": "string",
                    "enum": [
                        "睡眠模式", "观影模式", "离家模式", "回家模式",
                        "早安模式", "晚安模式", "待客模式", "阅读模式",
                    ],
                    "description": "目标场景名称",
                },
            },
            "required": ["scene"],
        },
    ),
    # ── 上下文查询 ──────────────────────────────────────────────────────────
    Tool(
        name="query_context",
        description="查询当前家居环境上下文：所有设备状态、当前场景、室内温湿度、用户偏好摘要。",
        inputSchema={
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["devices", "scene", "preferences", "all"],
                    "default": "all",
                    "description": "查询范围：devices=设备状态，scene=当前场景，preferences=用户偏好，all=全部",
                },
            },
        },
    ),
    # ── 信息查询 ────────────────────────────────────────────────────────────
    Tool(
        name="info_query",
        description="查询家居信息：室内温湿度、天气预报、历史记录、日程安排、用户偏好建议。",
        inputSchema={
            "type": "object",
            "properties": {
                "query_type": {
                    "type": "string",
                    "enum": ["temperature", "humidity", "weather", "history", "schedule", "preference"],
                    "description": "查询类型",
                },
                "params": {
                    "type": "object",
                    "description": "可选参数：location（天气地点）、date（历史日期）、time（日程时间）",
                },
            },
            "required": ["query_type"],
        },
    ),
    # ── 自然语言 → 场景规则 ────────────────────────────────────────────────
    Tool(
        name="nl_to_scene_rule",
        description="将自然语言描述转换为自动化场景规则。例如「晚上10点自动关灯」→ 创建定时规则。",
        inputSchema={
            "type": "object",
            "properties": {
                "natural_language": {
                    "type": "string",
                    "description": "自然语言描述的规则，如「当我下班回家时打开空调」",
                },
            },
            "required": ["natural_language"],
        },
    ),
    # ── 知识库查询 ──────────────────────────────────────────────────────────
    Tool(
        name="kb_query",
        description="查询 HomeMind 知识库，基于语义相似度检索相关知识条目。可用于回答家居相关问题。",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "查询文本",
                },
                "top_k": {
                    "type": "number",
                    "default": 3,
                    "description": "返回最相关的条目数量",
                },
                "category": {
                    "type": "string",
                    "description": "限定知识类别：健康建议/场景规则/用户习惯/用户反馈",
                },
            },
            "required": ["query"],
        },
    ),
    # ── 知识库写入 ──────────────────────────────────────────────────────────
    Tool(
        name="kb_add",
        description="向 HomeMind 知识库写入新知识条目，会自动去重合并。",
        inputSchema={
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "知识内容",
                },
                "category": {
                    "type": "string",
                    "default": "用户习惯",
                    "description": "类别：健康建议/场景规则/用户习惯/用户反馈",
                },
                "accepted": {
                    "type": "boolean",
                    "default": True,
                    "description": "是否为正向知识",
                },
            },
            "required": ["content"],
        },
    ),
    # ── 规则管理 ────────────────────────────────────────────────────────────
    Tool(
        name="rule_list",
        description="列出所有已创建的自动化规则，返回规则 ID、名称、触发条件、启用状态。",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="rule_toggle",
        description="启用或禁用指定的自动化规则。",
        inputSchema={
            "type": "object",
            "properties": {
                "rule_id": {"type": "string", "description": "规则 ID"},
                "enabled": {"type": "boolean", "description": "true=启用，false=禁用"},
            },
            "required": ["rule_id", "enabled"],
        },
    ),
]


# ── 工具处理器映射 ──────────────────────────────────────────────────────────

_tool_handlers: Dict[str, Callable] = {}


def register_tool_handler(name: str, handler: Callable):
    """注册工具处理器，由 main.py/webserver.py 在初始化时调用。"""
    _tool_handlers[name] = handler


def get_tool_handler(name: str) -> Optional[Callable]:
    return _tool_handlers.get(name)
