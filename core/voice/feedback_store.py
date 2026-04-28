"""Persistent feedback history for voice recognition and normalization."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from core.security import get_encrypted_storage

logger = logging.getLogger(__name__)


class VoiceFeedbackStore:
    """Sensitive voice feedback store with encrypted persistence."""

    def __init__(self, path: str = "data/voice_feedback.jsonl"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.legacy_plaintext_loaded = False
        self._storage = get_encrypted_storage()

    def add(self, record: Dict[str, object]) -> Dict[str, object]:
        payload = {
            "timestamp": datetime.now().isoformat(),
            **record,
        }
        existing = self._load_records()
        existing.append(payload)
        if self._storage.is_available():
            if self._storage.save_pickle(existing, str(self.path)):
                self.legacy_plaintext_loaded = False
        else:
            # Keep runtime behavior available, but do not write sensitive data in plaintext.
            logger.warning("VoiceFeedbackStore add skipped: encrypted storage unavailable")
            return payload
        return payload

    def recent(self, limit: int = 50) -> List[Dict[str, object]]:
        return self._load_records()[-limit:]

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

    def _load_records(self) -> List[Dict[str, object]]:
        if not self.path.exists():
            return []

        records = self._storage.load_pickle(str(self.path), default=None)
        if isinstance(records, list):
            return records

        if self._looks_like_plaintext():
            loaded: List[Dict[str, object]] = []
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        loaded.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            self.legacy_plaintext_loaded = True
            return loaded
        return []

    def _looks_like_plaintext(self) -> bool:
        try:
            with open(self.path, "rb") as handle:
                prefix = handle.read(32).lstrip()
            return prefix.startswith(b"{")
        except Exception:
            return False
