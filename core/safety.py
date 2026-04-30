"""Safety-sensitive smart-home intent helpers."""

from __future__ import annotations

from typing import Dict, Optional


SECURITY_SENSITIVE_TARGETS = (
    "门锁",
    "智能锁",
    "门禁",
    "防盗门",
    "入户门",
    "家门",
    "大门",
    "房门",
    "安防",
    "报警器",
    "摄像头",
    "监控",
    "燃气阀",
    "煤气阀",
)

DOOR_SECURITY_ACTIONS = (
    "锁门",
    "开门",
    "关门",
    "解锁",
    "上锁",
    "反锁",
    "开锁",
)

SAFETY_CLARIFICATION_MESSAGE = (
    "这个请求涉及门锁、安防或家庭安全设备。为避免误操作，我需要先澄清："
    "你要操作哪个具体设备、执行什么动作，以及是否确认当前环境安全？"
)


def detect_safety_sensitive_request(query: str, normalized_query: str = "") -> Optional[Dict[str, str]]:
    """Return a clarification payload for security-sensitive home commands."""
    raw_text = str(query or "").strip()
    route_text = str(normalized_query or "").strip()
    haystack = f"{raw_text} {route_text}".strip()
    if not haystack:
        return None

    for target in sorted(SECURITY_SENSITIVE_TARGETS, key=len, reverse=True):
        if target in haystack:
            return {
                "target": target,
                "message": SAFETY_CLARIFICATION_MESSAGE,
                "reason": "safety_sensitive_target",
            }

    compact = "".join(haystack.split())
    if any(action in compact for action in DOOR_SECURITY_ACTIONS) or (
        "门" in compact and any(token in compact for token in ("锁", "解锁", "上锁", "反锁", "开锁"))
    ):
        return {
            "target": "门锁",
            "message": SAFETY_CLARIFICATION_MESSAGE,
            "reason": "safety_sensitive_door_action",
        }

    return None
