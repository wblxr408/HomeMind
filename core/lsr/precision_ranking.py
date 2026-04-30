"""
LSR: Lightweight Stage Ranking.

This module ranks BSR candidates and injects explicit follow-up actions when the
current user turn is a continuation of the previous device interaction.
"""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np


class LSRecify:
    """Lightweight ranking model for smart-home action candidates."""

    def __init__(self):
        self.weights = np.array([0.30, 0.10, 0.05, 0.20, 0.35], dtype=np.float32)
        self.bias = 0.1

    def _feature_extract(self, query: str, candidate: Dict[str, Any], context, kb=None) -> np.ndarray:
        """Extract 5 ranking features for a candidate action."""
        f1 = float(candidate.get("score", 0.5))
        f2 = (context.temperature - 15.0) / 20.0
        f3 = (context.humidity - 30.0) / 50.0
        hour_sin = np.sin(2 * np.pi * context.hour / 24.0)
        f4 = (hour_sin + 1.0) / 2.0
        f5 = kb.get_user_preference_score(candidate.get("action", ""), context) if kb is not None else 0.5
        return np.array([f1, f2, f3, f4, f5], dtype=np.float32)

    def _contains_any(self, text: str, phrases) -> bool:
        return any(phrase in text for phrase in phrases)

    def _explicit_scene_target(self, query: str) -> str:
        scene_action_map = {
            "离家模式": "切换离家模式",
            "回家模式": "切换回家模式",
            "睡眠模式": "切换睡眠模式",
            "观影模式": "切换观影模式",
            "起床模式": "切换起床模式",
            "待客模式": "切换待客模式",
            "工作模式": "切换工作模式",
            "早安模式": "切换早安模式",
            "晚归模式": "切换晚归模式",
        }
        for scene, action in scene_action_map.items():
            if scene in query:
                return action

        colloquial_scene_map = {
            ("出门", "离家", "要走了", "我走了", "准备走", "马上走"): "切换离家模式",
            ("回家", "到家", "刚回来", "回来了"): "切换回家模式",
            ("睡觉", "睡了", "困了", "睡眠"): "切换睡眠模式",
            ("观影", "看电影", "电影模式"): "切换观影模式",
            ("起床", "早安"): "切换起床模式",
            ("待客", "客人", "家里来人"): "切换待客模式",
        }
        for keywords, action in colloquial_scene_map.items():
            if self._contains_any(query, keywords):
                return action
        return ""

    def _infer_device_from_action(self, action: str) -> str:
        aliases = {
            "空调": ("空调", "冷气", "暖气"),
            "灯光": ("灯光", "灯"),
            "电视": ("电视",),
            "风扇": ("风扇",),
            "窗户": ("窗户", "窗"),
            "音响": ("音响", "音箱"),
            "热水器": ("热水器",),
        }
        for device, names in aliases.items():
            if self._contains_any(action, names):
                return device
        return ""

    def _get_last_action(self, session_store=None) -> Dict[str, Any]:
        if session_store is None:
            return {}
        try:
            return dict(session_store.get_last_action() or {})
        except Exception:
            return {}

    def _last_device_from_session(self, session_store=None) -> str:
        last_action = self._get_last_action(session_store)
        device = str(last_action.get("device", "") or "")
        if device:
            return device

        if session_store is None:
            return ""
        try:
            runtime = session_store.get_runtime_context()
        except Exception:
            runtime = {}
        last_normalized = str(runtime.get("last_normalized_input", "") or "")
        return self._infer_device_from_action(last_normalized)

    def _device_is_active(self, last_action: Dict[str, Any]) -> bool:
        return str(last_action.get("device_action", "") or "") in {"on", "adjust", "open"}

    def _resolve_air_conditioner_followup(self, query: str, last_action: Dict[str, Any]) -> str:
        active = self._device_is_active(last_action)
        cooling_phrases = (
            "有点热", "太热", "好热", "热得很", "热死", "闷", "闷热", "不够凉", "凉快点",
            "低一点", "低一些", "凉一点", "凉一些", "冷一点", "冷一些",
            "再调低", "调低一些", "再冷一点", "还是热", "还是有点热", "还是很热",
            "还是会热", "还是会很热", "仍然热", "还是觉得热",
        )
        warming_phrases = (
            "有点冷", "太冷", "好冷", "冷得很", "冻", "不够暖", "暖和点",
            "高一点", "高一些", "热一点", "热一些", "暖一点", "暖一些",
            "再调高", "调高一些", "再热一点", "还是冷", "还是有点冷", "还是很冷",
            "还是会冷", "还是会很冷", "仍然冷", "还是觉得冷",
        )
        stop_phrases = ("关空调", "关闭空调", "把空调关掉", "不用空调", "别开空调")

        if self._contains_any(query, stop_phrases):
            return "关闭空调"
        if self._contains_any(query, cooling_phrases):
            return "调低空调温度" if active else "打开空调"
        if self._contains_any(query, warming_phrases):
            return "调高空调温度" if active else "打开暖气"
        return ""

    def _resolve_light_followup(self, query: str, last_action: Dict[str, Any]) -> str:
        active = self._device_is_active(last_action)
        brighter_phrases = (
            "亮一点", "亮一些", "再亮一点", "调亮一点", "调亮一些",
            "太暗", "有点暗", "还是暗", "还是有点暗", "看不清", "不够亮",
        )
        dimmer_phrases = (
            "暗一点", "暗一些", "再暗一点", "调暗一点", "调暗一些",
            "太亮", "有点亮", "还是亮", "还是有点亮", "刺眼", "太刺眼", "太晃眼",
        )
        stop_phrases = ("关灯", "关闭灯光", "把灯关掉", "不用灯了")

        if self._contains_any(query, stop_phrases):
            return "关闭灯光"
        if self._contains_any(query, brighter_phrases):
            return "调亮灯光" if active else "打开灯光"
        if self._contains_any(query, dimmer_phrases):
            return "调暗灯光" if active else "打开灯光"
        return ""

    def _resolve_media_followup(self, query: str, last_action: Dict[str, Any]) -> str:
        device = str(last_action.get("device", "") or "")
        if device not in {"电视", "音响"}:
            return ""
        noisy_phrases = ("太吵", "有点吵", "还是吵", "还是有点吵", "声音太大", "太响", "太大声")
        stop_phrases = ("关掉", "关闭", "停掉", "不用了")
        if self._contains_any(query, noisy_phrases) or self._contains_any(query, stop_phrases):
            return f"关闭{device}"
        return ""

    def _resolve_window_followup(self, query: str, last_action: Dict[str, Any]) -> str:
        active = self._device_is_active(last_action)
        open_phrases = ("有点闷", "不透气", "开窗", "打开窗户")
        close_phrases = ("有点冷", "风太大", "关窗", "关闭窗户")
        if self._contains_any(query, open_phrases):
            return "打开窗户" if not active else ""
        if self._contains_any(query, close_phrases):
            return "关闭窗户"
        return ""

    def _resolve_fan_followup(self, query: str, last_action: Dict[str, Any]) -> str:
        active = self._device_is_active(last_action)
        open_phrases = ("有点热", "太闷", "开风扇", "打开风扇")
        close_phrases = ("有点冷", "风太大", "关风扇", "关闭风扇")
        if self._contains_any(query, open_phrases):
            return "打开风扇" if not active else ""
        if self._contains_any(query, close_phrases):
            return "关闭风扇"
        return ""

    def _resolve_follow_up_from_last_action(self, query: str, session_store=None) -> str:
        last_action = self._get_last_action(session_store)
        device = str(last_action.get("device", "") or "")
        if not device:
            device = self._last_device_from_session(session_store)
            if not device:
                return ""
            last_action = {"device": device}

        if device == "空调":
            return self._resolve_air_conditioner_followup(query, last_action)
        if device == "灯光":
            return self._resolve_light_followup(query, last_action)
        if device in {"电视", "音响"}:
            return self._resolve_media_followup(query, last_action)
        if device == "窗户":
            return self._resolve_window_followup(query, last_action)
        if device == "风扇":
            return self._resolve_fan_followup(query, last_action)
        return ""

    def _explicit_device_target(self, query: str, session_store=None) -> str:
        direct_action_map = {
            "打开灯光": "打开灯光",
            "关闭灯光": "关闭灯光",
            "调亮灯光": "调亮灯光",
            "调暗灯光": "调暗灯光",
            "打开空调": "打开空调",
            "关闭空调": "关闭空调",
            "调高空调温度": "调高空调温度",
            "调低空调温度": "调低空调温度",
            "打开电视": "打开电视",
            "关闭电视": "关闭电视",
            "打开风扇": "打开风扇",
            "关闭风扇": "关闭风扇",
            "打开窗户": "打开窗户",
            "关闭窗户": "关闭窗户",
            "打开音响": "打开音响",
            "关闭音响": "关闭音响",
            "打开热水器": "打开热水器",
            "关闭热水器": "关闭热水器",
        }
        for phrase, action in direct_action_map.items():
            if phrase in query:
                return action

        colloquial_pairs = [
            (("开灯", "开一下灯", "把灯打开", "灯打开"), "打开灯光"),
            (("关灯", "关一下灯", "把灯关掉", "灯关掉"), "关闭灯光"),
            (("开空调", "把空调打开", "空调打开"), "打开空调"),
            (("关空调", "把空调关掉", "空调关掉"), "关闭空调"),
            (("开电视", "把电视打开"), "打开电视"),
            (("关电视", "把电视关掉"), "关闭电视"),
            (("开风扇", "把风扇打开"), "打开风扇"),
            (("关风扇", "把风扇关掉"), "关闭风扇"),
            (("开窗", "把窗户打开"), "打开窗户"),
            (("关窗", "把窗户关掉"), "关闭窗户"),
            (("开音响", "把音响打开"), "打开音响"),
            (("关音响", "把音响关掉"), "关闭音响"),
        ]
        for phrases, action in colloquial_pairs:
            if self._contains_any(query, phrases):
                return action

        return self._resolve_follow_up_from_last_action(query, session_store=session_store)

    def _inject_explicit_candidate(self, candidates: List[Dict[str, Any]], explicit_action: str) -> List[Dict[str, Any]]:
        prepared = [dict(candidate) for candidate in candidates]
        if not explicit_action:
            return prepared
        if any(candidate.get("action", "") == explicit_action for candidate in prepared):
            return prepared
        prepared.append({"action": explicit_action, "source": "explicit_context", "score": 0.92})
        return prepared

    def _explicit_device_penalty(self, explicit_action: str, candidate_action: str) -> float:
        if not explicit_action or not candidate_action or explicit_action == candidate_action:
            return 0.0

        explicit_device = self._infer_device_from_action(explicit_action)
        candidate_device = self._infer_device_from_action(candidate_action)
        if explicit_device and explicit_device == candidate_device:
            return 0.35
        return 0.0

    def rank(self, query: str, candidates: List[Dict[str, Any]], context, kb=None, session_store=None) -> List[Dict[str, Any]]:
        """Rank candidates and return them in descending score order."""
        if not candidates:
            return []

        scored = []
        explicit_scene_action = self._explicit_scene_target(query)
        explicit_device_action = self._explicit_device_target(query, session_store=session_store)
        prepared_candidates = self._inject_explicit_candidate(candidates, explicit_device_action)

        for cand in prepared_candidates:
            features = self._feature_extract(query, cand, context, kb)
            score = float(np.dot(features, self.weights) + self.bias)

            action = cand.get("action", "")
            if explicit_scene_action:
                if action == explicit_scene_action:
                    score += 0.35
                elif action.startswith("切换") and action != explicit_scene_action:
                    score -= 0.35

            if explicit_device_action:
                if action == explicit_device_action:
                    score += 0.45
                else:
                    score -= self._explicit_device_penalty(explicit_device_action, action)

            score = max(0.0, min(1.0, score))
            scored_candidate = dict(cand)
            scored_candidate["final_score"] = round(score, 4)
            scored.append(scored_candidate)

        scored.sort(key=lambda item: item["final_score"], reverse=True)
        return scored

    def update_weights(self, delta_weights: np.ndarray):
        """Incrementally update the ranking weights."""
        delta = np.array(delta_weights, dtype=np.float32)
        self.weights = np.clip(self.weights + delta * 0.01, 0.0, 1.0)
