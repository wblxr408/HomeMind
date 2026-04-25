"""Minimal TAP engine for HomeMind."""

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional


class TAPEngine:
    """Evaluate enabled TAP rules against the current context."""

    def evaluate(self, context, rules: List[Dict[str, Any]], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or datetime.now()
        matched: List[Dict[str, Any]] = []

        for rule in sorted(rules, key=lambda item: int(item.get("priority", 0)), reverse=True):
            if not rule.get("enabled", True):
                continue
            if not self._trigger_matches(rule.get("trigger", {}), context, now):
                continue
            if not self._conditions_match(rule.get("conditions", []), context):
                continue
            matched.append({
                "rule": deepcopy(rule),
                "command": self._action_to_command(rule.get("action", {})),
                "priority": int(rule.get("priority", 0)),
            })

        return self._resolve_conflicts(matched)

    def _trigger_matches(self, trigger: Dict[str, Any], context, now: datetime) -> bool:
        trigger_type = str(trigger.get("type", "")).strip()
        if trigger_type == "time":
            at = str(trigger.get("at", "")).strip()
            current = now.strftime("%H:%M")
            return bool(at) and at == current
        if trigger_type == "temperature":
            op = str(trigger.get("op", ">")).strip()
            value = float(trigger.get("value", 0))
            current = float(getattr(context, "temperature", 0.0))
            return self._compare(current, op, value)
        if trigger_type == "humidity":
            op = str(trigger.get("op", ">")).strip()
            value = float(trigger.get("value", 0))
            current = float(getattr(context, "humidity", 0.0))
            return self._compare(current, op, value)
        if trigger_type == "occupancy":
            op = str(trigger.get("op", ">")).strip()
            value = float(trigger.get("value", 0))
            current = float(getattr(context, "members_home", 0))
            return self._compare(current, op, value)
        if trigger_type == "scene":
            current_scene = getattr(context, "current_scene", "")
            return str(trigger.get("equals", "")).strip() == str(current_scene or "").strip()
        return False

    def _conditions_match(self, conditions: List[Dict[str, Any]], context) -> bool:
        for condition in conditions or []:
            condition_type = str(condition.get("type", "")).strip()
            if condition_type == "occupancy":
                current = float(getattr(context, "members_home", 0))
                if not self._compare(current, str(condition.get("op", ">")).strip(), float(condition.get("value", 0))):
                    return False
            elif condition_type == "scene":
                current_scene = str(getattr(context, "current_scene", "") or "")
                if current_scene != str(condition.get("equals", "")).strip():
                    return False
            elif condition_type == "device_status":
                devices = getattr(context, "devices", {}) or {}
                device = str(condition.get("device", "")).strip()
                expected = str(condition.get("equals", "")).strip()
                if str(devices.get(device, "")).strip() != expected:
                    return False
        return True

    def _action_to_command(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_type = str(action.get("type", "")).strip()
        if action_type == "scene_switch":
            return {
                "action": "场景切换",
                "scene": str(action.get("scene", "")).strip(),
                "device_action": "scene",
                "params": {},
                "confidence": 1.0,
                "reasoning": "TAP 自动化规则触发",
            }
        if action_type == "device_control":
            return {
                "action": "设备控制",
                "device": str(action.get("device", "")).strip(),
                "device_action": str(action.get("device_action", "")).strip(),
                "params": dict(action.get("params", {}) or {}),
                "confidence": 1.0,
                "reasoning": "TAP 自动化规则触发",
            }
        return {
            "action": "信息查询",
            "query_type": str(action.get("query_type", "status")).strip(),
            "params": dict(action.get("params", {}) or {}),
            "confidence": 1.0,
            "reasoning": "TAP 自动化规则触发",
        }

    def _resolve_conflicts(self, matched: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        accepted: List[Dict[str, Any]] = []
        seen_targets = set()
        for item in matched:
            command = item.get("command", {})
            target = self._conflict_key(command)
            if target in seen_targets:
                continue
            seen_targets.add(target)
            accepted.append(item)
        return accepted

    def _conflict_key(self, command: Dict[str, Any]) -> str:
        action = command.get("action", "")
        if action == "设备控制":
            return f"device:{command.get('device', '')}"
        if action == "场景切换":
            return "scene"
        return f"info:{command.get('query_type', '')}"

    def _compare(self, current: float, op: str, value: float) -> bool:
        if op == ">":
            return current > value
        if op == ">=":
            return current >= value
        if op == "<":
            return current < value
        if op == "<=":
            return current <= value
        if op == "==":
            return current == value
        return False
