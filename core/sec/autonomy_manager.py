"""渐进式自主权管理器。

根据设备累计成功率动态调整 Agent 的自主权限。
高风险设备初期强制人工确认，熟练后逐步放权。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class DeviceAutonomyLevel:
    """单个设备的自主权状态。"""

    device: str
    total_ops: int = 0
    successful_ops: int = 0
    confirmation_count: int = 0
    autonomy_level: int = 5  # 5=完全自主, 1=每次确认
    last_updated: str = ""

    @property
    def success_rate(self) -> float:
        if self.total_ops == 0:
            return 1.0
        return self.successful_ops / self.total_ops

    def is_confirmed_required(self) -> bool:
        return self.autonomy_level < 3


class AutonomyManager:
    """管理各设备的渐进式自主权。

    策略：
    - 新设备：每次确认（autonomy_level=1）
    - 5次成功操作后提升一级（最多5级）
    - 成功率低于阈值时降级
    - 高风险设备（热水器/窗户）最多升到3级
    """

    HIGH_RISK_DEVICES = {"\u70ed\u6c34\u5668", "\u7a97\u6237"}
    MAX_AUTONOMY = 5
    HIGH_RISK_MAX_AUTONOMY = 3

    def __init__(
        self,
        success_threshold: float = 0.80,
        confirms_to_advance: int = 5,
        degrade_threshold: float = 0.60,
    ):
        self.success_threshold = success_threshold
        self.confirms_to_advance = confirms_to_advance
        self.degrade_threshold = degrade_threshold
        self._devices: Dict[str, DeviceAutonomyLevel] = {}

    def get_device_autonomy(self, device: str) -> DeviceAutonomyLevel:
        if device not in self._devices:
            self._devices[device] = DeviceAutonomyLevel(
                device=device,
                last_updated=datetime.now().astimezone().isoformat(),
            )
        return self._devices[device]

    def is_confirmation_required(self, device: str, risk_level: str = "low") -> bool:
        """判断某设备当前操作是否需要人工确认。"""
        if risk_level == "high":
            return True
        autonomy = self.get_device_autonomy(device)
        return autonomy.is_confirmed_required()

    def record_operation(
        self,
        device: str,
        success: bool,
        confirmed: bool = False,
    ) -> Dict:
        """记录一次操作结果，自动调整自主权。"""
        autonomy = self.get_device_autonomy(device)
        autonomy.total_ops += 1
        if success:
            autonomy.successful_ops += 1
        if confirmed:
            autonomy.confirmation_count += 1
        autonomy.last_updated = datetime.now().astimezone().isoformat()

        max_level = (
            self.HIGH_RISK_MAX_AUTONOMY
            if device in self.HIGH_RISK_DEVICES
            else self.MAX_AUTONOMY
        )

        old_level = autonomy.autonomy_level

        if autonomy.total_ops >= 3 and autonomy.success_rate < self.degrade_threshold:
            autonomy.autonomy_level = max(1, autonomy.autonomy_level - 1)
            logger.info("AutonomyManager: %s 成功率 %.1f%% < %.0f%%, 降级 %d→%d",
                        device, autonomy.success_rate * 100, self.degrade_threshold * 100,
                        old_level, autonomy.autonomy_level)

        elif autonomy.successful_ops >= self.confirms_to_advance and autonomy.autonomy_level < max_level:
            autonomy.autonomy_level = min(max_level, autonomy.autonomy_level + 1)
            autonomy.successful_ops = 0
            logger.info("AutonomyManager: %s 连续成功 %d 次, 升级 %d→%d",
                        device, self.confirms_to_advance, old_level, autonomy.autonomy_level)

        return {
            "device": device,
            "autonomy_level": autonomy.autonomy_level,
            "success_rate": round(autonomy.success_rate, 3),
            "total_ops": autonomy.total_ops,
            "changed": old_level != autonomy.autonomy_level,
        }

    def get_all_status(self) -> Dict[str, Dict]:
        """返回所有设备的自主权状态。"""
        return {
            device: {
                "autonomy_level": a.autonomy_level,
                "success_rate": round(a.success_rate, 3),
                "total_ops": a.total_ops,
                "last_updated": a.last_updated,
                "requires_confirmation": a.is_confirmed_required(),
            }
            for device, a in self._devices.items()
        }

    def reset_device(self, device: str) -> None:
        """重置单个设备的自主权状态。"""
        if device in self._devices:
            del self._devices[device]
            logger.info("AutonomyManager: 重置设备 %s 自主权", device)

    def load_from_dict(self, data: Dict) -> None:
        """从持久化数据恢复状态。"""
        for device, state in (data or {}).items():
            self._devices[device] = DeviceAutonomyLevel(
                device=device,
                total_ops=int(state.get("total_ops", 0)),
                successful_ops=int(state.get("successful_ops", 0)),
                confirmation_count=int(state.get("confirmation_count", 0)),
                autonomy_level=int(state.get("autonomy_level", 1)),
                last_updated=str(state.get("last_updated", "")),
            )

    def to_dict(self) -> Dict:
        """导出所有设备状态供持久化。"""
        return {
            device: {
                "total_ops": a.total_ops,
                "successful_ops": a.successful_ops,
                "confirmation_count": a.confirmation_count,
                "autonomy_level": a.autonomy_level,
                "last_updated": a.last_updated,
            }
            for device, a in self._devices.items()
        }
