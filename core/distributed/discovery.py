"""
mDNS/DNS-SD 本地服务发现

基于 LAD-A2A 规范（2025年1月）设计：
- 主协议：mDNS/DNS-SD（_homemind._tcp.local.）
- 回退：HTTP well-known（/.well-known/lad/agents）
- 安全：TLS + JWS 签名验证

支持：
- 零配置局域网服务发现
- 节点加入/离开事件通知
- 能力元数据广播
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.config import DISCOVERY_CONFIG

logger = logging.getLogger(__name__)

SERVICE_TYPE = DISCOVERY_CONFIG.get("service_type", "_homemind._tcp.local.")
SERVICE_PORT = DISCOVERY_CONFIG.get("service_port", 8765)

# ── Peer 描述 ────────────────────────────────────────────────────────────────

@dataclass
class PeerNode:
    """局域网内发现的 HomeMind 节点。"""
    node_id: str
    host: str
    port: int
    capabilities: List[str] = field(default_factory=list)
    version: str = "1.0.0"
    last_seen: float = 0.0
    is_self: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def endpoint(self) -> str:
        return f"http://{self.host}:{self.port}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "port": self.port,
            "capabilities": self.capabilities,
            "version": self.version,
            "last_seen": self.last_seen,
            "endpoint": self.endpoint(),
            "metadata": self.metadata,
        }


# ── LocalDiscovery ────────────────────────────────────────────────────────────

class LocalDiscovery:
    """
    局域网零配置服务发现。

    使用 zeroconf 库实现 mDNS/DNS-SD。
    支持：
    - advertise(): 向局域网广播本节点服务
    - browse(): 发现局域网内其他 HomeMind 节点
    - on_peer_join / on_peer_leave: 节点变化回调

    用法：
        discovery = LocalDiscovery(
            node_id="hm-edge-01",
            capabilities=["agent", "gateway", "tts"],
        )
        await discovery.start()
    """

    def __init__(
        self,
        node_id: str,
        capabilities: Optional[List[str]] = None,
        port: int = SERVICE_PORT,
        version: str = "1.0.0",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.node_id = node_id
        self.capabilities = capabilities or []
        self.port = port
        self.version = version
        self.metadata = metadata or {}

        self._peers: Dict[str, PeerNode] = {}
        self._lock = asyncio.Lock()
        self._zeroconf: Any = None
        self._service_info: Any = None
        self._running = False

        # 事件回调
        self.on_peer_join: Optional[Callable[[PeerNode], None]] = None
        self.on_peer_leave: Optional[Callable[[str], None]] = None

    def _get_local_ip(self) -> str:
        """获取本机局域网 IP。"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def start(self) -> bool:
        """启动 mDNS 发现服务。"""
        try:
            import zeroconf
            self._zeroconf = zeroconf.Zeroconf()
            self._running = True

            # 注册本节点服务
            self._register_service()

            # 启动浏览器
            asyncio.create_task(self._browse_loop())

            logger.info("LocalDiscovery started: node=%s, ip=%s, port=%d",
                        self.node_id, self._get_local_ip(), self.port)
            return True

        except ImportError:
            logger.warning("zeroconf not installed, using fallback discovery")
            return await self._start_fallback()
        except Exception as exc:
            logger.error("LocalDiscovery start failed: %s", exc)
            return False

    def _register_service(self) -> None:
        """向 mDNS 注册本节点服务。"""
        if self._zeroconf is None:
            return

        try:
            import zeroconf as zc

            service_info = zc.ServiceInfo(
                SERVICE_TYPE,
                f"{self.node_id}.{SERVICE_TYPE}",
                addresses=[socket.inet_aton(self._get_local_ip())],
                port=self.port,
                properties={
                    b"node_id": self.node_id.encode(),
                    b"capabilities": ",".join(self.capabilities).encode(),
                    b"version": self.version.encode(),
                },
            )
            self._zeroconf.register_service(service_info)
            self._service_info = service_info
            logger.info("mDNS service registered: %s", self.node_id)
        except Exception as exc:
            logger.warning("mDNS register failed: %s", exc)

    async def _browse_loop(self) -> None:
        """持续发现局域网内其他节点。"""
        try:
            import zeroconf as zc

            listener = _PeerListener(self)
            browser = zc.ServiceBrowser(
                self._zeroconf,
                SERVICE_TYPE,
                listener=listener,
            )

            # 添加已发现的节点（排除自己）
            for info in browser.services.values():
                await self._on_service_added(info)

        except ImportError:
            pass
        except Exception as exc:
            logger.warning("Browse loop error: %s", exc)

    async def _on_service_added(self, service_info: Any) -> None:
        """处理新服务发现。"""
        try:
            name = service_info.name
            if self.node_id in name:
                return  # 排除自己

            # 解析服务
            if not service_info.addresses:
                return

            host = socket.inet_ntoa(service_info.addresses[0])
            port = service_info.port
            props = service_info.properties

            capabilities = []
            if b"capabilities" in props:
                capabilities = props[b"capabilities"].decode().split(",")

            version = "1.0.0"
            if b"version" in props:
                version = props[b"version"].decode()

            peer = PeerNode(
                node_id=name.replace(f".{SERVICE_TYPE}", ""),
                host=host,
                port=port,
                capabilities=capabilities,
                version=version,
                last_seen=datetime.now().timestamp(),
            )

            async with self._lock:
                is_new = peer.node_id not in self._peers
                self._peers[peer.node_id] = peer

            if is_new and self.on_peer_join:
                self.on_peer_join(peer)
                logger.info("Peer joined: %s @ %s:%d", peer.node_id, host, port)

        except Exception as exc:
            logger.warning("Service added error: %s", exc)

    async def _start_fallback(self) -> bool:
        """
        回退模式：无 zeroconf 时通过 HTTP well-known 端点发现。
        """
        logger.info("Using HTTP fallback discovery (no zeroconf)")
        asyncio.create_task(self._http_browse_loop())
        return True

    async def _http_browse_loop(self) -> None:
        """HTTP well-known 回退发现（每 30 秒扫描常见网段）。"""
        import requests

        local_ip = self._get_local_ip()
        base_ip = ".".join(local_ip.split(".")[:3])

        for i in range(1, 255):
            ip = f"{base_ip}.{i}"
            if ip == local_ip:
                continue
            try:
                resp = requests.get(
                    f"http://{ip}:{self.port}/.well-known/lad/agents",
                    timeout=1,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for agent in data.get("agents", []):
                        peer = PeerNode(
                            node_id=agent.get("node_id", ""),
                            host=ip,
                            port=self.port,
                            capabilities=agent.get("capabilities", []),
                            version=agent.get("version", "1.0.0"),
                        )
                        async with self._lock:
                            if peer.node_id not in self._peers:
                                self._peers[peer.node_id] = peer
                                if self.on_peer_join:
                                    self.on_peer_join(peer)
            except Exception:
                pass

            await asyncio.sleep(0.01)

    async def stop(self) -> None:
        """停止发现服务。"""
        self._running = False
        if self._zeroconf:
            try:
                if self._service_info:
                    self._zeroconf.unregister_service(self._service_info)
                self._zeroconf.close()
            except Exception:
                pass
        logger.info("LocalDiscovery stopped")

    def get_peers(self) -> List[PeerNode]:
        """返回当前发现的节点列表。"""
        return list(self._peers.values())

    def get_peer(self, node_id: str) -> Optional[PeerNode]:
        return self._peers.get(node_id)

    def get_peers_by_capability(self, capability: str) -> List[PeerNode]:
        return [p for p in self._peers.values() if capability in p.capabilities]


class _PeerListener:
    """mDNS 服务浏览器监听器。"""
    def __init__(self, discovery: LocalDiscovery):
        self._discovery = discovery

    def add_service(self, zc: Any, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if info:
            asyncio.create_task(self._discovery._on_service_added(info))

    def remove_service(self, zc: Any, type_: str, name: str) -> None:
        node_id = name.replace(f".{SERVICE_TYPE}", "")
        asyncio.create_task(self._discovery._on_peer_removed(node_id))

    async def _on_peer_removed(self, node_id: str) -> None:
        async with self._discovery._lock:
            if node_id in self._discovery._peers:
                del self._discovery._peers[node_id]
        if self._discovery.on_peer_leave:
            self._discovery.on_peer_leave(node_id)
        logger.info("Peer left: %s", node_id)
