"""
HomeMind MCP 模块 — 双向 MCP 集成

- MCP Server: 将 HomeMind 核心能力暴露为 MCP 工具
- MCP Client: 调用外部 MCP 工具（stdio / SSE 两种模式）

使用方式：

  # 独立 stdio 模式（Claude Desktop / Cursor）
  python -m core.mcp.server

  # Web 集成模式（Flask）
  from core.mcp.server import register_agent_handle
  register_agent_handle(agent)
  # 在 web/server.py 中挂载 /mcp 路由
"""

from core.mcp.server import mcp_server, register_agent_handle, get_agent_handle, run_stdio
from core.mcp.client import MCPClient, get_mcp_client, register_mcp_client, MCP_AVAILABLE
from core.mcp.tools import HOMEMIND_TOOLS, register_tool_handler, get_tool_handler
from core.mcp.handlers import register_agent_instance, get_agent

__all__ = [
    "mcp_server",
    "register_agent_handle",
    "get_agent_handle",
    "run_stdio",
    "MCPClient",
    "get_mcp_client",
    "register_mcp_client",
    "MCP_AVAILABLE",
    "HOMEMIND_TOOLS",
    "register_tool_handler",
    "get_tool_handler",
    "register_agent_instance",
    "get_agent",
]
