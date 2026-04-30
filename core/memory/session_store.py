"""Persistent short-term session state for HomeMind."""

import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional

from core.security import get_encrypted_storage

logger = logging.getLogger(__name__)


class SessionStore:
    """Persist the latest runtime context and recent turns."""

    def __init__(self, path: str = "data/session_state.json", max_recent_turns: int = 8):
        self.path = path
        self.max_recent_turns = max_recent_turns
        self.data: Dict[str, Any] = self._default_data()
        self.legacy_plaintext_loaded = False
        self._storage = get_encrypted_storage()
        self.load()

    def _default_data(self) -> Dict[str, Any]:
        return {
            "user_id": "default",
            "current_scene": "",
            "last_user_input": "",
            "last_normalized_input": "",
            "last_action": {},
            "last_clarification": {},
            "pending_clarification": {},
            "pending_confirmation": {},
            "pending_action_type": "",
            "pending_payload": {},
            "pending_feedback_target": {},
            "last_route": "local",
            "last_updated_at": "",
            "recent_turns": [],
        }

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            self.data = self._default_data()
            return self.get_runtime_context()

        try:
            loaded = self._storage.load_pickle(self.path, default=None)
            if loaded is None and self._looks_like_plaintext():
                with open(self.path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                self.legacy_plaintext_loaded = True
            base = self._default_data()
            if isinstance(loaded, dict):
                base.update(loaded)
            if not isinstance(base.get("recent_turns"), list):
                base["recent_turns"] = []
            self.data = base
        except Exception as exc:
            logger.warning("SessionStore load failed: %s", exc)
            self.data = self._default_data()
        return self.get_runtime_context()

    def save(self) -> bool:
        self.data["last_updated_at"] = datetime.now().astimezone().isoformat()
        if not self._storage.is_available():
            logger.warning("SessionStore save skipped: encrypted storage unavailable")
            return False
        ok = self._storage.save_pickle(self.data, self.path)
        if ok:
            self.legacy_plaintext_loaded = False
        return ok

    def update_from_query(self, raw_text: str, normalized_text: str = "") -> None:
        self.data["last_user_input"] = str(raw_text or "").strip()
        self.data["last_normalized_input"] = str(normalized_text or raw_text or "").strip()
        self.append_turn("user", self.data["last_user_input"])
        self.save()

    def update_from_decision(self, decision: Dict[str, Any], route: str = "local", result: str = "") -> None:
        safe_decision = {
            "type": decision.get("action", ""),
            "device": decision.get("device", ""),
            "scene": decision.get("scene", ""),
            "device_action": decision.get("device_action", ""),
            "params": dict(decision.get("params", {}) or {}),
            "confidence": decision.get("confidence", 0.0),
            "result": result,
        }
        self.data["last_action"] = safe_decision
        self.data["last_route"] = route
        scene = safe_decision.get("scene")
        if scene:
            self.data["current_scene"] = scene
        self.append_turn("assistant", result or safe_decision.get("type", ""))
        self.save()

    def update_clarification(self, question: str, answer: str = "") -> None:
        self.data["last_clarification"] = {
            "question": str(question or "").strip(),
            "answer": str(answer or "").strip(),
        }
        self.append_turn("assistant", question)
        self.save()

    def set_pending_clarification(self, kind: str, payload: Optional[Dict[str, Any]] = None) -> None:
        self.data["pending_clarification"] = {
            "kind": str(kind or "").strip(),
            "payload": deepcopy(payload or {}),
            "created_at": datetime.now().astimezone().isoformat(),
        }
        self.save()

    def get_pending_clarification(self) -> Dict[str, Any]:
        return deepcopy(self.data.get("pending_clarification", {}) or {})

    def clear_pending_clarification(self) -> None:
        self.data["pending_clarification"] = {}
        self.save()

    def set_pending_confirmation(self, action_type: str, payload: Dict[str, Any], feedback_target: Optional[Dict[str, Any]] = None) -> None:
        self.data["pending_confirmation"] = {
            "action_type": str(action_type or "").strip(),
            "payload": deepcopy(payload or {}),
        }
        self.data["pending_action_type"] = str(action_type or "").strip()
        self.data["pending_payload"] = deepcopy(payload or {})
        self.data["pending_feedback_target"] = deepcopy(feedback_target or {})
        self.save()

    def clear_pending_confirmation(self) -> None:
        self.data["pending_confirmation"] = {}
        self.data["pending_action_type"] = ""
        self.data["pending_payload"] = {}
        self.data["pending_feedback_target"] = {}
        self.save()

    def get_pending_confirmation(self) -> Dict[str, Any]:
        pending = self.data.get("pending_confirmation", {}) or {}
        if pending:
            return deepcopy(pending)
        if self.data.get("pending_action_type"):
            return {
                "action_type": self.data.get("pending_action_type", ""),
                "payload": deepcopy(self.data.get("pending_payload", {}) or {}),
            }
        return {}

    def update_scene(self, scene: str) -> None:
        self.data["current_scene"] = str(scene or "").strip()
        self.save()

    def append_turn(self, role: str, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        turns: List[Dict[str, str]] = self.data.setdefault("recent_turns", [])
        turns.append({
            "role": str(role or "system"),
            "text": text,
            "timestamp": datetime.now().astimezone().isoformat(),
        })
        if len(turns) > self.max_recent_turns:
            self.data["recent_turns"] = turns[-self.max_recent_turns:]

    def get_runtime_context(self) -> Dict[str, Any]:
        return deepcopy(self.data)

    def get_current_scene(self) -> str:
        return str(self.data.get("current_scene", "") or "")

    def get_last_action(self) -> Dict[str, Any]:
        return dict(self.data.get("last_action", {}) or {})

    def _looks_like_plaintext(self) -> bool:
        try:
            with open(self.path, "rb") as handle:
                prefix = handle.read(32).lstrip()
            return prefix.startswith(b"{")
        except Exception:
            return False
