"""治理策略引擎。

基于 YAML 配置驱动执行约束。
定义什么角色可以执行什么操作、什么时段允许什么设备。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class PolicyRule:
    """单条策略规则。"""

    name: str
    description: str = ""
    condition: Dict[str, Any] = field(default_factory=dict)
    effect: str = "allow"  # allow | deny | confirm
    priority: int = 0


class PolicyEngine:
    """轻量级策略引擎，解析 YAML 策略文件并执行决策。"""

    DEFAULT_POLICIES = """
# 默认安全策略（内嵌）
policies:
  - name: deny_high_risk_without_confirm
    description: 高风险设备（热水器≥60°C）在任何时段都需要确认
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

  - name: allow_normal_operations
    description: 普通设备在白天时段允许自动执行
    condition:
      risk_level: ["low"]
    effect: allow
    priority: 1

  - name: confirm_medium_risk
    description: 中风险设备需要确认
    condition:
      risk_level: ["medium"]
    effect: confirm
    priority: 50
"""

    def __init__(self, policy_dir: str = "config/policies"):
        self._policy_dir = Path(policy_dir)
        self._policies: List[PolicyRule] = []
        self._load_policies()

    def _load_policies(self):
        self._policies = list(yaml.safe_load(self.DEFAULT_POLICIES)["policies"])

        if self._policy_dir.exists():
            for path in sorted(self._policy_dir.glob("*.yaml")):
                try:
                    with open(path, encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                    for p in data.get("policies", []):
                        self._policies.append(PolicyRule(
                            name=p.get("name", path.stem),
                            description=p.get("description", ""),
                            condition=p.get("condition", {}),
                            effect=p.get("effect", "allow"),
                            priority=int(p.get("priority", 0)),
                        ))
                    logger.info("PolicyEngine: loaded %d policies from %s", len(data.get("policies", [])), path)
                except Exception as exc:
                    logger.warning("Failed to load policy %s: %s", path, exc)

        self._policies.sort(key=lambda p: p.get_priority(), reverse=True)
        logger.info("PolicyEngine: total %d policies loaded", len(self._policies))

    def evaluate(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """评估策略，返回决策结果。"""
        for policy in self._policies:
            if self._matches(policy, context):
                return {
                    "effect": policy.effect,
                    "policy": policy.name,
                    "description": policy.description,
                    "reason": f"matched policy: {policy.name}",
                }
        return {"effect": "allow", "policy": "default", "description": "默认允许", "reason": "no policy matched"}

    def _matches(self, policy: PolicyRule, context: Dict[str, Any]) -> bool:
        cond = policy.condition
        if not cond:
            return True

        if cond.get("rate_limited") and context.get("rate_limited"):
            return True

        device = context.get("device", "")
        if cond.get("device") and device in cond["device"]:
            return True

        risk_level = context.get("risk_level", "low")
        if risk_level in cond.get("risk_level", []):
            return True

        hour = context.get("hour", 12)
        allowed_hours = cond.get("allowed_hours")
        if allowed_hours:
            if hour not in allowed_hours:
                return True

        denied_hours = cond.get("denied_hours")
        if denied_hours and hour in denied_hours:
            return True

        return False

    def add_policy(self, policy: PolicyRule):
        self._policies.append(policy)
        self._policies.sort(key=lambda p: p.get_priority(), reverse=True)
        logger.info("PolicyEngine: added policy %s", policy.name)

    def get_policies(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": p.name,
                "description": p.description,
                "effect": p.effect,
                "priority": p.get_priority(),
            }
            for p in self._policies
        ]
