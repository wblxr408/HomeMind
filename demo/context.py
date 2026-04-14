"""
HomeContext 环境上下文数据类
独立模块，避免循环导入
"""

from dataclasses import dataclass, field
from typing import Dict


@dataclass
class HomeContext:
    """环境上下文状态（对应 design.md 2.2 节）"""
    hour: int = 12
    temperature: float = 26.0
    humidity: float = 50.0
    members_home: int = 1
    day_of_week: int = 0
    last_scene: int = -1
    devices: Dict[str, str] = field(default_factory=lambda: {
        "空调": "关", "灯光": "关", "电视": "关",
        "热水器": "关", "音响": "关", "窗户": "关", "风扇": "关",
    })

    def to_state_dict(self) -> Dict:
        return {
            "hour": self.hour,
            "temperature": self.temperature,
            "humidity": self.humidity,
            "members_home": self.members_home,
            "day_of_week": self.day_of_week,
            "last_scene": self.last_scene,
        }
