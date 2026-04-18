"""
智能家居协议适配层
支持多种智能家居协议：Matter, MQTT, 小米米家, 涂鸦, Home Assistant 等
"""
import json
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, List, Any, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class DeviceType(Enum):
    """设备类型枚举"""
    LIGHT = "light"
    AIR_CONDITIONER = "air_conditioner"
    TV = "tv"
    WATER_HEATER = "water_heater"
    FAN = "fan"
    SPEAKER = "speaker"
    WINDOW = "window"
    SWITCH = "switch"
    SENSOR = "sensor"
    LOCK = "lock"
    CAMERA = "camera"
    UNKNOWN = "unknown"


class ProtocolType(Enum):
    """协议类型枚举"""
    MATTER = "matter"
    MQTT = "mqtt"
    XIAOMI = "xiaomi"
    TUYA = "tuya"
    HOME_ASSISTANT = "home_assistant"
    HTTP = "http"
    WEBSOCKET = "websocket"
    SIMULATED = "simulated"


@dataclass
class DeviceCapability:
    """设备能力"""
    can_on_off: bool = False
    can_adjust_brightness: bool = False
    can_adjust_temperature: bool = False
    can_adjust_speed: bool = False
    can_query_state: bool = False
    can_sensor: bool = False
    brightness_range: tuple = (0, 100)
    temperature_range: tuple = (16, 30)
    speed_range: tuple = (1, 3)


@dataclass
class DeviceInfo:
    """设备信息"""
    device_id: str
    name: str
    device_type: DeviceType
    protocol: ProtocolType
    capabilities: DeviceCapability = field(default_factory=DeviceCapability)
    room: str = "living_room"
    manufacturer: str = ""
    model: str = ""
    firmware_version: str = ""
    is_online: bool = True
    last_seen: float = field(default_factory=time.time)


@dataclass
class DeviceState:
    """设备状态"""
    device_id: str
    is_on: bool = False
    brightness: int = 100
    temperature: int = 26
    speed: int = 1
    humidity: float = 0.0
    power_consumption: float = 0.0
    error_code: str = ""
    raw_data: Dict = field(default_factory=dict)


class BaseProtocol(ABC):
    """协议基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.is_connected = False
        self.devices: Dict[str, DeviceInfo] = {}
        self._state_callbacks: List[Callable] = []
    
    @abstractmethod
    def connect(self) -> bool:
        """建立连接"""
        pass
    
    @abstractmethod
    def disconnect(self):
        """断开连接"""
        pass
    
    @abstractmethod
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """发送命令"""
        pass
    
    @abstractmethod
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """查询状态"""
        pass
    
    @abstractmethod
    def discover_devices(self) -> List[DeviceInfo]:
        """发现设备"""
        pass
    
    def on_state_change(self, callback: Callable):
        """注册状态变化回调"""
        self._state_callbacks.append(callback)
    
    def _notify_state_change(self, device_id: str, state: DeviceState):
        """通知状态变化"""
        for callback in self._state_callbacks:
            try:
                callback(device_id, state)
            except Exception as e:
                logger.error(f"状态回调执行失败: {e}")


class MatterProtocol(BaseProtocol):
    """Matter 协议适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.controller_ip = config.get("controller_ip", "192.168.1.100")
        self.controller_port = config.get("controller_port", 5580)
        self.paired_devices: Dict[str, Any] = {}
    
    def connect(self) -> bool:
        """连接 Matter 控制器"""
        try:
            # 模拟连接（实际需要使用 matter sdk）
            logger.info(f"[Matter] 连接到控制器 {self.controller_ip}:{self.controller_port}")
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"[Matter] 连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        self.is_connected = False
        logger.info("[Matter] 已断开连接")
    
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """发送 Matter 命令"""
        if not self.is_connected:
            logger.error("[Matter] 未连接")
            return False
        
        try:
            action = command.get("action")
            params = command.get("params", {})
            
            # Matter 命令格式
            matter_cmd = self._to_matter_command(action, params)
            logger.info(f"[Matter] 发送命令到 {device_id}: {matter_cmd}")
            
            # 模拟执行
            return True
        except Exception as e:
            logger.error(f"[Matter] 命令发送失败: {e}")
            return False
    
    def _to_matter_command(self, action: str, params: Dict) -> Dict:
        """转换为 Matter 命令格式"""
        cmd_map = {
            "on": {"OnOff": {"on": True}},
            "off": {"OnOff": {"on": False}},
            "brightness": {"LevelControl": {"level": params.get("value", 100)}},
            "temperature": {"Thermostat": {"occupied_cooling_setpoint": params.get("value", 26) * 100}}
        }
        return cmd_map.get(action, {})
    
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """查询 Matter 设备状态"""
        # 模拟状态查询
        return DeviceState(
            device_id=device_id,
            is_on=False,
            brightness=100,
            temperature=26
        )
    
    def discover_devices(self) -> List[DeviceInfo]:
        """发现 Matter 设备"""
        # 模拟设备发现
        devices = [
            DeviceInfo(
                device_id="matter_light_001",
                name="Matter 灯泡",
                device_type=DeviceType.LIGHT,
                protocol=ProtocolType.MATTER,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_brightness=True,
                    can_query_state=True
                ),
                manufacturer="Philips",
                model="Hue Bulb"
            ),
            DeviceInfo(
                device_id="matter_ac_001",
                name="Matter 空调",
                device_type=DeviceType.AIR_CONDITIONER,
                protocol=ProtocolType.MATTER,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_temperature=True,
                    can_query_state=True,
                    temperature_range=(16, 30)
                ),
                manufacturer="Daikin",
                model="FTXB"
            )
        ]
        return devices


