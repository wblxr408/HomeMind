"""
场景模式切换工具。
"""

import logging

logger = logging.getLogger(__name__)

SCENE_CONFIGS = {
    "睡眠模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 10}},
        "空调": {"action": "adjust", "params": {"temperature": 26}},
        "电视": {"action": "off", "params": {}},
        "音响": {"action": "off", "params": {}},
    },
    "待客模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 100}},
        "空调": {"action": "adjust", "params": {"temperature": 25}},
        "音响": {"action": "on", "params": {"volume": 30, "mode": "背景音乐"}},
    },
    "离家模式": {
        "灯光": {"action": "off", "params": {}},
        "空调": {"action": "off", "params": {}},
        "电视": {"action": "off", "params": {}},
        "音响": {"action": "off", "params": {}},
    },
    "观影模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 30}},
        "空调": {"action": "adjust", "params": {"temperature": 25}},
        "电视": {"action": "on", "params": {}},
        "音响": {"action": "on", "params": {"volume": 40}},
    },
    "起床模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 80}},
        "空调": {"action": "adjust", "params": {"temperature": 24}},
        "音响": {"action": "on", "params": {"volume": 20, "mode": "闹钟"}},
    },
    "回家模式": {
        "灯光": {"action": "on", "params": {"brightness": 70}},
        "空调": {"action": "on", "params": {"temperature": 26}},
    },
    "工作模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 90}},
        "空调": {"action": "adjust", "params": {"temperature": 24}},
        "电视": {"action": "off", "params": {}},
        "音响": {"action": "off", "params": {}},
    },
    "早安模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 75}},
        "窗户": {"action": "open", "params": {}},
        "音响": {"action": "on", "params": {"volume": 18, "mode": "晨间播报"}},
        "空调": {"action": "adjust", "params": {"temperature": 24}},
    },
    "晚归模式": {
        "灯光": {"action": "on", "params": {"brightness": 60}},
        "空调": {"action": "on", "params": {"temperature": 26}},
        "音响": {"action": "off", "params": {}},
    },
}


class SceneSwitcher:
    """场景切换器，批量执行预设的多设备动作。"""

    def __init__(self, device_controller):
        self.device_ctrl = device_controller

    def execute(self, scene: str) -> str:
        config = SCENE_CONFIGS.get(scene)
        if config is None:
            return f"不支持的场景: {scene}"

        results = []
        for device, cmd in config.items():
            result = self.device_ctrl.execute(device, cmd["action"], cmd["params"])
            results.append(result)

        logger.info("Scene switch: %s, operations=%s", scene, len(results))
        return f"已切换到{scene}。" + " ".join(results)

    def switch(self, scene: str) -> str:
        return self.execute(scene)
