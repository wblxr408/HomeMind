"""Route user requests between chat, execution, automation, and clarification paths."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from core.config import ROUTING_THRESHOLDS
from core.safety import detect_safety_sensitive_request

logger = logging.getLogger(__name__)


class InferenceRouter:
    """Route requests based on intent type, score, and cloud availability.

    Adaptive routing: combines rule-based thresholds with confidence-based routing.
    High-risk devices (热水器/窗户) always force clarification regardless of score.
    """

    SUPPORTED_CAPABILITY_SUMMARY = (
        "我目前支持灯光、空调、电视、音响、风扇、窗户，以及回家、离家、睡眠、观影、起床、早安、晚归等场景模式。"
    )
    UNSUPPORTED_TARGETS = {
        "\u51b0\u7bb1": "",
        "\u6295\u5f71\u4eea": "",
        "闹钟": "如果你是想早上提醒，我可以先帮你切换到起床模式。",
        "洗衣机": "",
        "扫地机器人": "",
        "咖啡机": "",
        "空气净化器": "",
        "加湿器": "",
        "门锁": "",
        "电饭煲": "",
    }
    CHAT_PATTERNS = [
        r"^(你好|您好|嗨|hello|hi)\s*[!！。.]?$",
        r"^(谢谢|thanks|thank you)\s*[!！。.]?$",
        r"^(再见|拜拜|bye)\s*[!！。.]?$",
    ]
    AUTOMATION_TIME_PATTERNS = [
        r"\d{1,2}:\d{2}",
        r"\d{1,2}点(?:半|\d{1,2}分)?",
        r"(早上|上午|中午|下午|晚上|今晚|明早|明天早上)",
        r"(节假日|元旦|春节|清明|五一|劳动节|端午|中秋|国庆|圣诞)",
    ]
    SCENE_SHORTCUTS = {
        "早安": "早安模式",
        "晚安": "睡眠模式",
        "回家": "回家模式",
    }

    HIGH_RISK_DEVICES = set()

    def __init__(
        self,
        local_threshold: float = None,
        cloud_threshold: float = None,
        explicit_patterns: Optional[List[str]] = None,
    ):
        cfg = ROUTING_THRESHOLDS
        self.local_threshold = local_threshold if local_threshold is not None else cfg["local"]
        self.cloud_threshold = cloud_threshold if cloud_threshold is not None else cfg["cloud"]
        patterns = explicit_patterns or [
            r"^(打开|关闭|调高|调低|调亮|调暗|切换|查看|查询|设置)",
            r"(睡眠模式|待客模式|离家模式|观影模式|起床模式|回家模式|工作模式|早安模式|晚归模式)",
            r"(空调|灯光|电视|风扇|窗户|音响|热水器)",
        ]
        self._explicit_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        self._chat_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self.CHAT_PATTERNS]

    def normalize_intent_text(self, text: str) -> str:
        normalized = str(text or "").strip()
        for shortcut, scene in self.SCENE_SHORTCUTS.items():
            if shortcut in normalized and scene not in normalized:
                normalized = normalized.replace(shortcut, scene)
        return normalized

    def is_explicit_command(self, text: str) -> bool:
        text = self.normalize_intent_text(text)
        if not text:
            return False
        if "\u6696\u6c14" in text:
            return True
        return any(pattern.search(text) for pattern in self._explicit_patterns)

    def is_chat_reply(self, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        if any(pattern.search(text) for pattern in self._chat_patterns):
            return True
        compact = re.sub(r"\s+", "", text.lower())
        chat_tokens = ("你好", "您好", "嗨", "hello", "hi", "谢谢", "thanks", "thankyou", "再见", "bye", "拜拜")
        return len(compact) <= 16 and any(token in compact for token in chat_tokens)

    def is_automation_request(self, text: str) -> bool:
        text = self.normalize_intent_text(text)
        if not text:
            return False
        has_time = any(re.search(pattern, text) for pattern in self.AUTOMATION_TIME_PATTERNS)
        return has_time and self.is_explicit_command(text)

    def build_chat_reply(self, query: str) -> str:
        lowered = str(query or "").strip().lower()
        if any(token in lowered for token in ("谢谢", "thanks", "thank you")):
            return "不客气，我在。"
        if any(token in lowered for token in ("再见", "bye", "拜拜")):
            return "好的，有需要随时叫我。"
        return "你好，我可以帮你控制设备、切换场景，或者创建简单定时任务。"

    def detect_unsupported_request(self, query: str, normalized_query: str = "") -> Optional[Dict[str, Any]]:
        raw_text = str(query or "").strip()
        route_text = self.normalize_intent_text(normalized_query or raw_text or "")
        haystack = f"{raw_text} {route_text}".strip()
        if not haystack:
            return None
        if detect_safety_sensitive_request(raw_text, normalized_query=route_text):
            return None

        for target in sorted(self.UNSUPPORTED_TARGETS, key=len, reverse=True):
            if not target:
                continue
            if target in haystack:
                suggestion = self.UNSUPPORTED_TARGETS.get(target, "")
                message = f"目前我还不能控制“{target}”。{self.SUPPORTED_CAPABILITY_SUMMARY}"
                if suggestion:
                    message += suggestion
                return {
                    "intent_type": "unsupported_or_ambiguous_command",
                    "route": "unsupported",
                    "reason": "unsupported_target",
                    "target": target,
                    "message": message,
                    "reply_message": message,
                    "top_candidates": [],
                    "top_score": 0.0,
                    "requires_execution": False,
                    "requires_clarification": False,
                    "requires_automation": False,
                }
        return None

    def detect_safety_sensitive_request(self, query: str, normalized_query: str = "") -> Optional[Dict[str, Any]]:
        detected = detect_safety_sensitive_request(query, normalized_query=normalized_query)
        if not detected:
            return None
        message = detected["message"]
        return {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reason": detected["reason"],
            "target": detected["target"],
            "message": message,
            "reply_message": message,
            "top_candidates": [],
            "top_score": 0.0,
            "requires_execution": False,
            "requires_clarification": True,
            "requires_automation": False,
        }

    def classify_intent(self, query: str, normalized_query: str = "") -> Dict[str, Any]:
        raw_text = str(query or "").strip()
        route_text = self.normalize_intent_text(" ".join(part for part in [raw_text, normalized_query] if part).strip())
        safety = self.detect_safety_sensitive_request(raw_text, normalized_query=route_text)
        if safety:
            return safety
        unsupported = self.detect_unsupported_request(raw_text, normalized_query=route_text)
        if unsupported:
            return unsupported

        if self.is_chat_reply(route_text):
            return {
                "intent_type": "chat_reply",
                "route": "reply",
                "reason": "chat_reply",
                "reply_message": self.build_chat_reply(route_text),
                "top_candidates": [],
                "top_score": 0.0,
                "requires_execution": False,
                "requires_clarification": False,
                "requires_automation": False,
            }

        if self.is_automation_request(route_text):
            return {
                "intent_type": "action_command",
                "route": "automation",
                "reason": "time_automation_request",
                "reply_message": "",
                "top_candidates": [],
                "top_score": 0.0,
                "requires_execution": False,
                "requires_clarification": False,
                "requires_automation": True,
            }

        if self.is_explicit_command(route_text):
            return {
                "intent_type": "action_command",
                "route": "candidate",
                "reason": "explicit_command",
                "reply_message": "",
                "top_candidates": [],
                "top_score": 0.0,
                "requires_execution": True,
                "requires_clarification": False,
                "requires_automation": False,
            }

        return {
            "intent_type": "unsupported_or_ambiguous_command",
            "route": "candidate",
            "reason": "non_explicit_candidate",
            "reply_message": "",
            "top_candidates": [],
            "top_score": 0.0,
            "requires_execution": False,
            "requires_clarification": False,
            "requires_automation": False,
        }

    def decide_route(
        self,
        query: str,
        ranked_candidates: List[Dict[str, Any]],
        normalized_query: str = "",
        cloud_available: bool = False,
    ) -> Dict[str, Any]:
        route_query = self.normalize_intent_text(
            " ".join(part for part in [str(query or "").strip(), str(normalized_query or "").strip()] if part).strip()
        )
        base_intent = self.classify_intent(query, normalized_query=route_query)
        if base_intent["route"] in {"reply", "automation", "unsupported", "clarify"}:
            return self._add_reason(base_intent, "intent_routed_before_candidates")

        if not ranked_candidates:
            return self._add_reason({
                **base_intent,
                "route": "clarify",
                "reason": "no_candidates",
                "top_score": 0.0,
                "top_candidates": [],
                "requires_execution": False,
                "requires_clarification": True,
            }, "no_candidates")

        top = ranked_candidates[0]
        bsr_score = float(top.get("score", 0.0) or 0.0)
        lsr_score = float(top.get("final_score", 0.0) or 0.0)
        top_score = (bsr_score + lsr_score) / 2.0
        top_candidates = [item.get("action", "") for item in ranked_candidates[:3] if item.get("action")]

        # 1. 高风险设备 → 强制 clarify（不看分数）
        high_risk_device = self._detect_high_risk_device(route_query)
        if high_risk_device:
            return self._add_reason({
                **base_intent,
                "route": "clarify",
                "reason": "high_risk_device",
                "high_risk_device": high_risk_device,
                "top_score": top_score,
                "top_candidates": top_candidates,
                "requires_execution": False,
                "requires_clarification": True,
            }, f"high_risk_device:{high_risk_device}")

        # 2. 显式命令 → local 快速路径
        if self.is_explicit_command(route_query):
            return self._add_reason({
                **base_intent,
                "route": "local",
                "reason": "explicit_command",
                "top_score": top_score,
                "top_candidates": top_candidates,
                "requires_execution": True,
                "requires_clarification": False,
            }, "explicit_command")

        # 3. 置信度自适应路由
        combined_score = bsr_score * 0.4 + lsr_score * 0.6
        if combined_score >= self.local_threshold:
            return self._add_reason({
                **base_intent,
                "route": "local",
                "reason": "high_confidence_local",
                "top_score": top_score,
                "combined_score": round(combined_score, 3),
                "top_candidates": top_candidates,
                "requires_execution": True,
                "requires_clarification": False,
            }, "high_confidence_local")

        if combined_score >= self.cloud_threshold:
            return self._add_reason({
                **base_intent,
                "route": "cloud" if cloud_available else "fallback",
                "reason": "mid_confidence_cloud" if cloud_available else "cloud_unavailable",
                "top_score": top_score,
                "combined_score": round(combined_score, 3),
                "top_candidates": top_candidates,
                "requires_execution": True,
                "requires_clarification": False,
            }, "mid_confidence_cloud")

        return self._add_reason({
            **base_intent,
            "route": "clarify",
            "reason": "low_confidence_clarify",
            "top_score": top_score,
            "combined_score": round(combined_score, 3),
            "top_candidates": top_candidates,
            "requires_execution": False,
            "requires_clarification": True,
        }, "low_confidence_clarify")

    def _detect_high_risk_device(self, text: str) -> Optional[str]:
        """检测是否涉及高风险设备。"""
        return None

    def _add_reason(self, result: Dict[str, Any], reason: str) -> Dict[str, Any]:
        result["routing_reason"] = reason
        return result
