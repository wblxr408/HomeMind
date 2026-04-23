"""Structured validation for executable device commands."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ACTION_ALIASES = {
    "on": "on",
    "off": "off",
    "adjust": "adjust",
    "open": "open",
    "close": "close",
}


@dataclass(slots=True)
class CommandIssue:
    type: str
    message: str
    field: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"type": self.type, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        return payload


@dataclass(slots=True)
class DeviceCommandValidationResult:
    device: str
    action: str
    params: dict[str, Any] = field(default_factory=dict)
    errors: list[CommandIssue] = field(default_factory=list)
    warnings: list[CommandIssue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


def validate_device_command(device: str, payload: Any) -> DeviceCommandValidationResult:
    raw = payload if isinstance(payload, dict) else {}
    action = ACTION_ALIASES.get(str(raw.get("action", "")).strip().lower(), "")
    params = raw.get("params", {})
    result = DeviceCommandValidationResult(device=str(device or "").strip(), action=action)

    if not result.device:
        result.errors.append(CommandIssue("schema", "device is required", field="device"))
    if not action:
        result.errors.append(CommandIssue("schema", "action must be one of on/off/adjust/open/close", field="action"))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        result.errors.append(CommandIssue("schema", "params must be an object", field="params"))
        return result

    normalized_params: dict[str, Any] = {}
    for field_name, bounds in {
        "temperature": (16, 32),
        "brightness": (0, 100),
        "volume": (0, 100),
        "speed": (1, 5),
    }.items():
        if field_name not in params:
            continue
        value = params[field_name]
        if not isinstance(value, (int, float)):
            result.errors.append(CommandIssue("parameter", f"{field_name} must be numeric", field=field_name))
            continue
        low, high = bounds
        if value < low or value > high:
            result.errors.append(CommandIssue("parameter", f"{field_name} must be between {low} and {high}", field=field_name))
            continue
        normalized_params[field_name] = int(value) if field_name != "temperature" else float(value)

    for field_name in ("mode", "channel"):
        if field_name in params:
            normalized_params[field_name] = params[field_name]

    if action == "adjust" and not normalized_params:
        result.errors.append(CommandIssue("parameter", "adjust action requires at least one supported param", field="params"))

    result.params = normalized_params
    return result
