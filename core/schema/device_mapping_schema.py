"""Structured validation for spatial device mappings."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


ENTITY_ID_PATTERN = re.compile(r"^[a-z0-9_]+\.[a-zA-Z0-9_]+$")


def _normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")


@dataclass(slots=True)
class ValidationIssue:
    level: str
    type: str
    message: str
    field: str | None = None
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "level": self.level,
            "type": self.type,
            "message": self.message,
        }
        if self.field is not None:
            payload["field"] = self.field
        if self.index is not None:
            payload["index"] = self.index
        return payload


@dataclass(slots=True)
class DeviceMappingRow:
    entity_id: str
    area: str
    device_type: str = "light"

    def to_tuple(self) -> list[str]:
        return [self.entity_id, self.area, self.device_type]

    def to_dict(self) -> dict[str, str]:
        return {
            "entity_id": self.entity_id,
            "area": self.area,
            "device_type": self.device_type,
        }


@dataclass(slots=True)
class DeviceMappingValidationResult:
    rows: list[DeviceMappingRow] = field(default_factory=list)
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    source_format: str = "unknown"

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def tuples(self) -> list[list[str]]:
        return [row.to_tuple() for row in self.rows]

    def to_legacy_response(self) -> dict[str, Any]:
        if self.valid:
            return {"ok": True, "tuples": self.tuples, "warnings": [item.to_dict() for item in self.warnings]}
        first_error = self.errors[0].message if self.errors else "Invalid device mapping"
        return {
            "ok": False,
            "error": first_error,
            "tuples": [],
            "errors": [item.to_dict() for item in self.errors],
            "warnings": [item.to_dict() for item in self.warnings],
        }


def _parse_root(input_data: Any, result: DeviceMappingValidationResult) -> list[Any]:
    data = input_data
    if data is None:
        result.errors.append(ValidationIssue("error", "schema", "devices is missing", field="devices"))
        return []

    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            result.errors.append(ValidationIssue("error", "schema", "Invalid JSON string for device mapping"))
            return []

    if isinstance(data, dict):
        for key in ("devices", "deviceMapping", "mappings", "items"):
            if isinstance(data.get(key), list):
                result.source_format = f"object:{key}"
                return data[key]
        result.errors.append(
            ValidationIssue(
                "error",
                "schema",
                'Expected a JSON array, or an object with "devices" / "deviceMapping" / "mappings" / "items" array',
            )
        )
        return []

    if isinstance(data, list):
        result.source_format = "list"
        return data

    result.errors.append(ValidationIssue("error", "schema", "Invalid root type for device mapping"))
    return []


def validate_device_mapping_payload(
    input_data: Any,
    *,
    supported_device_types: set[str] | None = None,
) -> DeviceMappingValidationResult:
    result = DeviceMappingValidationResult()
    data = _parse_root(input_data, result)
    if result.errors:
        return result
    if not data:
        return result

    first = data[0]
    if isinstance(first, list):
        for index, row in enumerate(data):
            if not isinstance(row, list) or len(row) < 2:
                result.errors.append(
                    ValidationIssue("error", "schema", 'Each row must be [ "entity_id", "area", "device_type" ]', index=index)
                )
                continue
            entity_id = str(row[0] or "").strip()
            area = str(row[1] or "").strip()
            device_type = _normalize_token(row[2] if len(row) > 2 else "light") or "light"
            _validate_row(result, index, entity_id, area, device_type, supported_device_types)
        return result

    if isinstance(first, dict):
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                result.errors.append(ValidationIssue("error", "schema", "Each row must be an object", index=index))
                continue
            entity_id = str(
                item.get("entity_id") or item.get("entityId") or item.get("id") or item.get("entity") or ""
            ).strip()
            area = str(item.get("area") or item.get("room") or item.get("zone") or "").strip()
            device_type = _normalize_token(item.get("device_type") or item.get("deviceType") or item.get("type") or "light") or "light"
            _validate_row(result, index, entity_id, area, device_type, supported_device_types)
        return result

    result.errors.append(ValidationIssue("error", "schema", "Unrecognized list format"))
    return result


def _validate_row(
    result: DeviceMappingValidationResult,
    index: int,
    entity_id: str,
    area: str,
    device_type: str,
    supported_device_types: set[str] | None,
) -> None:
    if not entity_id:
        result.errors.append(ValidationIssue("error", "schema", "entity_id is required", field="entity_id", index=index))
        return
    if not area:
        result.errors.append(ValidationIssue("error", "schema", "area is required", field="area", index=index))
        return
    if not ENTITY_ID_PATTERN.match(entity_id):
        result.warnings.append(
            ValidationIssue(
                "warning",
                "device",
                "entity_id does not match the recommended domain.object_id pattern",
                field="entity_id",
                index=index,
            )
        )
    normalized_type = device_type or "light"
    if supported_device_types and normalized_type not in supported_device_types:
        result.errors.append(
            ValidationIssue(
                "error",
                "device",
                f"Unsupported device type: {normalized_type}",
                field="device_type",
                index=index,
            )
        )
        return
    result.rows.append(DeviceMappingRow(entity_id=entity_id, area=area, device_type=normalized_type))
