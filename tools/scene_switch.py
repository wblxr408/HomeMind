"""
Scene switching tool.

Scene definitions are persisted through SceneStore, while SCENE_CONFIGS remains
as a compatibility alias for validators and older imports.
"""

import logging
from typing import List, Optional

from core.automation.scene_store import DEFAULT_SCENE_CONFIGS, SceneStore

logger = logging.getLogger(__name__)

SCENE_CONFIGS = DEFAULT_SCENE_CONFIGS


class SceneSwitcher:
    """Batch execute device operations for a named scene."""

    def __init__(self, device_controller, scene_store: Optional[SceneStore] = None):
        self.device_ctrl = device_controller
        self.scene_store = scene_store or SceneStore()

    def execute(self, scene: str) -> str:
        config = self.scene_store.get_scene(scene)
        if config is None:
            return f"不支持的场景: {scene}"

        results = []
        for device, cmd in config.items():
            result = self.device_ctrl.execute(device, cmd.get("action", ""), cmd.get("params", {}))
            results.append(result)

        logger.info("场景切换: %s，执行了%s项操作", scene, len(results))
        return f"已切换到{scene}。" + " ".join(results)

    def list_scenes(self) -> List[str]:
        return self.scene_store.list_scenes()

    def switch(self, scene: str) -> str:
        """Compatibility wrapper for older callers."""
        return self.execute(scene)
