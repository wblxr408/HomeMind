"""Persistent feedback history for voice recognition and normalization."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


class VoiceFeedbackStore:
    """Append-only JSONL store for ASR and normalization feedback."""

    def __init__(self, path: str = "data/voice_feedback.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def add(self, record: Dict[str, object]) -> Dict[str, object]:
        payload = {
            "timestamp": datetime.now().isoformat(),
            **record,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return payload

    def recent(self, limit: int = 50) -> List[Dict[str, object]]:
        if not self.path.exists():
            return []
        records = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records[-limit:]

    def find_correction(self, original_text: str) -> Optional[Dict[str, object]]:
        key = self._key(original_text)
        if not key:
            return None
        for record in reversed(self.recent(limit=200)):
            if record.get("feedback") not in ("corrected", "accepted"):
                continue
            if self._key(str(record.get("asr_text", ""))) != key:
                continue
            corrected = record.get("corrected_normalized") or record.get("normalized")
            if corrected:
                return record
        return None

    def _key(self, text: str) -> str:
        return "".join(str(text or "").lower().split())
