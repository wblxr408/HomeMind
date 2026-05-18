"""
MCP Client — 调用外部 MCP 工具

允许 HomeMind Agent 作为 MCP Client，调用外部 MCP Server（如天气服务、日历等第三方工具）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

MCP_AVAILABLE = False
try:
    from mcp.client import ClientSession
    from mcp.client.stdio import stdio_client
    from mcp.client.sse import sse_client
    from subprocess import Popen

    MCP_AVAILABLE = True
except ImportError:
    pass


@dataclass
class MCPToolResult:
    """MCP 工具调用结果。"""
    content: List[Any]
    is_error: bool = False
    error_message: str = ""


class MCPClient:
    """
    MCP Client — 调用外部 MCP 工具服务器。

    支持两种连接方式：
    - stdio: 启动子进程（推荐，本地工具）
    - SSE: 通过 HTTP SSE 连接远程服务

    用法（stdio 模式）：
        client = MCPClient()
        await client.connect_stdio("python /path/to/weather_server.py")
        result = await client.call_tool("get_weather", {"city": "北京"})
        await client.disconnect()
    """

    def __init__(self):
        self._session: Optional[ClientSession] = None
        self._proc: Optional[Any] = None
        self._connected = False
        self._tools: List[Dict] = []

    async def connect_stdio(self, command: str, args: Optional[List[str]] = None) -> bool:
        """
        通过 stdio 连接 MCP Server（启动子进程）。

        Args:
            command: 启动命令，如 "python" 或 "node"
            args: 命令参数列表，如 ["/path/to/server.py"]
        """
        if not MCP_AVAILABLE:
            logger.warning("MCP client unavailable: mcp package not installed")
            return False

        try:
            if args:
                proc = Popen([command] + args)
            else:
                proc = Popen(command, shell=True)
            self._proc = proc

            read_stream, write_stream = stdio_client(proc.stdout, proc.stdin)
            self._session = ClientSession(read_stream, write_stream)
                await self._session.initialize()
                self._connected = True

                # 获取工具列表
                tools_response = await self._session.list_tools()
                self._tools = [
                    {"name": t.name, "description": t.description}
                    for t in tools_response.tools
                ]
                logger.info("MCP Client connected, tools: %s", [t["name"] for t in self._tools])
                return True

        except Exception as exc:
            logger.error("MCP stdio connect failed: %s", exc)
            self._connected = False
            return False

    async def connect_sse(self, url: str, headers: Optional[Dict[str, str]] = None) -> bool:
        """
        通过 SSE/HTTP 连接远程 MCP Server。

        Args:
            url: MCP Server 的 SSE 端点 URL
            headers: 可选的 HTTP 请求头
        """
        if not MCP_AVAILABLE:
            logger.warning("MCP client unavailable")
            return False

        try:
            async with sse_client(url, headers=headers or {}) as (read, write):
                self._session = ClientSession(read, write)
                await self._session.initialize()
                self._connected = True

                tools_response = await self._session.list_tools()
                self._tools = [
                    {"name": t.name, "description": t.description}
                    for t in tools_response.tools
                ]
                logger.info("MCP Client connected to %s, tools: %s", url, [t["name"] for t in self._tools])
                return True

        except Exception as exc:
            logger.error("MCP SSE connect failed: %s", exc)
            self._connected = False
            return False

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> MCPToolResult:
        """
        调用远端 MCP Server 上的工具。

        Args:
            name: 工具名称
            arguments: 工具参数字典

        Returns:
            MCPToolResult: 工具执行结果
        """
        if not self._connected or self._session is None:
            return MCPToolResult(
                content=[],
                is_error=True,
                error_message="Not connected to any MCP server",
            )

        try:
            result = await self._session.call_tool(name, arguments)
            return MCPToolResult(
                content=[item.model_dump() for item in result.content]
            )
        except Exception as exc:
            logger.error("MCP call_tool %s failed: %s", name, exc)
            return MCPToolResult(
                content=[],
                is_error=True,
                error_message=str(exc),
            )

    def get_tools(self) -> List[Dict]:
        """返回已连接 Server 提供的工具列表。"""
        return self._tools

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self):
        """断开连接。"""
        if self._session:
            await self._session.close()
            self._session = None
        if self._proc:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None
        self._connected = False
        self._tools = []
        logger.info("MCP Client disconnected")


# ── 全局 Client 管理 ──────────────────────────────────────────────────────────

_global_clients: Dict[str, MCPClient] = {}


def get_mcp_client(name: str = "default") -> MCPClient:
    """获取命名的全局 MCP Client 实例。"""
    if name not in _global_clients:
        _global_clients[name] = MCPClient()
    return _global_clients[name]


def register_mcp_client(name: str, client: MCPClient):
    """注册全局 MCP Client。"""
    _global_clients[name] = client
