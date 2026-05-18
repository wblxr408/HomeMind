"""
Mesh 网络传输层 — 节点间点对点通信

提供：
- WebSocket 长连接管理
- HTTP 请求/响应
- 消息广播与点对点传递
- 离线消息 Relay（Store-and-Forward）
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.config import MESH_CONFIG

logger = logging.getLogger(__name__)

# ── 消息类型 ────────────────────────────────────────────────────────────────

@dataclass
class MeshMessage:
    """Mesh 网络消息。"""
    id: str = ""
    from_node: str = ""
    to_node: str = ""  # 空=广播
    type: str = ""      # "event" | "request" | "response"
    action: str = ""    # "device_state" | "context_sync" | "handoff"
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().astimezone().isoformat()
        if not self.id:
            import uuid
            self.id = uuid.uuid4().hex[:16]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "from": self.from_node,
            "to": self.to_node,
            "type": self.type,
            "action": self.action,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }


# ── Peer 连接 ────────────────────────────────────────────────────────────────

class PeerConnection:
    """到单个远端节点的连接。"""

    def __init__(self, node_id: str, peer: Any, ws: Optional[Any] = None):
        self.node_id = node_id
        self._ws = ws
        self._http_base = f"http://{peer.host}:{peer.port}" if peer else ""
        self._connected = ws is not None
        self._last_ping = datetime.now().timestamp()

    async def send(self, message: MeshMessage) -> bool:
        """发送消息到对端。"""
        if self._ws and self._connected:
            try:
                await self._ws.send(json.dumps(message.to_dict(), ensure_ascii=False))
                return True
            except Exception as exc:
                logger.warning("WS send failed to %s: %s", self.node_id, exc)
                self._connected = False
                return False

        # HTTP 回退
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._http_base}/mesh/message",
                    json=message.to_dict(),
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status < 400
        except Exception as exc:
            logger.warning("HTTP send failed to %s: %s", self.node_id, exc)
            return False

    def is_connected(self) -> bool:
        return self._connected


# ── MeshTransport ────────────────────────────────────────────────────────────

class MeshTransport:
    """
    Mesh 网络传输管理器。

    负责：
    - 管理到各远端节点的连接
    - 广播消息到所有已连接节点
    - 点对点消息传递
    - 离线消息 Relay（Store-and-Forward）

    用法：
        transport = MeshTransport(node_id="hm-edge-01")
        await transport.connect_peer(peer)
        await transport.broadcast(msg)
        await transport.relay_to("hm-phone-01", msg)
    """

    def __init__(self, node_id: str, relay_store_path: str = None):
        self.node_id = node_id
        self._peers: Dict[str, PeerConnection] = {}
        if relay_store_path is None:
            relay_store_path = MESH_CONFIG.get("relay_db_path", "data/mesh_relay.db")
        self._relay_store_path = relay_store_path
        self._lock = asyncio.Lock()

        # 消息处理
        self._handlers: Dict[str, Callable] = {}

        # WebSocket 服务器（接收远端连接）
        self._ws_server: Optional[Any] = None
        self._running = False

    async def connect_peer(self, peer: Any) -> bool:
        """连接到远端节点。"""
        node_id = peer.node_id if hasattr(peer, 'node_id') else str(peer)
        endpoint = peer.endpoint() if hasattr(peer, 'endpoint') else peer

        try:
            async with self._lock:
                if node_id in self._peers:
                    return True

                # 尝试 WebSocket
                ws = None
                try:
                    import websockets
                    ws_endpoint = endpoint.replace("http://", "ws://").replace("https://", "wss://")
                    ws_endpoint += "/mesh/ws"
                    ws = await websockets.connect(ws_endpoint, ping_interval=30)
                except Exception:
                    pass

                conn = PeerConnection(node_id, peer, ws=ws)
                self._peers[node_id] = conn
                logger.info("Connected to peer: %s", node_id)
                return True

        except Exception as exc:
            logger.error("Connect peer %s failed: %s", node_id, exc)
            return False

    async def disconnect_peer(self, node_id: str) -> None:
        """断开与远端节点的连接。"""
        async with self._lock:
            if node_id in self._peers:
                conn = self._peers[node_id]
                if conn._ws:
                    await conn._ws.close()
                del self._peers[node_id]
                logger.info("Disconnected peer: %s", node_id)

    async def broadcast(self, message: MeshMessage) -> Dict[str, bool]:
        """向所有已连接 peer 广播消息。"""
        results = {}
        message.from_node = self.node_id
        message.to_node = ""  # 广播标识

        async with self._lock:
            peers = list(self._peers.items())

        for node_id, conn in peers:
            results[node_id] = await conn.send(message)

        return results

    async def relay_to(self, target_node_id: str, message: MeshMessage) -> bool:
        """点对点消息传递，支持离线 Relay。"""
        message.from_node = self.node_id
        message.to_node = target_node_id

        async with self._lock:
            conn = self._peers.get(target_node_id)

        if conn and conn.is_connected():
            return await conn.send(message)

        # 离线 → Store-and-Forward
        await self._store_for_retry(target_node_id, message)
        logger.info("Message stored for retry: %s -> %s", self.node_id, target_node_id)
        return False

    async def _store_for_retry(self, target_node_id: str, message: MeshMessage) -> None:
        """离线消息存储，供节点重新上线后补发。"""
        try:
            import sqlite3
            from pathlib import Path
            Path(self._relay_store_path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(self._relay_store_path)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS relay ("
                "id TEXT, target_node TEXT, message TEXT, stored_at REAL)"
            )
            conn.execute(
                "INSERT INTO relay (id, target_node, message, stored_at) VALUES (?, ?, ?, ?)",
                (message.id, target_node_id, json.dumps(message.to_dict(), ensure_ascii=False), datetime.now().timestamp()),
            )
            conn.commit()
            conn.close()
        except Exception as exc:
            logger.warning("Relay store failed: %s", exc)

    async def flush_relay(self, target_node_id: str) -> List[MeshMessage]:
        """节点重新上线后，补发所有离线消息。"""
        messages = []
        try:
            import sqlite3
            conn = sqlite3.connect(self._relay_store_path)
            rows = conn.execute(
                "SELECT message FROM relay WHERE target_node = ? ORDER BY stored_at",
                (target_node_id,),
            ).fetchall()
            conn.execute("DELETE FROM relay WHERE target_node = ?", (target_node_id,))
            conn.commit()
            conn.close()
            for row in rows:
                messages.append(MeshMessage(**json.loads(row[0])))
        except Exception as exc:
            logger.warning("Relay flush failed: %s", exc)
        return messages

    def register_handler(self, action: str, handler: Callable) -> None:
        """注册消息处理函数。"""
        self._handlers[action] = handler

    async def handle_message(self, message: MeshMessage) -> None:
        """处理收到的消息。"""
        handler = self._handlers.get(message.action)
        if handler:
            try:
                await handler(message)
            except Exception as exc:
                logger.warning("Message handler error: %s", exc)

    async def start_ws_server(self, host: str = "0.0.0.0", port: int = None) -> None:
        """启动 WebSocket 服务器，接收远端连接。"""
        if port is None:
            port = MESH_CONFIG.get("ws_server_port", 8765)
        """启动 WebSocket 服务器，接收远端连接。"""
        try:
            import websockets

            self._running = True

            async def ws_handler(ws, path):
                peer_id = ""
                try:
                    async for raw in ws:
                        data = json.loads(raw)
                        msg = MeshMessage(**data)
                        peer_id = msg.from_node

                        # 注册 peer
                        async with self._lock:
                            self._peers[peer_id] = PeerConnection(peer_id, None, ws=ws)

                        # 处理消息
                        await self.handle_message(msg)

                except Exception as exc:
                    logger.warning("WS handler error: %s", exc)
                finally:
                    if peer_id:
                        await self.disconnect_peer(peer_id)

            self._ws_server = await websockets.serve(ws_handler, host, port)
            logger.info("Mesh WS server started: %s:%d", host, port)

        except ImportError:
            logger.warning("websockets not available, WS server not started")
        except Exception as exc:
            logger.error("WS server start failed: %s", exc)

    async def stop(self) -> None:
        self._running = False
        if self._ws_server:
            self._ws_server.close()
            await self._ws_server.wait_closed()
        for node_id in list(self._peers.keys()):
            await self.disconnect_peer(node_id)
        logger.info("MeshTransport stopped")

    def get_connected_peers(self) -> List[str]:
        return [n for n, c in self._peers.items() if c.is_connected()]
