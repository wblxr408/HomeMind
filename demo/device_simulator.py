"""
家庭设备状态模拟器。

该类同时兼容：
1. 演示用设备状态容器
2. DeviceController 依赖的协议网关最小接口
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class DeviceSimulator:
    """模拟家庭设备状态。"""

    DEVICE_ID_MAP = {
        "air_conditioner": "空调",
        "light": "灯光",
        "tv": "电视",
        "water_heater": "热水器",
        "fan": "风扇",
        "speaker": "音响",
        "window": "窗户",
    }

    def __init__(self):
        self.devices: Dict[str, Dict[str, Any]] = {
            "空调": {"status": "关", "temperature": 26, "mode": "制冷"},
            "灯光": {"status": "关", "brightness": 100},
            "电视": {"status": "关", "channel": 1, "volume": 20},
            "热水器": {"status": "关", "temperature": 45},
            "风扇": {"status": "关", "speed": 2},
            "音响": {"status": "关", "volume": 30, "mode": "蓝牙"},
            "窗户": {"status": "关"},
        }

    def _resolve_device(self, device: str) -> str:
        if device in self.devices:
            return device
        return self.DEVICE_ID_MAP.get(device, device)

    def get_state(self) -> Dict[str, str]:
        return {dev: info["status"] for dev, info in self.devices.items()}

    def get_full_state(self) -> Dict[str, Dict[str, Any]]:
        return {k: v.copy() for k, v in self.devices.items()}

    def update(self, device: str, status: str, **kwargs):
        resolved = self._resolve_device(device)
        if resolved not in self.devices:
            return
        self.devices[resolved]["status"] = status
        for key, value in kwargs.items():
            self.devices[resolved][key] = value
        logger.info("Simulator device updated: %s -> %s", resolved, self.devices[resolved])

    def set_brightness(self, brightness: int):
        self.devices["灯光"]["brightness"] = brightness

    def set_temperature(self, temperature: float):
        self.devices["空调"]["temperature"] = temperature

    # Protocol gateway compatibility
    def get_device_status(self, device_id: str) -> Dict[str, Any] | None:
        resolved = self._resolve_device(device_id)
        state = self.devices.get(resolved)
        return state.copy() if state else None

    def set_device_state(self, device_id: str, state: Dict[str, Any]) -> bool:
        resolved = self._resolve_device(device_id)
        if resolved not in self.devices:
            logger.warning("Simulator device not found: %s", device_id)
            return False
        merged = self.devices[resolved].copy()
        merged.update(state or {})
        self.devices[resolved] = merged
        logger.info("Simulator gateway push: %s -> %s", resolved, merged)
        return True

    def set_device_states(self, states: Dict[str, Dict[str, Any]]) -> bool:
        ok = True
        for device_id, state in (states or {}).items():
            ok = self.set_device_state(device_id, state) and ok
        return ok

    def is_connected(self) -> bool:
        return True

    def get_status_info(self) -> Dict[str, Any]:
        return {
            "connected": True,
            "mode": "simulated",
            "deviceCount": len(self.devices),
        }
