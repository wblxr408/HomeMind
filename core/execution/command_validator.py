"""Validate structured commands before HomeMind executes them."""

from __future__ import annotations

import time
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.automation.scene_store import SceneStore
from tools.scene_switch import SCENE_CONFIGS


@dataclass
class ValidationResult:
    valid: bool
    errors: List[str]
    normalized_command: Dict[str, Any]
    risk_level: str  # low | medium | high
    requires_confirmation: bool
    warnings: List[str] = field(default_factory=list)
    rate_limited: bool = False
    confidence: float = 0.0


class CommandRateLimiter:
    """Per-device rate limiter to prevent rapid-fire commands."""

    def __init__(self, window_s: int = 30, max_ops: int = 5):
        self.window_s = window_s
        self.max_ops = max_ops
        self._ops: Dict[str, List[float]] = defaultdict(list)

    def check(self, device: str, device_action: str) -> Tuple[bool, str]:
        key = f"{device}:{device_action}"
        now = time.time()
        cutoff = now - self.window_s
        self._ops[key] = [t for t in self._ops[key] if t > cutoff]
        if len(self._ops[key]) >= self.max_ops:
            return False, f"设备 {device} 在 {self.window_s}s 内操作过于频繁"
        self._ops[key].append(now)
        return True, ""

    def reset(self):
        self._ops.clear()


class CommandValidator:
    """Validate device control and scene switch commands."""

    # 安全类设备：任何操作都需要确认
    HIGH_RISK_DEVICES = {"热水器", "窗户"}

    DEVICE_ACTIONS = {
        "空调": {"on", "off", "adjust"},
        "灯光": {"on", "off", "adjust"},
        "电视": {"on", "off", "adjust"},
        "热水器": {"on", "off", "adjust"},
        "风扇": {"on", "off", "adjust"},
        "音响": {"on", "off", "adjust"},
        "窗户": {"open", "close"},
    }
    VALID_ACTION_TYPES = {"设备控制", "场景切换", "信息查询"}

    # 扩展参数边界
    PARAM_RANGES = {
        ("空调", "temperature"): (16, 30),
        ("热水器", "temperature"): (30, 75),
        ("灯光", "brightness"): (0, 100),
        ("电视", "volume"): (0, 100),
        ("音响", "volume"): (0, 100),
        ("风扇", "speed"): (1, 5),
        ("空调", "humidity"): (30, 90),
    }

    def __init__(self, scene_store: SceneStore = None, rate_limit_window_s: int = 30, rate_limit_max_ops: int = 5):
        self.scene_store = scene_store
        self._rate_limiter = CommandRateLimiter(
            window_s=rate_limit_window_s,
            max_ops=rate_limit_max_ops,
        )

    def validate(self, command: Dict[str, Any]) -> ValidationResult:
        normalized = self._normalize_command(command)
        errors: List[str] = []
        warnings: List[str] = []
        action = normalized.get("action", "")

        if action not in self.VALID_ACTION_TYPES:
            errors.append(f"不支持的动作类型: {action}")

        confidence = normalized.get("confidence", 0.0)
        if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
            errors.append("confidence 必须在 0 到 1 之间")

        if action == "设备控制":
            errs, warns = self._validate_device_control(normalized)
            errors.extend(errs)
            warnings.extend(warns)
        elif action == "场景切换":
            errors.extend(self._validate_scene_switch(normalized))
        elif action == "信息查询":
            if not normalized.get("query_type"):
                errors.append("信息查询缺少 query_type")

        risk_level = self._risk_level(normalized)
        requires_confirmation = risk_level == "high"

        # 速率限制检查（仅针对设备控制）
        rate_limited = False
        if action == "设备控制" and not errors:
            device = normalized.get("device", "")
            device_action = normalized.get("device_action", "")
            ok, msg = self._rate_limiter.check(device, device_action)
            if not ok:
                errors.append(msg)
                rate_limited = True

        return ValidationResult(
            valid=not errors,
            errors=errors,
            normalized_command=normalized,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            warnings=warnings,
            rate_limited=rate_limited,
            confidence=confidence,
        )

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
        return normalized

    def _validate_device_control(self, command: Dict[str, Any]) -> Tuple[List[str], List[str]]:
        errors: List[str] = []
        warnings: List[str] = []
        device = command.get("device", "")
        device_action = command.get("device_action", "")
        params = command.get("params", {})

        if device not in self.DEVICE_ACTIONS:
            errors.append(f"设备不在白名单中: {device}")
            return errors, warnings

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
                errors.append(f"{device} 参数 {key} 超出范围 [{minimum}, {maximum}]: 当前值 {value}")
            elif value < minimum + 2:
                warnings.append(f"{device} 参数 {key}={value} 接近下限 {minimum}，请确认")

        # 高温热水器警告
        if device == "热水器":
            temp = params.get("temperature")
            if isinstance(temp, (int, float)) and temp >= 60:
                warnings.append(f"热水器温度设为 {temp}°C，可能造成烫伤风险")

        return errors, warnings

    def _validate_scene_switch(self, command: Dict[str, Any]) -> List[str]:
        scene = command.get("scene", "")
        # 优先使用 scene_store（真实存储），fallback 到硬编码白名单
        if self.scene_store is not None:
            dynamic_scenes = set(self.scene_store.list_scenes())
            if scene not in SCENE_CONFIGS and scene not in dynamic_scenes:
                return [f"场景不在白名单中: {scene}"]
        else:
            if scene not in SCENE_CONFIGS:
                return [f"场景不在白名单中: {scene}"]
        return []

    def _risk_level(self, command: Dict[str, Any]) -> str:
        action = command.get("action", "")
        device = command.get("device", "")
        params = command.get("params", {})

        if action == "设备控制":
            # 高温热水器在任何温度下都是中高风险
            if device == "热水器":
                temp = float(params.get("temperature", 45) or 45)
                if temp >= 60:
                    return "high"
                return "medium"
            # 窗户开关是中风险
            if device in ("窗户",):
                return "medium"
        return "low"

    def set_scene_store(self, store: SceneStore) -> None:
        """支持运行时注入 scene_store，解决 __init__ 时未传入的问题。"""
        self.scene_store = store