class MQTTProtocol(BaseProtocol):
    """MQTT 协议适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.broker_host = config.get("broker_host", "localhost")
        self.broker_port = config.get("broker_port", 1883)
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.topics: Dict[str, str] = config.get("topics", {})
        self.client = None
        self._subscription_handlers: Dict[str, Callable] = {}
    
    def connect(self) -> bool:
        """连接 MQTT Broker"""
        try:
            # 尝试使用 paho-mqtt
            try:
                import paho.mqtt.client as mqtt
                
                def on_connect(client, userdata, flags, rc):
                    if rc == 0:
                        self.is_connected = True
                        logger.info("[MQTT] 连接成功")
                        # 订阅设备状态主题
                        for topic in self.topics.get("state", []):
                            client.subscribe(topic)
                    else:
                        logger.error(f"[MQTT] 连接失败: {rc}")
                
                def on_message(client, userdata, msg):
                    self._handle_mqtt_message(msg)
                
                self.client = mqtt.Client()
                self.client.on_connect = on_connect
                self.client.on_message = on_message
                
                if self.username:
                    self.client.username_pw_set(self.username, self.password)
                
                self.client.connect(self.broker_host, self.broker_port, 60)
                self.client.loop_start()
                
            except ImportError:
                logger.warning("[MQTT] paho-mqtt 未安装，使用模拟模式")
                self.is_connected = True
            
            return True
        except Exception as e:
            logger.error(f"[MQTT] 连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开 MQTT 连接"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
        self.is_connected = False
        logger.info("[MQTT] 已断开连接")
    
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """通过 MQTT 发送命令"""
        if not self.is_connected:
            logger.error("[MQTT] 未连接")
            return False
        
        try:
            action = command.get("action")
            params = command.get("params", {})
            
            # 构建 MQTT 消息
            topic = self.topics.get("command", "home/device/{device_id}/command")
            topic = topic.format(device_id=device_id)
            
            payload = {
                "action": action,
                "params": params,
                "timestamp": int(time.time())
            }
            
            if self.client:
                self.client.publish(topic, json.dumps(payload))
                logger.info(f"[MQTT] 发布命令到 {topic}: {payload}")
            
            return True
        except Exception as e:
            logger.error(f"[MQTT] 命令发送失败: {e}")
            return False
    
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """通过 MQTT 查询状态"""
        topic = self.topics.get("state", "home/device/{device_id}/state")
        topic = topic.format(device_id=device_id)
        
        # 模拟状态查询
        return DeviceState(
            device_id=device_id,
            is_on=False,
            brightness=100,
            temperature=26
        )
    
    def discover_devices(self) -> List[DeviceInfo]:
        """通过 MQTT 发现设备"""
        devices = [
            DeviceInfo(
                device_id="mqtt_light_001",
                name="MQTT 灯泡",
                device_type=DeviceType.LIGHT,
                protocol=ProtocolType.MQTT,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_brightness=True,
                    can_query_state=True
                )
            )
        ]
        return devices
    
    def _handle_mqtt_message(self, msg):
        """处理收到的 MQTT 消息"""
        try:
            topic = msg.topic
            payload = json.loads(msg.payload.decode())
            
            # 提取设备 ID
            device_id = topic.split("/")[-2]
            
            # 调用对应的处理函数
            if topic in self._subscription_handlers:
                self._subscription_handlers[topic](device_id, payload)
            
            # 解析状态并通知
            state = self._parse_state(device_id, payload)
            if state:
                self._notify_state_change(device_id, state)
                
        except Exception as e:
            logger.error(f"[MQTT] 消息处理失败: {e}")
    
    def _parse_state(self, device_id: str, payload: Dict) -> Optional[DeviceState]:
        """解析设备状态"""
        try:
            return DeviceState(
                device_id=device_id,
                is_on=payload.get("is_on", False),
                brightness=payload.get("brightness", 100),
                temperature=payload.get("temperature", 26),
                raw_data=payload
            )
        except Exception:
            return None
    
    def subscribe(self, topic: str, handler: Callable):
        """订阅主题"""
        self._subscription_handlers[topic] = handler
        if self.client and self.is_connected:
            self.client.subscribe(topic)


class XiaomiProtocol(BaseProtocol):
    """小米米家协议适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.server_ip = config.get("server_ip", "192.168.1.1")
        self.server_port = config.get("server_port", 54321)
        self.token = config.get("token", "")
        self.did_map: Dict[str, str] = {}  # device_id -> miio did
    
    def connect(self) -> bool:
        """连接小米网关"""
        try:
            logger.info(f"[小米] 连接到 {self.server_ip}:{self.server_port}")
            # 实际需要使用 miio 库
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"[小米] 连接失败: {e}")
            return False
    
    def disconnect(self):
        """断开连接"""
        self.is_connected = False
    
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """发送米家命令"""
        if not self.is_connected:
            return False
        
        action = command.get("action")
        params = command.get("params", {})
        
        # 米家命令格式
        mi_cmd = self._build_mi_command(action, params)
        logger.info(f"[小米] 设备 {device_id} 命令: {mi_cmd}")
        
        return True
    
    def _build_mi_command(self, action: str, params: Dict) -> Dict:
        """构建米家命令"""
        if action == "on":
            return {"method": "set_power", "params": ["on"]}
        elif action == "off":
            return {"method": "set_power", "params": ["off"]}
        elif action == "brightness":
            return {"method": "set_brightness", "params": [params.get("value", 100)]}
        elif action == "temperature":
            return {"method": "set_temp", "params": [params.get("value", 26)]}
        return {}
    
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """查询米家设备状态"""
        return DeviceState(device_id=device_id, is_on=False)
    
    def discover_devices(self) -> List[DeviceInfo]:
        """发现米家设备"""
        devices = [
            DeviceInfo(
                device_id="xiaomi_light_001",
                name="米家吸顶灯",
                device_type=DeviceType.LIGHT,
                protocol=ProtocolType.XIAOMI,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_brightness=True,
                    can_query_state=True
                ),
                manufacturer="小米",
                model="MJXDD02SYL"
            ),
            DeviceInfo(
                device_id="xiaomi_ac_001",
                name="米家空调",
                device_type=DeviceType.AIR_CONDITIONER,
                protocol=ProtocolType.XIAOMI,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_temperature=True,
                    can_query_state=True
                ),
                manufacturer="小米",
                model="KFR-35GW"
            ),
            DeviceInfo(
                device_id="xiaomi_sensor_001",
                name="米家温湿度传感器",
                device_type=DeviceType.SENSOR,
                protocol=ProtocolType.XIAOMI,
                capabilities=DeviceCapability(
                    can_sensor=True,
                    can_query_state=True
                ),
                manufacturer="小米",
                model="LYWSD03MMC"
            )
        ]
        return devices


