"""
Smart Home Gateway - 智能家居协议网关
支持多种智能家居协议的统一接口
"""

import logging
import json
import os
from typing import Dict, List, Optional, Any
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class ProtocolInterface(ABC):
    """协议接口基类"""

    @abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass

    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """检查连接状态"""
        pass

    @abstractmethod
    def discover_devices(self) -> List[Dict[str, Any]]:
        """发现设备"""
        pass

    @abstractmethod
    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        """获取设备状态"""
        pass

    @abstractmethod
    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        """设置设备状态"""
        pass


class MatterProtocol(ProtocolInterface):
    """Matter 协议支持"""

    def __init__(self, controller_ip: str = "192.168.1.100", port: int = 5580):
        self.controller_ip = controller_ip
        self.port = port
        self._connected = False
        self._devices: Dict[str, Dict] = {}

    def connect(self) -> bool:
        try:
            logger.info(f"[Matter] 连接到控制器 {self.controller_ip}:{self.port}")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[Matter] 连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False
        logger.info("[Matter] 已断开连接")

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        logger.info("[Matter] 设备发现中...")
        return []

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._devices.get(device_id)

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        if not self._connected:
            return False
        self._devices[device_id] = state
        return True


class MQTTProtocol(ProtocolInterface):
    """MQTT 协议支持"""

    def __init__(self, broker: str = "192.168.1.100", port: int = 1883,
                 username: str = "", password: str = ""):
        self.broker = broker
        self.port = port
        self.username = username
        self.password = password
        self._connected = False
        self._devices: Dict[str, Dict] = {}
        self._client = None

    def connect(self) -> bool:
        try:
            logger.info(f"[MQTT] 连接到 broker {self.broker}:{self.port}")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[MQTT] 连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self):
        if self._client:
            self._client.disconnect()
        self._connected = False
        logger.info("[MQTT] 已断开连接")

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        logger.info("[MQTT] 订阅设备主题...")
        return []

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._devices.get(device_id)

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        if not self._connected:
            return False
        self._devices[device_id] = state
        return True


class XiaomiProtocol(ProtocolInterface):
    """小米米家协议支持"""

    def __init__(self, gateway_ip: str = "192.168.1.50", did: str = ""):
        self.gateway_ip = gateway_ip
        self.did = did
        self._connected = False
        self._devices: Dict[str, Dict] = {}

    def connect(self) -> bool:
        try:
            logger.info(f"[Xiaomi] 连接到网关 {self.gateway_ip}")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[Xiaomi] 连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False
        logger.info("[Xiaomi] 已断开连接")

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        logger.info("[Xiaomi] 设备发现中...")
        return []

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._devices.get(device_id)

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        if not self._connected:
            return False
        self._devices[device_id] = state
        return True


class HomeAssistantProtocol(ProtocolInterface):
    """Home Assistant API 支持"""

    def __init__(self, url: str = "http://192.168.1.200:8123", token: str = ""):
        self.url = url.rstrip("/")
        self.token = token
        self._connected = False
        self._devices: Dict[str, Dict] = {}

    def connect(self) -> bool:
        try:
            logger.info(f"[HomeAssistant] 连接到 {self.url}")
            self._connected = True
            return True
        except Exception as e:
            logger.error(f"[HomeAssistant] 连接失败: {e}")
            self._connected = False
            return False

    def disconnect(self):
        self._connected = False
        logger.info("[HomeAssistant] 已断开连接")

    def is_connected(self) -> bool:
        return self._connected

    def discover_devices(self) -> List[Dict[str, Any]]:
        if not self._connected:
            return []
        logger.info("[HomeAssistant] 获取设备状态...")
        return []

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        return self._devices.get(device_id)

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        if not self._connected:
            return False
        self._devices[device_id] = state
        return True


