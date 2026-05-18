"""Validate structured commands before HomeMind executes them."""

from copy import deepcopy
from typing import Any, Dict, List

from core.config import VALIDATOR_CONFIG
from core.automation.scene_store import SceneStore
from tools.scene_switch import SCENE_CONFIGS


class CommandValidator:
    """Validate device control and scene switch commands."""

    def __init__(self, scene_store: SceneStore = None):
        self.scene_store = scene_store

    DEVICE_ACTIONS = {
        "空调": {"on", "off", "adjust"},
        "灯光": {"on", "off", "adjust"},
        "电视": {"on", "off", "adjust"},
        "热水器": {"on", "off", "adjust"},
        "风扇": {"on", "off", "adjust"},
        "音响": {"on", "off", "adjust"},
        "窗户": {"on", "off", "adjust"},
    }
    VALID_ACTION_TYPES = {"设备控制", "场景切换", "信息查询"}
    PARAM_RANGES = {
        ("空调", "temperature"): (16, 30),
        ("热水器", "temperature"): (30, 75),
        ("灯光", "brightness"): (0, 100),
        ("电视", "volume"): (0, 100),
        ("音响", "volume"): (0, 100),
        ("风扇", "speed"): (1, 5),
    }

    def validate(self, command: Dict[str, Any]) -> Dict[str, Any]:
        normalized = self._normalize_command(command)
        errors: List[str] = []
        action = normalized.get("action", "")

        if action not in self.VALID_ACTION_TYPES:
            errors.append(f"不支持的动作类型: {action}")

        confidence = normalized.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
            errors.append("confidence 必须在 0 到 1 之间")

        if action == "设备控制":
            errors.extend(self._validate_device_control(normalized))
        elif action == "场景切换":
            errors.extend(self._validate_scene_switch(normalized))
        elif action == "信息查询":
            if not normalized.get("query_type"):
                errors.append("信息查询缺少 query_type")

        risk_level = self._risk_level(normalized)
        requires_confirmation = risk_level == "high"

        return {
            "valid": not errors,
            "errors": errors,
            "normalized_command": normalized,
            "risk_level": risk_level,
            "requires_confirmation": requires_confirmation,
        }

    def _normalize_command(self, command: Dict[str, Any]) -> Dict[str, Any]:
        normalized = deepcopy(command or {})
        normalized.setdefault("action", "")
        normalized.setdefault("device", "")
        normalized.setdefault("scene", "")
        normalized.setdefault("device_action", "")
        normalized.setdefault("params", {})
        normalized.setdefault("confidence", 0.0)
        normalized.setdefault("reasoning", "")
        normalized.setdefault("query_type", "")
        if not isinstance(normalized["params"], dict):
            normalized["params"] = {}

        # 归一化 action 类型：Cloud LLM 可能返回 "打开空调" 而非 "设备控制"
        action = normalized.get("action", "")
        if action not in self.VALID_ACTION_TYPES:
            # 尝试从 action 文本推断类型和参数
            fixed = self._fix_action_field(action, normalized)
            if fixed:
                normalized = fixed

        return normalized

    def _fix_action_field(self, action: str, command: Dict[str, Any]) -> Dict[str, Any] | None:
        """
        尝试修复 Cloud LLM 返回的非法 action 值。
        例如：action="打开空调" → action="设备控制", device="空调", device_action="on"
        """
        from core.llm.decision import DEVICE_ACTION_MAP, SCENE_ACTION_MAP

        # 匹配设备控制：action 字段包含设备+动作
        for key, (ctrl_type, device, device_action, params) in DEVICE_ACTION_MAP.items():
            if key in action:
                command["action"] = "设备控制"
                command["device"] = device
                command["device_action"] = device_action
                # 合并 LLM 返回的参数和已知参数
                existing_params = dict(command.get("params", {}))
                existing_params.update(params)
                command["params"] = existing_params
                return command

        # 匹配场景切换
        for key, scene in SCENE_ACTION_MAP.items():
            if key in action or scene in action:
                command["action"] = "场景切换"
                command["scene"] = scene
                return command

        return None

    def _validate_device_control(self, command: Dict[str, Any]) -> List[str]:
        errors: List[str] = []
        device = command.get("device", "")
        device_action = command.get("device_action", "")
        params = command.get("params", {})

        if device not in self.DEVICE_ACTIONS:
            errors.append(f"设备不在白名单中: {device}")
            return errors

        if device_action not in self.DEVICE_ACTIONS[device]:
            errors.append(f"{device} 不支持动作: {device_action}")

        for (range_device, key), (minimum, maximum) in self.PARAM_RANGES.items():
            if range_device != device or key not in params:
                continue
            value = params.get(key)
            if not isinstance(value, (int, float)):
                errors.append(f"{device} 参数 {key} 必须为数字")
                continue
            if value < minimum or value > maximum:
                errors.append(f"{device} 参数 {key} 超出范围: {minimum}-{maximum}")

        return errors

    def _validate_scene_switch(self, command: Dict[str, Any]) -> List[str]:
        scene = command.get("scene", "")
        dynamic_scenes = set(self.scene_store.list_scenes()) if self.scene_store else set()
        if scene not in SCENE_CONFIGS and scene not in dynamic_scenes:
            return [f"场景不在白名单中: {scene}"]
        return []

    def _risk_level(self, command: Dict[str, Any]) -> str:
        action = command.get("action", "")
        device = command.get("device", "")
        params = command.get("params", {})

        if action == "设备控制":
            risk_temp = VALIDATOR_CONFIG.get("water_heater_risk_temp", 60)
            default_temp = VALIDATOR_CONFIG.get("water_heater_default_temp", 45)
            if device == "热水器" and float(params.get("temperature", default_temp) or default_temp) >= risk_temp:
                return "high"
            if device in ("窗户", "热水器"):
                return "medium"
        return "low"