class HomeAssistantProtocol(BaseProtocol):
    """Home Assistant 协议适配器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.host = config.get("host", "http://localhost:8123")
        self.token = config.get("token", "")
        self.api_version = "v1"
    
    def connect(self) -> bool:
        """连接 Home Assistant"""
        try:
            import requests
            
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json"
            }
            
            response = requests.get(
                f"{self.host}/api/",
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                self.is_connected = True
                logger.info(f"[HA] 连接到 {self.host} 成功")
                return True
            
            return False
        except ImportError:
            logger.warning("[HA] requests 库未安装，使用模拟模式")
            self.is_connected = True
            return True
        except Exception as e:
            logger.error(f"[HA] 连接失败: {e}")
            return False
    
    def disconnect(self):
        self.is_connected = False
    
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """通过 HA API 发送命令"""
        if not self.is_connected:
            return False
        
        try:
            import requests
            
            action = command.get("action")
            params = command.get("params", {})
            
            # 转换为 HA 服务调用
            service_data = self._build_ha_service(action, params, device_id)
            domain = self._get_ha_domain(device_id)
            
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.post(
                f"{self.host}/api/services/{domain}",
                headers=headers,
                json=service_data,
                timeout=5
            )
            
            return response.status_code == 200
        except Exception as e:
            logger.error(f"[HA] 命令发送失败: {e}")
            return False
    
    def _build_ha_service(self, action: str, params: Dict, device_id: str) -> Dict:
        """构建 HA 服务调用"""
        if action == "on":
            return {"entity_id": device_id}
        elif action == "off":
            return {"entity_id": device_id}
        elif action == "brightness":
            return {"entity_id": device_id, "brightness": params.get("value", 255)}
        elif action == "temperature":
            return {"entity_id": device_id, "temperature": params.get("value", 26)}
        return {"entity_id": device_id}
    
    def _get_ha_domain(self, device_id: str) -> str:
        """获取 HA 域"""
        if "light" in device_id:
            return "light"
        elif "climate" in device_id or "ac" in device_id:
            return "climate"
        elif "switch" in device_id:
            return "switch"
        return "homeassistant"
    
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """查询 HA 设备状态"""
        try:
            import requests
            
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.host}/api/states/{device_id}",
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                return self._parse_ha_state(device_id, data)
        except Exception as e:
            logger.error(f"[HA] 状态查询失败: {e}")
        
        return DeviceState(device_id=device_id)
    
    def _parse_ha_state(self, device_id: str, data: Dict) -> DeviceState:
        """解析 HA 状态"""
        state = data.get("state", "unknown")
        attrs = data.get("attributes", {})
        
        return DeviceState(
            device_id=device_id,
            is_on=state == "on",
            brightness=attrs.get("brightness", 100),
            temperature=attrs.get("temperature", 26),
            raw_data=attrs
        )
    
    def discover_devices(self) -> List[DeviceInfo]:
        """发现 HA 设备"""
        try:
            import requests
            
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(
                f"{self.host}/api/states",
                headers=headers,
                timeout=10
            )
            
            if response.status_code == 200:
                states = response.json()
                devices = []
                
                for state in states:
                    entity_id = state["entity_id"]
                    device_type = self._detect_device_type(entity_id)
                    
                    if device_type != DeviceType.UNKNOWN:
                        devices.append(DeviceInfo(
                            device_id=entity_id,
                            name=state["attributes"].get("friendly_name", entity_id),
                            device_type=device_type,
                            protocol=ProtocolType.HOME_ASSISTANT,
                            capabilities=self._get_capabilities(device_type)
                        ))
                
                return devices
        except Exception as e:
            logger.error(f"[HA] 设备发现失败: {e}")
        
        return []
    
    def _detect_device_type(self, entity_id: str) -> DeviceType:
        """检测设备类型"""
        if "light" in entity_id:
            return DeviceType.LIGHT
        elif "climate" in entity_id or "ac" in entity_id or "air_conditioner" in entity_id:
            return DeviceType.AIR_CONDITIONER
        elif "media_player" in entity_id or "tv" in entity_id:
            return DeviceType.TV
        elif "sensor" in entity_id and ("temperature" in entity_id or "humidity" in entity_id):
            return DeviceType.SENSOR
        elif "switch" in entity_id:
            return DeviceType.SWITCH
        return DeviceType.UNKNOWN
    
    def _get_capabilities(self, device_type: DeviceType) -> DeviceCapability:
        """获取设备能力"""
        caps = DeviceCapability()
        
        if device_type == DeviceType.LIGHT:
            caps.can_on_off = True
            caps.can_adjust_brightness = True
        elif device_type == DeviceType.AIR_CONDITIONER:
            caps.can_on_off = True
            caps.can_adjust_temperature = True
        elif device_type == DeviceType.SENSOR:
            caps.can_sensor = True
            caps.can_query_state = True
        
        return caps


class SimulatedProtocol(BaseProtocol):
    """模拟协议（用于测试和演示）"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self._simulated_devices: Dict[str, DeviceState] = {}
    
    def connect(self) -> bool:
        self.is_connected = True
        logger.info("[模拟] 协议已连接")
        return True
    
    def disconnect(self):
        self.is_connected = False
    
    def send_command(self, device_id: str, command: Dict[str, Any]) -> bool:
        """模拟命令执行"""
        action = command.get("action")
        params = command.get("params", {})
        
        if device_id not in self._simulated_devices:
            self._simulated_devices[device_id] = DeviceState(device_id=device_id)
        
        state = self._simulated_devices[device_id]
        
        if action == "on":
            state.is_on = True
        elif action == "off":
            state.is_on = False
        elif action == "brightness":
            state.brightness = params.get("value", 100)
        elif action == "temperature":
            state.temperature = params.get("value", 26)
        elif action == "speed":
            state.speed = params.get("value", 1)
        
        # 模拟延迟
        time.sleep(0.1)
        
        self._notify_state_change(device_id, state)
        return True
    
    def query_state(self, device_id: str) -> Optional[DeviceState]:
        """返回模拟状态"""
        if device_id not in self._simulated_devices:
            self._simulated_devices[device_id] = DeviceState(device_id=device_id)
        return self._simulated_devices[device_id]
    
    def discover_devices(self) -> List[DeviceInfo]:
        """返回预设设备"""
        return [
            DeviceInfo(
                device_id="sim_light_001",
                name="模拟灯光",
                device_type=DeviceType.LIGHT,
                protocol=ProtocolType.SIMULATED,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_brightness=True
                )
            ),
            DeviceInfo(
                device_id="sim_ac_001",
                name="模拟空调",
                device_type=DeviceType.AIR_CONDITIONER,
                protocol=ProtocolType.SIMULATED,
                capabilities=DeviceCapability(
                    can_on_off=True,
                    can_adjust_temperature=True
                )
            )
        ]


