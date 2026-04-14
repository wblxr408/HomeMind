"""
设备控制工具
支持设备：空调 / 灯光 / 电视 / 热水器 / 风扇 / 音响 / 窗户
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class DeviceController:
    """设备控制器，模拟/实际控制家庭设备"""

    def __init__(self):
        self.state: Dict[str, Dict] = {
            "空调": {"status": "关", "temperature": 26, "mode": "制冷"},
            "灯光": {"status": "关", "brightness": 100},
            "电视": {"status": "关", "channel": 1, "volume": 20},
            "热水器": {"status": "关", "temperature": 45},
            "风扇": {"status": "关", "speed": 2},
            "音响": {"status": "关", "volume": 30, "mode": "蓝牙"},
            "窗户": {"status": "关"},
        }

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
        return f"已开启{device}{extra_str}。"

    def _turn_off(self, device: str) -> str:
        self.state[device]["status"] = "关"
        logger.info(f"设备关闭: {device}")
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
        return f"已为您{msg_str}。"

    def get_state(self, device: str) -> Dict:
        return self.state.get(device, {})

    def get_all_state(self) -> Dict[str, Dict]:
        return self.state.copy()
