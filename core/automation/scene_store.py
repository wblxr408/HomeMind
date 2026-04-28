"""Persistent scene configuration storage."""

import json
import os
from copy import deepcopy
from typing import Dict, List, Optional


DEFAULT_SCENE_CONFIGS = {
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
        "灯光": {"action": "adjust", "params": {"brightness": 80}},
        "空调": {"action": "adjust", "params": {"temperature": 24}},
        "音响": {"action": "on", "params": {"volume": 20}},
    },
    "晚归模式": {
        "灯光": {"action": "adjust", "params": {"brightness": 35}},
        "空调": {"action": "adjust", "params": {"temperature": 26}},
        "电视": {"action": "off", "params": {}},
        "音响": {"action": "off", "params": {}},
    },
}


class SceneStore:
    """JSON-backed scene storage with CRUD operations."""

    def __init__(self, path: str = "data/scenes.json"):
        self.path = path
        self.scenes: Dict[str, Dict] = {}
        self.load()

    def load(self) -> Dict[str, Dict]:
        if not os.path.exists(self.path):
            self.scenes = deepcopy(DEFAULT_SCENE_CONFIGS)
            self.save()
            return self.scenes
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.scenes = data if isinstance(data, dict) else deepcopy(DEFAULT_SCENE_CONFIGS)
        except Exception:
            self.scenes = deepcopy(DEFAULT_SCENE_CONFIGS)
        return self.scenes

    def save(self) -> bool:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self.scenes, handle, ensure_ascii=False, indent=2)
        return True

    def list_scenes(self) -> List[str]:
        return list(self.scenes.keys())

    def get_scene(self, name: str) -> Optional[Dict]:
        scene = self.scenes.get(name)
        return deepcopy(scene) if scene is not None else None

    def add_scene(self, name: str, config: Dict) -> Dict:
        name = str(name or "").strip()
        if not name:
            raise ValueError("scene name is required")
        self.scenes[name] = deepcopy(config or {})
        self.save()
        return self.get_scene(name) or {}

    def update_scene(self, name: str, config: Dict) -> Optional[Dict]:
        if name not in self.scenes:
            return None
        self.scenes[name] = deepcopy(config or {})
        self.save()
        return self.get_scene(name)

    def delete_scene(self, name: str) -> bool:
        if name not in self.scenes:
            return False
        del self.scenes[name]
        self.save()
        return True