class SmartHomeGateway:
    """智能家居网关 - 统一管理多种协议"""
    
    def __init__(self):
        self.protocols: Dict[ProtocolType, BaseProtocol] = {}
        self.devices: Dict[str, DeviceInfo] = {}
        self._device_state_cache: Dict[str, DeviceState] = {}
        self._lock = threading.Lock()
    
    def register_protocol(self, protocol_type: ProtocolType, protocol: BaseProtocol):
        """注册协议"""
        self.protocols[protocol_type] = protocol
        logger.info(f"[网关] 已注册协议: {protocol_type.value}")
    
    def connect_protocol(self, protocol_type: ProtocolType) -> bool:
        """连接指定协议"""
        if protocol_type not in self.protocols:
            logger.error(f"[网关] 协议未注册: {protocol_type.value}")
            return False
        
        return self.protocols[protocol_type].connect()
    
    def connect_all(self) -> Dict[ProtocolType, bool]:
        """连接所有协议"""
        results = {}
        for ptype, protocol in self.protocols.items():
            results[ptype] = protocol.connect()
        return results
    
    def discover_all_devices(self) -> List[DeviceInfo]:
        """发现所有设备"""
        all_devices = []
        
        for protocol in self.protocols.values():
            try:
                devices = protocol.discover_devices()
                all_devices.extend(devices)
                
                for device in devices:
                    self.devices[device.device_id] = device
                    logger.info(f"[网关] 发现设备: {device.name} ({device.device_id})")
            except Exception as e:
                logger.error(f"[网关] 设备发现失败 ({protocol}): {e}")
        
        return all_devices
    
    def get_device(self, device_id: str) -> Optional[DeviceInfo]:
        """获取设备信息"""
        return self.devices.get(device_id)
    
    def get_all_devices(self) -> List[DeviceInfo]:
        """获取所有设备"""
        return list(self.devices.values())
    
    def control_device(self, device_id: str, action: str, params: Dict = None) -> bool:
        """控制设备"""
        device = self.devices.get(device_id)
        if not device:
            logger.error(f"[网关] 设备不存在: {device_id}")
            return False
        
        params = params or {}
        command = {"action": action, "params": params}
        
        protocol = self.protocols.get(device.protocol)
        if not protocol:
            logger.error(f"[网关] 协议未连接: {device.protocol}")
            return False
        
        return protocol.send_command(device_id, command)
    
    def query_device_state(self, device_id: str) -> Optional[DeviceState]:
        """查询设备状态"""
        device = self.devices.get(device_id)
        if not device:
            return None
        
        protocol = self.protocols.get(device.protocol)
        if not protocol:
            return None
        
        state = protocol.query_state(device_id)
        
        if state:
            with self._lock:
                self._device_state_cache[device_id] = state
        
        return state
    
    def get_cached_state(self, device_id: str) -> Optional[DeviceState]:
        """获取缓存的设备状态"""
        with self._lock:
            return self._device_state_cache.get(device_id)
    
    def get_all_states(self) -> Dict[str, DeviceState]:
        """获取所有设备状态"""
        with self._lock:
            return dict(self._device_state_cache)
    
    def on_device_state_change(self, callback: Callable):
        """注册设备状态变化回调"""
        for protocol in self.protocols.values():
            protocol.on_state_change(callback)
    
    def get_devices_by_type(self, device_type: DeviceType) -> List[DeviceInfo]:
        """按类型获取设备"""
        return [d for d in self.devices.values() if d.device_type == device_type]
    
    def get_devices_by_room(self, room: str) -> List[DeviceInfo]:
        """按房间获取设备"""
        return [d for d in self.devices.values() if d.room == room]
    
    def get_devices_by_protocol(self, protocol: ProtocolType) -> List[DeviceInfo]:
        """按协议获取设备"""
        return [d for d in self.devices.values() if d.protocol == protocol]
    
    def execute_scene(self, scene_config: Dict[str, Any]) -> Dict[str, bool]:
        """执行场景（批量控制）"""
        results = {}
        
        for device_config in scene_config.get("devices", []):
            device_id = device_config.get("device_id")
            action = device_config.get("action")
            params = device_config.get("params", {})
            
            if device_id and action:
                result = self.control_device(device_id, action, params)
                results[device_id] = result
        
        return results


