"""
基于YAML定义的轻量级策略评估器，支持多条件匹配和优先级排序，适用于智能家居场景的安全和行为控制。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

import yaml

from core.config import POLICY_CONFIG

logger = logging.getLogger(__name__)


@dataclass
class PolicyRule:
    """Single policy rule."""

    name: str
    description: str = ""
    condition: Dict[str, Any] = field(default_factory=dict)
    effect: str = "allow"  # allow | deny | confirm
    priority: int = 0

    def get_priority(self) -> int:
        return int(self.priority or 0)


class PolicyEngine:
    """Lightweight YAML policy evaluator."""

    DEFAULT_POLICIES = """
policies:
  - name: deny_high_risk_without_confirm
    description: 高风险设备在任何时段都需要确认
    condition:
      device: ["热水器"]
      risk_level: ["high"]
    effect: confirm
    priority: 100

  - name: deny_rate_limited_operations
    description: 超过速率限制的操作直接拒绝
    condition:
      rate_limited: true
    effect: deny
    priority: 200

  - name: confirm_medium_risk
    description: 中风险设备需要确认
    condition:
      risk_level: ["medium"]
    effect: confirm
    priority: 50

  - name: allow_normal_operations
    description: 普通低风险操作默认允许
    condition:
      risk_level: ["low"]
    effect: allow
    priority: 1
"""

    def __init__(self, policy_dir: str = None):
        if policy_dir is None:
            policy_dir = POLICY_CONFIG.get("policy_dir", "config/policies")
        self._policy_dir = Path(policy_dir)
        self._policies: List[PolicyRule] = []
        self._load_policies()

    def _coerce_policy(self, raw: Dict[str, Any], fallback_name: str = "policy") -> PolicyRule:
        item = dict(raw or {})
        return PolicyRule(
            name=str(item.get("name", fallback_name)).strip(),
            description=str(item.get("description", "")).strip(),
            condition=dict(item.get("condition", {}) or {}),
            effect=str(item.get("effect", "allow")).strip() or "allow",
            priority=int(item.get("priority", 0) or 0),
        )

    def _load_policies(self) -> None:
        self._policies = [
            self._coerce_policy(item, fallback_name="default")
            for item in list(yaml.safe_load(self.DEFAULT_POLICIES).get("policies", []) or [])
        ]

        if self._policy_dir.exists():
            for path in sorted(self._policy_dir.glob("*.yaml")):
                try:
                    with open(path, encoding="utf-8") as handle:
                        data = yaml.safe_load(handle) or {}
                    loaded = [self._coerce_policy(item, fallback_name=path.stem) for item in list(data.get("policies", []) or [])]
                    self._policies.extend(loaded)
                    logger.info("PolicyEngine: loaded %d policies from %s", len(loaded), path)
                except Exception as exc:
                    logger.warning("PolicyEngine: failed to load %s: %s", path, exc)

        self._policies.sort(key=lambda item: item.get_priority(), reverse=True)
        logger.info("PolicyEngine: total %d policies loaded", len(self._policies))

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        for policy in self._policies:
            if self._matches(policy, context):
                return {
                    "effect": policy.effect,
                    "policy": policy.name,
                    "description": policy.description,
                    "reason": f"matched policy: {policy.name}",
                }
        return {
            "effect": "allow",
            "policy": "default",
            "description": "默认允许",
            "reason": "no policy matched",
        }

    def _matches(self, policy: PolicyRule, context: Dict[str, Any]) -> bool:
        cond = dict(policy.condition or {})
        if not cond:
            return True

        for field_name, expected in cond.items():
            actual = context.get(field_name)
            if field_name == "allowed_hours":
                actual = context.get("hour")
                hours = list(expected or [])
                if actual not in hours:
                    return False
                continue
            if field_name == "denied_hours":
                actual = context.get("hour")
                hours = list(expected or [])
                if actual not in hours:
                    return False
                continue
            if isinstance(expected, list):
                if actual not in expected:
                    return False
                continue
            if isinstance(expected, dict):
                minimum = expected.get("min")
                maximum = expected.get("max")
                if minimum is not None and (actual is None or actual < minimum):
                    return False
                if maximum is not None and (actual is None or actual > maximum):
                    return False
                continue
            if isinstance(expected, bool):
                if bool(actual) != expected:
                    return False
                continue
            if actual != expected:
                return False
        return True

    def add_policy(self, policy: PolicyRule) -> None:
        self._policies.append(policy)
        self._policies.sort(key=lambda item: item.get_priority(), reverse=True)
        logger.info("PolicyEngine: added policy %s", policy.name)

    def get_policies(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": item.name,
                "description": item.description,
                "effect": item.effect,
                "priority": item.get_priority(),
                "condition": dict(item.condition or {}),
            }
            for item in self._policies
        ]
