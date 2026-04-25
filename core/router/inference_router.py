"""Route user requests between local, cloud, clarification, and fallback paths."""

import re
from typing import Any, Dict, List, Optional


class InferenceRouter:
    """Route requests based on explicitness, score, and cloud availability."""

    def __init__(
        self,
        local_threshold: float = 0.85,
        cloud_threshold: float = 0.55,
        explicit_patterns: Optional[List[str]] = None,
    ):
        self.local_threshold = local_threshold
        self.cloud_threshold = cloud_threshold
        patterns = explicit_patterns or [
            r"^(打开|关闭|调高|调低|调亮|调暗|切换|查看|查询)",
            r"(睡眠模式|待客模式|离家模式|观影模式|起床模式|回家模式)",
            r"(空调|灯光|电视|风扇|窗户|音响|热水器)",
        ]
        self._explicit_patterns = [re.compile(pattern) for pattern in patterns]

    def is_explicit_command(self, text: str) -> bool:
        text = str(text or "").strip()
        if not text:
            return False
        return any(pattern.search(text) for pattern in self._explicit_patterns)

    def decide_route(
        self,
        query: str,
        ranked_candidates: List[Dict[str, Any]],
        normalized_query: str = "",
        cloud_available: bool = False,
    ) -> Dict[str, Any]:
        route_query = str(normalized_query or query or "").strip()
        if not ranked_candidates:
            return {
                "route": "clarify",
                "reason": "no_candidates",
                "top_score": 0.0,
                "top_candidates": [],
            }

        top = ranked_candidates[0]
        top_score = float(top.get("final_score", top.get("score", 0.0)) or 0.0)
        top_candidates = [item.get("action", "") for item in ranked_candidates[:3] if item.get("action")]

        if self.is_explicit_command(route_query):
            return {
                "route": "local",
                "reason": "explicit_command",
                "top_score": top_score,
                "top_candidates": top_candidates,
            }

        if top_score >= self.local_threshold:
            return {
                "route": "local",
                "reason": "high_confidence_local",
                "top_score": top_score,
                "top_candidates": top_candidates,
            }

        if top_score >= self.cloud_threshold:
            return {
                "route": "cloud" if cloud_available else "fallback",
                "reason": "mid_confidence_cloud" if cloud_available else "cloud_unavailable",
                "top_score": top_score,
                "top_candidates": top_candidates,
            }

        return {
            "route": "clarify",
            "reason": "low_confidence_clarify",
            "top_score": top_score,
            "top_candidates": top_candidates,
        }
