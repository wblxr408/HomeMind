"""Persistent storage for TAP rules."""

import json
import os
import uuid
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional


class TAPRuleStore:
    """Store TAP rules in a local JSON file."""

    def __init__(self, path: str = "data/tap_rules.json"):
        self.path = path
        self.rules: List[Dict[str, Any]] = []
        self.load()

    def load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            self.rules = []
            return self.list_rules()
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            self.rules = data if isinstance(data, list) else []
        except Exception:
            self.rules = []
        return self.list_rules()

    def save(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.rules, handle, ensure_ascii=False, indent=2)
            return True
        except Exception:
            return False

    def list_rules(self) -> List[Dict[str, Any]]:
        return deepcopy(sorted(self.rules, key=lambda item: int(item.get("priority", 0)), reverse=True))

    def get_rule(self, rule_id: str) -> Optional[Dict[str, Any]]:
        for rule in self.rules:
            if rule.get("id") == rule_id:
                return deepcopy(rule)
        return None

    def add_rule(self, rule: Dict[str, Any]) -> Dict[str, Any]:
        new_rule = self._normalize_rule(rule)
        self.rules.append(new_rule)
        self.save()
        return deepcopy(new_rule)

    def update_rule(self, rule_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for index, rule in enumerate(self.rules):
            if rule.get("id") != rule_id:
                continue
            merged = deepcopy(rule)
            merged.update(updates or {})
            merged["id"] = rule_id
            merged["updated_at"] = datetime.now().astimezone().isoformat()
            self.rules[index] = self._normalize_rule(merged, preserve_timestamps=True)
            self.save()
            return deepcopy(self.rules[index])
        return None

    def delete_rule(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [rule for rule in self.rules if rule.get("id") != rule_id]
        changed = len(self.rules) != before
        if changed:
            self.save()
        return changed

    def toggle_rule(self, rule_id: str, enabled: Optional[bool] = None) -> Optional[Dict[str, Any]]:
        rule = self.get_rule(rule_id)
        if rule is None:
            return None
        new_enabled = (not bool(rule.get("enabled", True))) if enabled is None else bool(enabled)
        return self.update_rule(rule_id, {"enabled": new_enabled})

    def _normalize_rule(self, rule: Dict[str, Any], preserve_timestamps: bool = False) -> Dict[str, Any]:
        now = datetime.now().astimezone().isoformat()
        normalized = {
            "id": str(rule.get("id") or uuid.uuid4().hex[:12]),
            "name": str(rule.get("name") or "未命名规则"),
            "enabled": bool(rule.get("enabled", True)),
            "priority": int(rule.get("priority", 0)),
            "trigger": dict(rule.get("trigger", {}) or {}),
            "conditions": list(rule.get("conditions", []) or []),
            "action": dict(rule.get("action", {}) or {}),
            "created_at": rule.get("created_at") if preserve_timestamps else str(rule.get("created_at") or now),
            "updated_at": str(rule.get("updated_at") or now),
        }
        return normalized
