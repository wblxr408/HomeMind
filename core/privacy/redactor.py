"""Privacy-focused redaction and minimal cloud context construction."""

import re
from typing import Any, Dict, Iterable, Optional

from core.constants import SCENE_NAMES


class PrivacyRedactor:
    """Build compact, cloud-safe context summaries."""

    def __init__(self, top_k: int = 3):
        self.top_k = top_k

    def redact_text(self, text: str) -> str:
        text = str(text or "")
        text = re.sub(r"\b1\d{10}\b", "[PHONE]", text)
        text = re.sub(r"\b[\w.\-]+@[\w.\-]+\.\w+\b", "[EMAIL]", text)
        text = re.sub(r"\b\d{15,18}[0-9Xx]\b", "[ID]", text)
        return text

    def summarize_preferences(self, preference_store) -> Dict[str, Any]:
        if preference_store is None:
            return {}
        summary = preference_store.get_cloud_preference_summary()
        return dict(summary or {})

    def build_cloud_context(
        self,
        context,
        candidates: Iterable[Dict[str, Any]],
        session_store=None,
        preference_store=None,
    ) -> Dict[str, Any]:
        current_scene = getattr(context, "current_scene", "") or ""
        if not current_scene and session_store is not None:
            current_scene = session_store.get_current_scene()
        if not current_scene:
            last_scene = getattr(context, "last_scene", -1)
            current_scene = SCENE_NAMES.get(last_scene, "") if isinstance(last_scene, int) else ""

        top_candidates = []
        for item in list(candidates or [])[: self.top_k]:
            action = str(item.get("action", "")).strip()
            if action:
                top_candidates.append(action)

        payload = {
            "hour": int(getattr(context, "hour", 0)),
            "temperature": float(getattr(context, "temperature", 0.0)),
            "humidity": float(getattr(context, "humidity", 0.0)),
            "occupancy": int(getattr(context, "members_home", 0)),
            "scene": current_scene,
            "top_candidates": top_candidates,
            "preference_summary": self.summarize_preferences(preference_store),
        }
        return payload
