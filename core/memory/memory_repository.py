from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _utc_timestamp() -> str:
    from datetime import datetime

    return datetime.utcnow().isoformat() + "Z"


class _JsonRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("[]", encoding="utf-8")

    def _load(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        return payload if isinstance(payload, list) else []

    def _save(self, rows: list[dict[str, Any]]) -> None:
        self.path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


class PreferenceRepository(_JsonRepository):
    def list(self) -> list[dict[str, Any]]:
        rows = self._load()
        rows.sort(key=lambda item: item.get("updatedAt", item.get("createdAt", "")), reverse=True)
        return rows

    def summarize(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for item in self.list():
            key = (str(item.get("scope") or "global"), str(item.get("key") or ""))
            grouped.setdefault(key, []).append(item)

        summary = []
        for (scope, key), items in grouped.items():
            latest = items[0]
            summary.append(
                {
                    "scope": scope,
                    "key": key,
                    "value": latest.get("value"),
                    "count": len(items),
                    "updatedAt": latest.get("updatedAt", latest.get("createdAt")),
                }
            )
        summary.sort(key=lambda item: item.get("updatedAt", ""), reverse=True)
        return summary

    def save_preference(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self._load()
        now = _utc_timestamp()
        record = {
            "id": str(payload.get("id") or f"pref-{int(time.time() * 1000)}"),
            "scope": str(payload.get("scope") or "global"),
            "key": str(payload.get("key") or "").strip(),
            "value": payload.get("value"),
            "source": str(payload.get("source") or "manual"),
            "scene": str(payload.get("scene") or "").strip() or None,
            "area": str(payload.get("area") or "").strip() or None,
            "device": str(payload.get("device") or "").strip() or None,
            "weight": float(payload.get("weight", 1.0) or 1.0),
            "createdAt": payload.get("createdAt") or now,
            "updatedAt": now,
        }
        rows = [item for item in rows if item.get("id") != record["id"]]
        rows.append(record)
        self._save(rows)
        return record

    def delete(self, pref_id: str) -> dict[str, Any] | None:
        rows = self._load()
        kept = []
        removed = None
        for item in rows:
            if item.get("id") == pref_id and removed is None:
                removed = item
                continue
            kept.append(item)
        if removed is not None:
            self._save(kept)
        return removed

    def clear(self) -> None:
        self._save([])


class MemoryRepository(_JsonRepository):
    def list(self, limit: int | None = None) -> list[dict[str, Any]]:
        rows = self._load()
        rows.sort(key=lambda item: item.get("updatedAt", item.get("createdAt", "")), reverse=True)
        if limit is not None:
            return rows[: max(0, int(limit))]
        return rows

    def save_memory(self, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self._load()
        now = _utc_timestamp()
        record = {
            "id": str(payload.get("id") or f"mem-{int(time.time() * 1000)}"),
            "kind": str(payload.get("kind") or "note"),
            "summary": str(payload.get("summary") or "").strip(),
            "detail": str(payload.get("detail") or "").strip(),
            "source": str(payload.get("source") or "manual"),
            "scene": str(payload.get("scene") or "").strip() or None,
            "entities": payload.get("entities") if isinstance(payload.get("entities"), list) else [],
            "pinned": bool(payload.get("pinned", False)),
            "createdAt": payload.get("createdAt") or now,
            "updatedAt": now,
        }
        rows = [item for item in rows if item.get("id") != record["id"]]
        rows.append(record)
        self._save(rows)
        return record

    def delete(self, memory_id: str) -> dict[str, Any] | None:
        rows = self._load()
        kept = []
        removed = None
        for item in rows:
            if item.get("id") == memory_id and removed is None:
                removed = item
                continue
            kept.append(item)
        if removed is not None:
            self._save(kept)
        return removed

    def clear(self) -> None:
        self._save([])