# 协议工厂
def create_protocol(protocol_type: ProtocolType, config: Dict[str, Any]) -> BaseProtocol:
    """创建协议实例"""
    protocol_map = {
        ProtocolType.MATTER: MatterProtocol,
        ProtocolType.MQTT: MQTTProtocol,
        ProtocolType.XIAOMI: XiaomiProtocol,
        ProtocolType.HOME_ASSISTANT: HomeAssistantProtocol,
        ProtocolType.SIMULATED: SimulatedProtocol,
    }
    
    protocol_class = protocol_map.get(protocol_type)
    if not protocol_class:
        raise ValueError(f"不支持的协议: {protocol_type}")
    
    return protocol_class(config)


# 使用示例
if __name__ == "__main__":
    # 创建网关
    gateway = SmartHomeGateway()
    
    # 注册并连接模拟协议
    simulated = create_protocol(ProtocolType.SIMULATED, {})
    gateway.register_protocol(ProtocolType.SIMULATED, simulated)
    gateway.connect_protocol(ProtocolType.SIMULATED)
    
    # 发现设备
    devices = gateway.discover_all_devices()
    print(f"\n发现 {len(devices)} 个设备:")
    for device in devices:
        print(f"  - {device.name} ({device.device_id}) [{device.device_type.value}]")
    
    # 控制设备
    print("\n控制设备:")
    result = gateway.control_device("sim_light_001", "on")
    print(f"  sim_light_001 on: {result}")
    
    result = gateway.control_device("sim_ac_001", "on", {"temperature": 24})
    print(f"  sim_ac_001 on with temp=24: {result}")
    
    # 查询状态
    print("\n查询状态:")
    state = gateway.query_device_state("sim_light_001")
    if state:
        print(f"  sim_light_001: is_on={state.is_on}")
    
    state = gateway.query_device_state("sim_ac_001")
    if state:
        print(f"  sim_ac_001: is_on={state.is_on}, temp={state.temperature}")
