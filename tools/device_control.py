"""
设备控制工具
支持设备：空调 / 灯光 / 电视 / 热水器 / 风扇 / 音响 / 窗户
支持协议网关模式：可与真实智能家居协议集成
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DeviceController:
    """设备控制器，模拟/实际控制家庭设备"""

    def __init__(self, protocol_gateway=None):
        """
        初始化设备控制器

        Args:
            protocol_gateway: 可选的协议网关实例，用于与真实设备通信
        """
        self._gateway = protocol_gateway
        self.state: Dict[str, Dict] = {
            "空调": {"status": "关", "temperature": 26, "mode": "制冷"},
            "灯光": {"status": "关", "brightness": 100},
            "电视": {"status": "关", "channel": 1, "volume": 20},
            "热水器": {"status": "关", "temperature": 45},
            "风扇": {"status": "关", "speed": 2},
            "音响": {"status": "关", "volume": 30, "mode": "蓝牙"},
            "窗户": {"status": "关"},
        }

    def _sync_from_gateway(self):
        """从真实协议网关同步设备状态"""
        if self._gateway is None:
            return
        try:
            for device in self.state:
                try:
                    status = self._gateway.get_device_status(device)
                    if status is not None:
                        self.state[device]["status"] = status.get("status", self.state[device]["status"])
                        for key, value in status.items():
                            if key != "status" and key in self.state[device]:
                                self.state[device][key] = value
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Gateway sync failed: {e}")

    def _push_to_gateway(self, device: str, state: Dict[str, Any]):
        """推送设备状态到真实协议网关"""
        if self._gateway is None:
            return
        try:
            self._gateway.set_device_state(device, state)
        except Exception as e:
            logger.warning(f"Gateway push failed: {e}")

    def execute(self, device: str, action: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        执行设备控制
        device: 空调 / 灯光 / 电视 / 热水器 / 风扇 / 音响 / 窗户
        action: on / off / adjust
        params: 设备相关参数
        """
        if params is None:
            params = {}

        device = device.strip()
        action = action.strip()

        if device not in self.state:
            return f"不支持的设备: {device}"

        if action == "on":
            return self._turn_on(device, params)
        elif action == "off":
            return self._turn_off(device)
        elif action == "adjust":
            return self._adjust(device, params)
        elif action == "open":
            return self._turn_on(device, params)
        elif action == "close":
            return self._turn_off(device)
        else:
            return f"不支持的动作: {action}"

    def _turn_on(self, device: str, params: Dict) -> str:
        self.state[device]["status"] = "开"
        extra = []
        if device == "空调" and "temperature" in params:
            self.state[device]["temperature"] = params["temperature"]
            extra.append(f"温度{params['temperature']}°C")
        if device == "灯光" and "brightness" in params:
            self.state[device]["brightness"] = params["brightness"]
            extra.append(f"亮度{params['brightness']}%")
        if device == "电视" and "channel" in params:
            self.state[device]["channel"] = params["channel"]
        if device == "音响" and "volume" in params:
            self.state[device]["volume"] = params["volume"]
        if device == "音响" and "mode" in params:
            self.state[device]["mode"] = params["mode"]
        extra_str = "，" + "，".join(extra) if extra else ""
        logger.info(f"设备开启: {device}{extra_str}")

        # 推送到真实网关
        self._push_to_gateway(device, self.state[device])

        return f"已开启{device}{extra_str}。"

    def _turn_off(self, device: str) -> str:
        self.state[device]["status"] = "关"
        logger.info(f"设备关闭: {device}")

        # 推送到真实网关
        self._push_to_gateway(device, self.state[device])

        return f"已关闭{device}。"

    def _adjust(self, device: str, params: Dict) -> str:
        if self.state[device]["status"] == "关":
            self.state[device]["status"] = "开"
        msgs = []
        if device == "空调" and "temperature" in params:
            self.state[device]["temperature"] = params["temperature"]
            msgs.append(f"温度调整为{params['temperature']}°C")
        if device == "灯光" and "brightness" in params:
            self.state[device]["brightness"] = params["brightness"]
            msgs.append(f"亮度调整为{params['brightness']}%")
        if device == "电视" and "volume" in params:
            self.state[device]["volume"] = params["volume"]
            msgs.append(f"音量调整为{params['volume']}")
        if device == "风扇" and "speed" in params:
            self.state[device]["speed"] = params["speed"]
            msgs.append(f"风速调整为{params['speed']}档")
        msg_str = "，".join(msgs) if msgs else "已调整"
        logger.info(f"设备调节: {device} - {msg_str}")

        # 推送到真实网关
        self._push_to_gateway(device, self.state[device])

        return f"已为您{msg_str}。"

    def get_state(self, device: str) -> Dict:
        # 先尝试从网关同步
        self._sync_from_gateway()
        return self.state.get(device, {})

    def get_all_state(self) -> Dict[str, Dict]:
        # 先尝试从网关同步
        self._sync_from_gateway()
        return self.state.copy()

    def is_gateway_connected(self) -> bool:
        """检查协议网关是否已连接"""
        if self._gateway is None:
            return False
        return self._gateway.is_connected()

    def get_gateway_info(self) -> Dict[str, Any]:
        """获取网关状态信息"""
        if self._gateway is None:
            return {"connected": False, "mode": "simulated"}
        return self._gateway.get_status_info()
