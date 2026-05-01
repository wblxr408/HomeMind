"""Runtime security chain: identity, policy, autonomy, audit context."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional

from core.governance import PolicyEngine
from core.sec.autonomy_manager import AutonomyManager


@dataclass
class IdentityContext:
    user_id: str
    session_id: str
    trust_level: str = "session"
    capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class ZeroTrustIdentityManager:
    """Explicit capability-based identity for every request."""

    DEFAULT_CAPABILITIES = ["设备控制", "场景切换", "信息查询"]

    def issue(
        self,
        user_id: str = "default",
        session_id: str = "",
        capabilities: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> IdentityContext:
        return IdentityContext(
            user_id=str(user_id or "default"),
            session_id=str(session_id or "session_default"),
            trust_level="session",
            capabilities=list(capabilities or self.DEFAULT_CAPABILITIES),
            metadata=dict(metadata or {}),
        )

    def authorize(self, identity: IdentityContext, command: Dict[str, Any], risk_level: str = "low") -> Dict[str, Any]:
        action = str(command.get("action", "")).strip()
        if action not in identity.capabilities:
            return {"allowed": False, "reason": "capability_missing"}
        if risk_level == "high" and identity.trust_level not in {"session", "verified"}:
            return {"allowed": False, "reason": "high_risk_identity_insufficient"}
        return {"allowed": True, "reason": "authorized"}


class RuntimeSecurityChain:
    """Evaluate policy, identity, anomaly frequency, and progressive autonomy."""

    def __init__(
        self,
        policy_engine: Optional[PolicyEngine] = None,
        autonomy_manager: Optional[AutonomyManager] = None,
        identity_manager: Optional[ZeroTrustIdentityManager] = None,
        anomaly_window_s: int = 60,
        anomaly_max_ops: int = 8,
    ):
        self.policy_engine = policy_engine or PolicyEngine()
        self.autonomy_manager = autonomy_manager or AutonomyManager()
        self.identity_manager = identity_manager or ZeroTrustIdentityManager()
        self.anomaly_window_s = anomaly_window_s
        self.anomaly_max_ops = anomaly_max_ops
        self._ops: Dict[str, Deque[float]] = defaultdict(deque)

    def evaluate(
        self,
        command: Dict[str, Any],
        validation,
        identity: IdentityContext,
        runtime_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        runtime_context = dict(runtime_context or {})
        risk_level = getattr(validation, "risk_level", "low")
        mode = str(runtime_context.get("mode") or identity.metadata.get("mode") or "simulated").strip().lower()
        authz = self.identity_manager.authorize(identity, command, risk_level=risk_level)
        if not authz["allowed"]:
            return {"allowed": False, "effect": "deny", "reason": authz["reason"], "identity": identity}

        policy_context = {
            "device": command.get("device", ""),
            "scene": command.get("scene", ""),
            "action": command.get("action", ""),
            "risk_level": risk_level,
            "rate_limited": getattr(validation, "rate_limited", False),
            "hour": runtime_context.get("hour", 12),
            "user_id": identity.user_id,
            "route": runtime_context.get("route", ""),
        }
        policy_result = self.policy_engine.evaluate(policy_context)
        if policy_result["effect"] == "deny":
            return {
                "allowed": False,
                "effect": "deny",
                "reason": policy_result["reason"],
                "policy": policy_result["policy"],
                "identity": identity,
            }

        anomaly = self._check_anomaly(command)
        if anomaly["detected"] and mode != "simulated":
            return {
                "allowed": False,
                "effect": "deny",
                "reason": anomaly["reason"],
                "policy": "runtime_anomaly_guard",
                "identity": identity,
            }

        device = str(command.get("device", "")).strip()
        autonomy_confirm = bool(device) and self.autonomy_manager.is_confirmation_required(device, risk_level=risk_level)
        needs_confirm = (
            getattr(validation, "requires_confirmation", False)
            or policy_result["effect"] == "confirm"
            or autonomy_confirm
        )
        if mode == "simulated":
            needs_confirm = False
        return {
            "allowed": True,
            "effect": "confirm" if needs_confirm else "allow",
            "reason": anomaly["reason"] if anomaly["detected"] and mode == "simulated" else policy_result["reason"],
            "policy": policy_result["policy"],
            "identity": identity,
            "autonomy_confirmation": autonomy_confirm,
            "advisory_only": mode == "simulated" and (policy_result["effect"] == "confirm" or anomaly["detected"] or autonomy_confirm),
        }

    def record_outcome(self, command: Dict[str, Any], success: bool, confirmed: bool = False) -> Dict[str, Any]:
        device = str(command.get("device", "")).strip()
        if not device:
            return {}
        return self.autonomy_manager.record_operation(device, success=success, confirmed=confirmed)

    def _check_anomaly(self, command: Dict[str, Any]) -> Dict[str, Any]:
        device = str(command.get("device", "")).strip()
        action = str(command.get("device_action", "") or command.get("action", "")).strip()
        if not device or not action:
            return {"detected": False, "reason": ""}
        key = f"{device}:{action}"
        now = time.time()
        queue = self._ops[key]
        cutoff = now - self.anomaly_window_s
        while queue and queue[0] < cutoff:
            queue.popleft()
        queue.append(now)
        if len(queue) > self.anomaly_max_ops:
            return {"detected": True, "reason": "runtime_anomaly_high_frequency"}
        return {"detected": False, "reason": ""}
