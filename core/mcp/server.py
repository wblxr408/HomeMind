"""
HomeMind MCP Server — 暴露 HomeMind 工具为 MCP 工具
支持 stdio 传输（Claude Desktop/Cursor）和 Streamable HTTP（Web 集成）
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from core.mcp.tools import HOMEMIND_TOOLS, get_tool_handler

logger = logging.getLogger(__name__)

# Server instance — single shared instance for stdio mode
mcp_server = Server("homemind")


def _register_tools(server: Server):
    """注册所有 HomeMind 工具到 MCP Server。"""
    tools = HOMEMIND_TOOLS

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        handler = get_tool_handler(name)
        if handler is None:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        try:
            result = await handler(arguments)
            return [TextContent(type="text", text=str(result))]
        except Exception as exc:
            logger.error("MCP tool %s failed: %s", name, exc)
            return [TextContent(type="text", text=f"Error: {exc}")]


_register_tools(mcp_server)


async def run_stdio():
    """stdio 传输入口 — 供 Claude Desktop / Cursor / 其他 MCP Client 使用。"""
    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


# ── 全局 agent 引用（由 main.py / web/server.py 注入） ──────────────────────

_agent_handle: Optional[Any] = None


def register_agent_handle(handle: Any):
    """将 HomeMindAgent 实例注入 MCP Server，使工具可调用真实逻辑。"""
    global _agent_handle
    _agent_handle = handle


def get_agent_handle() -> Optional[Any]:
    return _agent_handle


if __name__ == "__main__":
    pass  # Circular import prevented; use `register_agent_instance` from a separate entry point
