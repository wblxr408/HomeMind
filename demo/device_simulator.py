"""
家庭设备状态模拟器
演示环境使用，模拟真实家庭设备状态
"""

import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)


class DeviceSimulator:
    """模拟家庭设备状态（演示用）"""

    def __init__(self):
        self.devices: Dict[str, Dict[str, Any]] = {
            "空调":   {"status": "关", "temperature": 26, "mode": "制冷"},
            "灯光":   {"status": "关", "brightness": 100},
            "电视":   {"status": "关", "channel": 1, "volume": 20},
            "热水器": {"status": "关", "temperature": 45},
            "风扇":   {"status": "关", "speed": 2},
            "音响":   {"status": "关", "volume": 30, "mode": "蓝牙"},
            "窗户":   {"status": "关"},
        }

    def get_state(self) -> Dict[str, str]:
        """返回设备状态摘要（用于 HomeContext.devices）"""
        return {dev: info["status"] for dev, info in self.devices.items()}

    def get_full_state(self) -> Dict[str, Dict[str, Any]]:
        """返回完整设备状态"""
        return {k: v.copy() for k, v in self.devices.items()}

    def update(self, device: str, status: str, **kwargs):
        if device in self.devices:
            self.devices[device]["status"] = status
            for k, v in kwargs.items():
                self.devices[device][k] = v

    def set_brightness(self, brightness: int):
        self.devices["灯光"]["brightness"] = brightness

    def set_temperature(self, temperature: float):
        self.devices["空调"]["temperature"] = temperature
