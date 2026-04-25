"""Persistent structured long-term preference storage for HomeMind."""

import json
import logging
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


class PreferenceStore:
    """Persist stable user preferences in structured JSON."""

    def __init__(self, path: str = "data/preferences.json"):
        self.path = path
        self.data: Dict[str, Any] = self._default_data()
        self.load()

    def _default_data(self) -> Dict[str, Any]:
        return {
            "user_id": "default",
            "devices": {},
            "scenes": {},
            "recommendation": {},
            "language": {"dialect_terms": {}},
            "updated_at": "",
        }

    def load(self) -> Dict[str, Any]:
        if not os.path.exists(self.path):
            self.data = self._default_data()
            return self.snapshot()

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            base = self._default_data()
            if isinstance(loaded, dict):
                base.update(loaded)
            if not isinstance(base.get("devices"), dict):
                base["devices"] = {}
            if not isinstance(base.get("scenes"), dict):
                base["scenes"] = {}
            if not isinstance(base.get("recommendation"), dict):
                base["recommendation"] = {}
            if not isinstance(base.get("language"), dict):
                base["language"] = {"dialect_terms": {}}
            if not isinstance(base["language"].get("dialect_terms"), dict):
                base["language"]["dialect_terms"] = {}
            self.data = base
        except Exception as exc:
            logger.warning("PreferenceStore load failed: %s", exc)
            self.data = self._default_data()
        return self.snapshot()

    def save(self) -> bool:
        self.data["updated_at"] = datetime.now().astimezone().isoformat()
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, ensure_ascii=False, indent=2)
            return True
        except Exception as exc:
            logger.warning("PreferenceStore save failed: %s", exc)
            return False

    def record_action_accept(self, decision: Dict[str, Any], context=None) -> None:
        action = str(decision.get("action", "") or "")
        device = str(decision.get("device", "") or "")
        scene = str(decision.get("scene", "") or "")
        params = dict(decision.get("params", {}) or {})

        if action == "设备控制" and device:
            device_entry = self.data.setdefault("devices", {}).setdefault(device, {})
            if device == "空调" and "temperature" in params:
                value = params["temperature"]
                if isinstance(value, (int, float)):
                    device_entry["preferred_temperature"] = int(round(float(value)))
            if device == "灯光" and "brightness" in params:
                value = params["brightness"]
                if isinstance(value, (int, float)):
                    device_entry["preferred_brightness"] = int(round(float(value)))

        if action == "场景切换" and scene:
            scene_entry = self.data.setdefault("scenes", {}).setdefault(scene, {"accept_count": 0})
            scene_entry["accept_count"] = int(scene_entry.get("accept_count", 0)) + 1
            if context is not None and hasattr(context, "hour"):
                scene_entry["preferred_hour"] = int(getattr(context, "hour"))

        self.save()

    def record_feedback(self, raw_text: str, normalized_text: str, feedback: str) -> None:
        feedback = str(feedback or "").strip()
        raw_text = str(raw_text or "").strip()
        normalized_text = str(normalized_text or "").strip()
        if feedback not in ("接受", "accepted", "纠正", "corrected"):
            return
        if not raw_text or not normalized_text or raw_text == normalized_text:
            return
        dialect_terms = self.data.setdefault("language", {}).setdefault("dialect_terms", {})
        dialect_terms[raw_text] = normalized_text
        self.save()

    def record_recommendation_feedback(self, scene: str, feedback: str) -> None:
        if not scene:
            return
        metric = f"{scene}_accept_rate"
        entry = self.data.setdefault("recommendation", {}).setdefault(metric, {"accepted": 0, "total": 0})
        entry["total"] = int(entry.get("total", 0)) + 1
        if feedback in ("接受", "accepted"):
            entry["accepted"] = int(entry.get("accepted", 0)) + 1
        self.save()

    def get_preference_boost(self, candidate_action: str, context=None) -> float:
        action = str(candidate_action or "")
        score = 0.5
        devices = self.data.get("devices", {})
        scenes = self.data.get("scenes", {})

        ac_pref = devices.get("空调", {}).get("preferred_temperature")
        if ac_pref is not None and action in ("打开空调", "调低空调温度", "调高空调温度"):
            score += 0.2

        light_pref = devices.get("灯光", {}).get("preferred_brightness")
        if light_pref is not None:
            if light_pref <= 40 and action == "调暗灯光":
                score += 0.2
            if light_pref >= 70 and action == "调亮灯光":
                score += 0.2

        scene_map = {
            "切换睡眠模式": "睡眠模式",
            "切换待客模式": "待客模式",
            "切换离家模式": "离家模式",
            "切换观影模式": "观影模式",
            "切换起床模式": "起床模式",
            "切换回家模式": "回家模式",
        }
        preferred_scene = scene_map.get(action)
        if preferred_scene:
            accept_count = int(scenes.get(preferred_scene, {}).get("accept_count", 0))
            if accept_count > 0:
                score += min(0.25, accept_count * 0.05)

        if context is not None and hasattr(context, "hour"):
            hour = int(getattr(context, "hour"))
            if preferred_scene and preferred_scene in scenes:
                preferred_hour = scenes[preferred_scene].get("preferred_hour")
                if isinstance(preferred_hour, int) and abs(preferred_hour - hour) <= 1:
                    score += 0.1

        return max(0.0, min(1.0, score))

    def get_cloud_preference_summary(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        devices = self.data.get("devices", {})
        scenes = self.data.get("scenes", {})

        ac_temp = devices.get("空调", {}).get("preferred_temperature")
        if ac_temp is not None:
            summary["preferred_ac_temp"] = ac_temp

        light_brightness = devices.get("灯光", {}).get("preferred_brightness")
        if light_brightness is not None:
            summary["preferred_light_brightness"] = light_brightness

        best_scene = ""
        best_count = -1
        for scene, meta in scenes.items():
            count = int(meta.get("accept_count", 0))
            if count > best_count:
                best_scene = scene
                best_count = count
        if best_scene:
            summary["preferred_scene"] = best_scene

        return summary

    def snapshot(self) -> Dict[str, Any]:
        return deepcopy(self.data)
