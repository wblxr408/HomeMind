"""
Normalize English, Mandarin, and common colloquial/dialect expressions into
HomeMind's standard Chinese command phrases.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class NormalizedQuery:
    original: str
    normalized: str
    language: str = "unknown"
    confidence: float = 0.0
    matched_rule: str = ""
    extra_candidates: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "original": self.original,
            "normalized": self.normalized,
            "language": self.language,
            "confidence": self.confidence,
            "matched_rule": self.matched_rule,
            "extra_candidates": self.extra_candidates,
        }


class LanguageNormalizer:
    """Rule-based normalization tuned for smart-home short commands."""

    def __init__(self, feedback_store=None):
        self._rules = self._build_rules()
        self.feedback_store = feedback_store

    def normalize(self, text: str, language: str = "auto") -> NormalizedQuery:
        original = (text or "").strip()
        if not original:
            return NormalizedQuery(original="", normalized="", language=language, confidence=0.0)

        detected = self._detect_language(original, language)
        feedback_match = self._lookup_feedback(original)
        if feedback_match:
            normalized = str(feedback_match.get("corrected_normalized") or feedback_match.get("normalized"))
            return NormalizedQuery(
                original=original,
                normalized=normalized,
                language=detected,
                confidence=0.98,
                matched_rule="voice_feedback_history",
            )

        comparable = self._compact(original.lower())
        for rule in self._rules:
            if rule["language"] not in (detected, "any"):
                continue
            if rule["pattern"].search(comparable):
                return NormalizedQuery(
                    original=original,
                    normalized=rule["normalized"],
                    language=detected,
                    confidence=rule["confidence"],
                    matched_rule=rule["name"],
                    extra_candidates=list(rule.get("extra_candidates", [])),
                )

        return NormalizedQuery(
            original=original,
            normalized=original,
            language=detected,
            confidence=0.5,
            matched_rule="passthrough",
        )

    def _detect_language(self, text: str, language: str) -> str:
        if language in ("zh", "en"):
            return language
        if re.search(r"[a-zA-Z]", text):
            return "en"
        if re.search(r"[\u4e00-\u9fff]", text):
            return "zh"
        return "unknown"

    def _compact(self, text: str) -> str:
        return re.sub(r"[\s,.;:!?，。！？、]+", "", text)

    def _lookup_feedback(self, original: str) -> Optional[Dict[str, object]]:
        if not self.feedback_store:
            return None
        try:
            return self.feedback_store.find_correction(original)
        except Exception:
            return None

    def _build_rules(self) -> List[Dict[str, object]]:
        raw_rules = [
            ("en_open_ac", "en", r"(turn|switch|power)?on(the)?(airconditioner|ac|a/c)", "打开空调", 0.95, []),
            ("en_close_ac", "en", r"(turn|switch|power)?off(the)?(airconditioner|ac|a/c)", "关闭空调", 0.95, []),
            ("en_cooler", "en", r"(makeit)?(cooler|toohot|hot|cooldown)", "太热了", 0.84, ["打开空调", "打开风扇", "打开窗户"]),
            ("en_warmer", "en", r"(makeit)?(warmer|toocold|cold|warmup)", "太冷了", 0.84, ["打开暖气", "调高空调温度"]),
            ("en_open_light", "en", r"(turn|switch)?on(the)?(light|lights|lamp)", "打开灯光", 0.94, []),
            ("en_close_light", "en", r"(turn|switch)?off(the)?(light|lights|lamp)", "关闭灯光", 0.94, []),
            ("en_brighten_light", "en", r"(brighten|brighter|turnupthelight|make.*light.*bright)", "调亮灯光", 0.9, []),
            ("en_dim_light", "en", r"(dim|dimmer|soften|make.*light.*dark)", "调暗灯光", 0.9, []),
            ("en_open_tv", "en", r"(turn|switch)?on(the)?(tv|television)", "打开电视", 0.94, []),
            ("en_close_tv", "en", r"(turn|switch)?off(the)?(tv|television)", "关闭电视", 0.94, []),
            ("en_sleep_scene", "en", r"(sleepmode|gotobed|goingtosleep|imsleepy)", "切换睡眠模式", 0.9, []),
            ("en_movie_scene", "en", r"(moviemode|watchmovie|cinema)", "切换观影模式", 0.9, []),
            ("en_away_scene", "en", r"(awaymode|leaving|goout|imleaving)", "切换离家模式", 0.9, []),

            ("zh_open_ac_colloquial", "zh", r"(开|打开|整|弄).{0,3}(空调|冷气)|空调.{0,3}(开|打开|开起)", "打开空调", 0.94, []),
            ("zh_close_ac_colloquial", "zh", r"(关|关闭).{0,3}(空调|冷气)|空调.{0,3}(关|关闭)", "关闭空调", 0.94, []),
            ("zh_hot_dialect", "zh", r"(热煞|热死|热得很|热得慌|遭不住|太热|好热|凉快点)", "太热了", 0.88, ["打开空调", "打开风扇", "打开窗户"]),
            ("zh_stuffy_dialect", "zh", r"(闷得很|闷得慌|有点闷|不透气|屋里闷)", "有点闷", 0.88, ["打开空调", "打开风扇", "打开窗户"]),
            ("zh_cold_dialect", "zh", r"(冷得很|太冷|好冷|冷死|暖和点)", "太冷了", 0.86, ["打开暖气", "调高空调温度"]),
            ("zh_brighten_light_dialect", "zh", r"(灯|灯光).{0,4}(搞亮|整亮|亮点|调亮|开亮)", "调亮灯光", 0.92, []),
            ("zh_dim_light_dialect", "zh", r"(灯|灯光).{0,4}(暗点|柔和|调暗|小点)", "调暗灯光", 0.92, []),
            ("zh_open_light_colloquial", "zh", r"(开|打开|整).{0,3}(灯|灯光)", "打开灯光", 0.92, []),
            ("zh_close_light_colloquial", "zh", r"(关|关闭).{0,3}(灯|灯光)", "关闭灯光", 0.92, []),
            ("zh_sleep_scene_colloquial", "zh", r"(睡觉|睡了|歇了|困告|睡眠模式)", "切换睡眠模式", 0.9, []),
            ("zh_movie_scene_colloquial", "zh", r"(看电影|观影|电影模式)", "切换观影模式", 0.9, []),
            ("zh_away_scene_colloquial", "zh", r"(出门|离家|不在家)", "切换离家模式", 0.9, []),
        ]
        return [
            {
                "name": name,
                "language": language,
                "pattern": re.compile(pattern),
                "normalized": normalized,
                "confidence": confidence,
                "extra_candidates": extra_candidates,
            }
            for name, language, pattern, normalized, confidence, extra_candidates in raw_rules
        ]
