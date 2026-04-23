"""Minimal TAP engine used as a supplement to the main dialogue pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


ActionExecutor = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class RuleExecution:
    rule_id: str
    alias: str
    action_results: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ruleId": self.rule_id,
            "alias": self.alias,
            "actionResults": self.action_results,
        }


@dataclass(slots=True)
class TapEvaluationResult:
    matched_rules: list[str] = field(default_factory=list)
    skipped_rules: list[dict[str, Any]] = field(default_factory=list)
    executed_rules: list[RuleExecution] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "matchedRules": self.matched_rules,
            "skippedRules": self.skipped_rules,
            "executedRules": [item.to_dict() for item in self.executed_rules],
        }


class TapRuleEngine:
    """Evaluate lightweight TAP rules against events and snapshots."""

    def evaluate(
        self,
        rules: list[dict[str, Any]],
        event: dict[str, Any],
        snapshot: dict[str, Any],
        executor: ActionExecutor | None = None,
    ) -> TapEvaluationResult:
        result = TapEvaluationResult()
        for rule in rules:
            if not rule.get("enabled", True):
                result.skipped_rules.append({"ruleId": rule.get("id"), "reason": "disabled"})
                continue
            if not self._matches_trigger(rule.get("trigger") or [], event):
                continue
            result.matched_rules.append(rule.get("id") or "")
            if not self._conditions_match(rule.get("condition") or [], snapshot):
                result.skipped_rules.append({"ruleId": rule.get("id"), "reason": "condition_not_met"})
                continue
            action_results = []
            for action in rule.get("action") or []:
                action_results.append(executor(action) if executor else {"status": "planned", "action": action})
            result.executed_rules.append(
                RuleExecution(
                    rule_id=str(rule.get("id") or ""),
                    alias=str(rule.get("alias") or ""),
                    action_results=action_results,
                )
            )
        return result

    def _matches_trigger(self, triggers: list[dict[str, Any]], event: dict[str, Any]) -> bool:
        for trigger in triggers:
            platform = trigger.get("platform")
            if platform == "time" and event.get("platform") == "time":
                if str(trigger.get("at")) == str(event.get("at")):
                    return True
            elif platform == "scene" and event.get("platform") == "scene":
                trigger_scene = str(trigger.get("scene") or trigger.get("entity_id") or "")
                if trigger_scene and trigger_scene == str(event.get("scene") or event.get("entity_id") or ""):
                    return True
            elif platform == "state" and event.get("platform") == "state":
                if str(trigger.get("entity_id")) != str(event.get("entity_id")):
                    continue
                expected_to = trigger.get("to", trigger.get("state"))
                expected_from = trigger.get("from")
                if expected_to is not None and str(expected_to) != str(event.get("to")):
                    continue
                if expected_from is not None and str(expected_from) != str(event.get("from")):
                    continue
                return True
            elif platform == "numeric_state" and event.get("platform") == "numeric_state":
                if str(trigger.get("entity_id")) != str(event.get("entity_id")):
                    continue
                value = event.get("value")
                if value is None:
                    continue
                if not self._numeric_match(value, trigger.get("above"), trigger.get("below")):
                    continue
                return True
        return False

    def _conditions_match(self, conditions: list[dict[str, Any]], snapshot: dict[str, Any]) -> bool:
        for condition in conditions:
            condition_type = condition.get("condition")
            if condition_type == "state":
                actual = self._lookup_entity_state(snapshot, str(condition.get("entity_id") or ""))
                if actual != str(condition.get("state")):
                    return False
            elif condition_type == "numeric_state":
                actual = self._lookup_numeric_value(snapshot, str(condition.get("entity_id") or ""))
                if actual is None or not self._numeric_match(actual, condition.get("above"), condition.get("below")):
                    return False
            elif condition_type == "scene":
                expected = str(condition.get("scene") or condition.get("state") or "")
                if str(snapshot.get("context", {}).get("scene") or "") != expected:
                    return False
            elif condition_type == "occupancy":
                actual = self._lookup_numeric_value(snapshot, str(condition.get("entity_id") or "occupancy"))
                if actual is None or not self._numeric_match(actual, condition.get("above"), condition.get("below")):
                    return False
            elif condition_type == "time":
                now_value = str(snapshot.get("context", {}).get("time") or datetime.now().strftime("%H:%M:%S"))
                if not self._time_match(now_value, condition.get("after"), condition.get("before")):
                    return False
            elif condition_type == "template":
                template = str(condition.get("value_template") or "").strip().lower()
                if template not in {"{{ true }}", "{{true}}", "true"}:
                    return False
        return True

    def _lookup_entity_state(self, snapshot: dict[str, Any], entity_id: str) -> str | None:
        if entity_id == "group.family":
            occupancy = snapshot.get("context", {}).get("occupancy")
            return "home" if occupancy and float(occupancy) > 0 else "away"
        if entity_id == "scene.current":
            return str(snapshot.get("context", {}).get("scene") or "")
        device_state = snapshot.get("devices", {}).get(entity_id)
        if isinstance(device_state, dict):
            return "on" if device_state.get("is_on") else "off"
        if device_state is not None:
            return str(device_state)
        return None

    def _lookup_numeric_value(self, snapshot: dict[str, Any], entity_id: str) -> float | None:
        context = snapshot.get("context", {})
        if entity_id in {"sensor.indoor_temperature", "temperature"}:
            value = context.get("temperature")
        elif entity_id in {"sensor.indoor_humidity", "humidity"}:
            value = context.get("humidity")
        elif entity_id in {"sensor.occupancy", "occupancy"}:
            value = context.get("occupancy")
        else:
            device_state = snapshot.get("devices", {}).get(entity_id, {})
            if isinstance(device_state, dict):
                for key in ("temperature", "brightness", "volume", "speed"):
                    if key in device_state:
                        value = device_state[key]
                        break
                else:
                    value = None
            else:
                value = None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _numeric_match(value: float, above: Any, below: Any) -> bool:
        if above is not None and float(value) <= float(above):
            return False
        if below is not None and float(value) >= float(below):
            return False
        return True

    @staticmethod
    def _time_match(now_value: str, after: Any, before: Any) -> bool:
        normalized_now = now_value[:8]
        if after is not None and normalized_now < str(after):
            return False
        if before is not None and normalized_now > str(before):
            return False
        return True
