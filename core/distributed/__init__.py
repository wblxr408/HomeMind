"""
分布式通信模块 — 面试问答索引

├── discovery.py — mDNS/DNS-SD 零配置服务发现（LAD-A2A 规范）
└── transport.py — Mesh 网络传输（WebSocket / HTTP，Store-and-Forward）

核心概念：
- LAD-A2A：Local Agent Discovery Agent-to-Agent 协议（2025年1月）
- mDNS/DNS-SD：零配置局域网服务发现
- Store-and-Forward：节点离线时消息暂存，上线后补发
- WebSocket 长连接：实时双向通信

与 MCP 的关系：
- MCP：外部 AI 系统 ↔ HomeMind（工具调用）
- Mesh：HomeMind 节点 ↔ HomeMind 节点（分布式协作）
"""

from core.distributed.discovery import LocalDiscovery, PeerNode, SERVICE_TYPE, SERVICE_PORT
from core.distributed.transport import MeshTransport, MeshMessage, PeerConnection

__all__ = [
    "LocalDiscovery",
    "PeerNode",
    "SERVICE_TYPE",
    "SERVICE_PORT",
    "MeshTransport",
    "MeshMessage",
    "PeerConnection",
]