class SmartHomeGateway:
    """
    智能家居网关 - 统一管理多种协议
    """

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or self._get_default_config_path()
        self.config: Dict[str, Any] = {}
        self.protocols: Dict[str, ProtocolInterface] = {}
        self.active_protocol: Optional[ProtocolInterface] = None
        self._devices: Dict[str, Dict[str, Any]] = {}
        self._load_config()
        self._init_protocols()

    def _get_default_config_path(self) -> str:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(project_root, "web", "protocol_config.json")

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    self.config = json.load(f)
                logger.info(f"[Gateway] 已加载配置: {self.config_path}")
            except Exception as e:
                logger.error(f"[Gateway] 配置加载失败: {e}")
                self.config = {}
        else:
            logger.warning(f"[Gateway] 配置文件不存在: {self.config_path}")

    def _init_protocols(self):
        protocol_map = {
            "matter": MatterProtocol,
            "mqtt": MQTTProtocol,
            "xiaomi": XiaomiProtocol,
            "home_assistant": HomeAssistantProtocol,
        }

        for name, cls in protocol_map.items():
            if name in self.config:
                cfg = self.config[name]
                if cfg.get("enabled", False):
                    try:
                        if name == "matter":
                            protocol = cls(
                                controller_ip=cfg.get("controller_ip", "192.168.1.100"),
                                port=cfg.get("port", 5580)
                            )
                        elif name == "mqtt":
                            protocol = cls(
                                broker=cfg.get("broker", "192.168.1.100"),
                                port=cfg.get("port", 1883),
                                username=cfg.get("username", ""),
                                password=cfg.get("password", "")
                            )
                        elif name == "xiaomi":
                            protocol = cls(
                                gateway_ip=cfg.get("gateway_ip", "192.168.1.50"),
                                did=cfg.get("did", "")
                            )
                        elif name == "home_assistant":
                            protocol = cls(
                                url=cfg.get("url", "http://192.168.1.200:8123"),
                                token=cfg.get("token", "")
                            )
                        else:
                            protocol = cls()

                        self.protocols[name] = protocol
                        logger.info(f"[Gateway] {name} 协议已初始化")
                    except Exception as e:
                        logger.error(f"[Gateway] {name} 协议初始化失败: {e}")

        self._connect_first_available()

    def _connect_first_available(self):
        for name, protocol in self.protocols.items():
            if protocol.connect():
                self.active_protocol = protocol
                logger.info(f"[Gateway] 已连接到 {name} 协议")
                break

    def discover_devices(self) -> List[Dict[str, Any]]:
        devices = []
        if self.active_protocol:
            devices = self.active_protocol.discover_devices()
            self._devices = {d.get("id", ""): d for d in devices}
        return devices

    def get_device_status(self, device_id: str) -> Optional[Dict[str, Any]]:
        if self.active_protocol:
            return self.active_protocol.get_device_status(device_id)
        return None

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        if self.active_protocol:
            return self.active_protocol.set_device_state(device_id, state)
        return False

    def is_connected(self) -> bool:
        return self.active_protocol is not None and self.active_protocol.is_connected()

    def get_all_devices(self) -> Dict[str, Dict[str, Any]]:
        return self._devices.copy()

    def reconnect(self) -> bool:
        if self.active_protocol:
            self.active_protocol.disconnect()
        return self._connect_first_available()

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "connected": self.is_connected(),
            "active_protocol": type(self.active_protocol).__name__ if self.active_protocol else None,
            "available_protocols": list(self.protocols.keys()),
            "device_count": len(self._devices),
        }


def create_gateway(mode: str = "simulated") -> Any:
    """
    工厂函数：创建协议网关
    """
    if mode == "real":
        try:
            gateway = SmartHomeGateway()
            if gateway.is_connected():
                return gateway
            else:
                logger.warning("[Gateway] 真实设备网关连接失败")
                return None
        except Exception as e:
            logger.error(f"[Gateway] 创建真实网关失败: {e}")
            return None
    else:
        return None
