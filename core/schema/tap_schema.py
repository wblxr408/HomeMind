"""Structured TAP parsing and validation without external dependencies."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


TIME_PATTERN = re.compile(r"^\d{2}:\d{2}(:\d{2})?$")
ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-zA-Z0-9_]+$")


class TapParseError(ValueError):
    def __init__(self, message: str, line: int | None = None):
        super().__init__(message)
        self.line = line


@dataclass(slots=True)
class TapIssue:
    type: str
    message: str
    field: str | None = None
    line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"type": self.type, "message": self.message}
        if self.field is not None:
            payload["field"] = self.field
        if self.line is not None:
            payload["line"] = self.line
        return payload


@dataclass(slots=True)
class TapValidationResult:
    valid: bool
    parsed: dict[str, Any] | None = None
    errors: list[TapIssue] = field(default_factory=list)
    warnings: list[TapIssue] = field(default_factory=list)
    corrected_code: str | None = None

    def to_api_response(self) -> dict[str, Any]:
        return {
            "success": True,
            "validation": {
                "valid": self.valid,
                "errors": [item.to_dict() for item in self.errors],
                "warnings": [item.to_dict() for item in self.warnings],
                "statistics": {
                    "schemaErrors": sum(1 for item in self.errors if item.type == "schema"),
                    "deviceErrors": sum(1 for item in self.errors if item.type == "device"),
                    "parameterErrors": sum(1 for item in self.errors if item.type == "parameter"),
                    "autoFixed": self.corrected_code is not None,
                    "fixIterations": 1 if self.corrected_code is not None else 0,
                    "latencyMs": 5,
                },
            },
            "correctedCode": self.corrected_code,
            "report": "Code structure is valid. No errors found."
            if self.valid
            else f"Found {len(self.errors)} error(s) and {len(self.warnings)} warning(s).",
            "parsedRule": self.parsed,
        }


def validate_tap_rule_payload(payload: Any) -> TapValidationResult:
    if not isinstance(payload, dict):
        return TapValidationResult(valid=False, errors=[TapIssue("schema", "Rule payload must be an object")])
    rule = {
        "id": str(payload.get("id") or "").strip() or None,
        "alias": str(payload.get("alias") or "").strip(),
        "description": str(payload.get("description") or "").strip(),
        "enabled": bool(payload.get("enabled", True)),
        "trigger": payload.get("trigger"),
        "condition": payload.get("condition") or [],
        "action": payload.get("action"),
    }
    issues: list[TapIssue] = []
    warnings: list[TapIssue] = []
    _validate_rule_structure(rule, issues, warnings)
    return TapValidationResult(valid=not issues, parsed=rule if not issues else None, errors=issues, warnings=warnings)


def validate_tap_code(code: str, auto_fix: bool = True) -> dict[str, Any]:
    normalized = str(code or "")
    corrected = normalized.replace("\t", "  ").strip() if auto_fix else normalized.strip()
    warnings: list[TapIssue] = []
    if "\t" in normalized:
        warnings.append(TapIssue("style", "Tabs found; YAML should use spaces for indentation"))
    try:
        parsed = _parse_yaml_like(corrected)
        if not isinstance(parsed, dict):
            raise TapParseError("Top-level TAP document must be a mapping")
    except TapParseError as exc:
        result = TapValidationResult(
            valid=False,
            errors=[TapIssue("schema", str(exc), line=exc.line)],
            warnings=warnings,
            corrected_code=corrected if corrected != normalized else None,
        )
        return result.to_api_response()

    issues: list[TapIssue] = []
    _validate_rule_structure(parsed, issues, warnings)
    result = TapValidationResult(
        valid=not issues,
        parsed=parsed if not issues else None,
        errors=issues,
        warnings=warnings,
        corrected_code=corrected if corrected != normalized else None,
    )
    return result.to_api_response()


def _validate_rule_structure(rule: dict[str, Any], issues: list[TapIssue], warnings: list[TapIssue]) -> None:
    alias = str(rule.get("alias") or "").strip()
    if not alias:
        issues.append(TapIssue("schema", "Missing required field: alias", field="alias"))

    trigger = rule.get("trigger")
    action = rule.get("action")
    condition = rule.get("condition") or []

    if not isinstance(trigger, list) or not trigger:
        issues.append(TapIssue("schema", "trigger must be a non-empty list", field="trigger"))
    if not isinstance(action, list) or not action:
        issues.append(TapIssue("schema", "action must be a non-empty list", field="action"))
    if condition and not isinstance(condition, list):
        issues.append(TapIssue("schema", "condition must be a list", field="condition"))

    if isinstance(trigger, list):
        for index, item in enumerate(trigger):
            _validate_trigger(item, issues, warnings, index)
    if isinstance(condition, list):
        for index, item in enumerate(condition):
            _validate_condition(item, issues, warnings, index)
    if isinstance(action, list):
        for index, item in enumerate(action):
            _validate_action(item, issues, warnings, index)


def _validate_trigger(item: Any, issues: list[TapIssue], warnings: list[TapIssue], index: int) -> None:
    if not isinstance(item, dict):
        issues.append(TapIssue("schema", f"trigger[{index}] must be an object", field="trigger"))
        return
    platform = str(item.get("platform") or "").strip()
    if platform not in {"time", "numeric_state", "state", "scene"}:
        issues.append(TapIssue("schema", f"Unsupported trigger platform: {platform or 'unknown'}", field="trigger.platform"))
        return
    if platform == "time":
        if not TIME_PATTERN.match(str(item.get("at") or "")):
            issues.append(TapIssue("parameter", "time trigger requires at in HH:MM or HH:MM:SS format", field="trigger.at"))
    elif platform == "numeric_state":
        _validate_entity_id(item.get("entity_id"), warnings, issues, "trigger.entity_id")
        if item.get("above") is None and item.get("below") is None:
            issues.append(TapIssue("parameter", "numeric_state trigger requires above or below", field="trigger"))
    elif platform == "state":
        _validate_entity_id(item.get("entity_id"), warnings, issues, "trigger.entity_id")
        if item.get("to") is None and item.get("from") is None and item.get("state") is None:
            issues.append(TapIssue("parameter", "state trigger requires to/from/state", field="trigger"))
    elif platform == "scene":
        if not str(item.get("scene") or item.get("entity_id") or "").strip():
            issues.append(TapIssue("parameter", "scene trigger requires scene or entity_id", field="trigger.scene"))


def _validate_condition(item: Any, issues: list[TapIssue], warnings: list[TapIssue], index: int) -> None:
    if not isinstance(item, dict):
        issues.append(TapIssue("schema", f"condition[{index}] must be an object", field="condition"))
        return
    condition_type = str(item.get("condition") or "").strip()
    if condition_type in {"state", "numeric_state", "occupancy"}:
        _validate_entity_id(item.get("entity_id"), warnings, issues, "condition.entity_id")
    elif condition_type == "time":
        if item.get("after") is None and item.get("before") is None:
            issues.append(TapIssue("parameter", "time condition requires after or before", field="condition"))
    elif condition_type == "scene":
        if not str(item.get("scene") or item.get("state") or "").strip():
            issues.append(TapIssue("parameter", "scene condition requires scene or state", field="condition.scene"))
    elif condition_type == "template":
        if not str(item.get("value_template") or "").strip():
            issues.append(TapIssue("parameter", "template condition requires value_template", field="condition.value_template"))
    else:
        issues.append(TapIssue("schema", f"Unsupported condition type: {condition_type or 'unknown'}", field="condition.condition"))


def _validate_action(item: Any, issues: list[TapIssue], warnings: list[TapIssue], index: int) -> None:
    if not isinstance(item, dict):
        issues.append(TapIssue("schema", f"action[{index}] must be an object", field="action"))
        return
    service = str(item.get("service") or "").strip()
    if not service:
        issues.append(TapIssue("schema", "action requires service", field="action.service"))
        return
    target = item.get("target") or {}
    entity_id = None
    if isinstance(target, dict):
        entity_id = target.get("entity_id")
    if service != "notify.notify" and not entity_id and service != "scene.turn_on":
        warnings.append(TapIssue("device", "Action target.entity_id is recommended", field="action.target.entity_id"))
    if entity_id:
        _validate_entity_id(entity_id, warnings, issues, "action.target.entity_id")


def _validate_entity_id(value: Any, warnings: list[TapIssue], issues: list[TapIssue], field: str) -> None:
    entity_id = str(value or "").strip()
    if not entity_id:
        issues.append(TapIssue("schema", f"{field} is required", field=field))
        return
    if not ENTITY_ID_PATTERN.match(entity_id):
        warnings.append(TapIssue("device", f"{field} does not match the recommended domain.object_id pattern", field=field))


def _parse_yaml_like(text: str) -> Any:
    lines = []
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2:
            raise TapParseError("Indentation must use multiples of two spaces", line=line_no)
        lines.append((indent, raw_line.strip(), line_no))
    if not lines:
        raise TapParseError("Empty TAP document")
    value, index = _parse_block(lines, 0, lines[0][0])
    if index != len(lines):
        _, _, line_no = lines[index]
        raise TapParseError("Unexpected trailing content", line=line_no)
    return value


def _parse_block(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        raise TapParseError("Unexpected end of TAP document")
    current_indent, content, _ = lines[index]
    if current_indent != indent:
        raise TapParseError("Invalid indentation")
    if content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_mapping(lines, index, indent)


def _parse_mapping(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[dict[str, Any], int]:
    mapping: dict[str, Any] = {}
    while index < len(lines):
        current_indent, content, line_no = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise TapParseError("Unexpected nested indentation", line=line_no)
        if content.startswith("- "):
            break
        if ":" not in content:
            raise TapParseError("Expected key: value pair", line=line_no)
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            mapping[key] = _parse_scalar(raw_value)
            continue
        if index >= len(lines) or lines[index][0] <= current_indent:
            mapping[key] = []
            continue
        mapping[key], index = _parse_block(lines, index, current_indent + 2)
    return mapping, index


def _parse_list(lines: list[tuple[int, str, int]], index: int, indent: int) -> tuple[list[Any], int]:
    items: list[Any] = []
    while index < len(lines):
        current_indent, content, line_no = lines[index]
        if current_indent < indent:
            break
        if current_indent > indent:
            raise TapParseError("Unexpected list indentation", line=line_no)
        if not content.startswith("- "):
            break
        remainder = content[2:].strip()
        index += 1
        if not remainder:
            if index >= len(lines) or lines[index][0] <= current_indent:
                items.append({})
                continue
            value, index = _parse_block(lines, index, current_indent + 2)
            items.append(value)
            continue
        if ":" in remainder:
            key, raw_value = remainder.split(":", 1)
            item: dict[str, Any] = {key.strip(): _parse_scalar(raw_value.strip()) if raw_value.strip() else None}
            if index < len(lines) and lines[index][0] > current_indent:
                extra, index = _parse_block(lines, index, current_indent + 2)
                if isinstance(extra, dict):
                    if item[key.strip()] is None:
                        item[key.strip()] = extra
                    else:
                        item.update(extra)
                else:
                    raise TapParseError("List item mapping cannot contain non-mapping nested content", line=lines[index - 1][2])
            if item[key.strip()] is None:
                item[key.strip()] = {}
            items.append(item)
            continue
        items.append(_parse_scalar(remainder))
    return items, index


def _parse_scalar(value: str) -> Any:
    raw = value.strip()
    if not raw:
        return ""
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        return raw[1:-1]
    lowered = raw.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if re.fullmatch(r"-?\d+", raw):
        return int(raw)
    if re.fullmatch(r"-?\d+\.\d+", raw):
        return float(raw)
    return raw
