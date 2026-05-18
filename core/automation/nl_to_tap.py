"""Natural language to TAP rule conversion."""

from __future__ import annotations

import re
from typing import Dict, Optional

from core.config import NL_TAP_CONFIG


class NLToTAPConverter:
    """Convert simple natural language automation requests into TAP rules."""

    FIXED_HOLIDAYS = {
        "元旦": {"type": "holiday", "name": "元旦", "month": 1, "day": 1},
        "五一": {"type": "holiday", "name": "五一", "month": 5, "day": 1},
        "劳动节": {"type": "holiday", "name": "五一", "month": 5, "day": 1},
        "国庆": {"type": "holiday", "name": "国庆", "month": 10, "day": 1},
        "圣诞": {"type": "holiday", "name": "圣诞", "month": 12, "day": 25},
    }
    HOLIDAY_PATTERNS = [
        r"节假日", r"周末", r"周六", r"周日",
        r"元旦", r"春节", r"清明", r"五一", r"劳动节", r"端午",
        r"中秋", r"国庆", r"圣诞",
    ]
    SCENE_NAMES = [
        "睡眠模式", "待客模式", "离家模式", "观影模式", "起床模式",
        "回家模式", "工作模式", "早安模式", "晚归模式",
    ]
    SCENE_SHORTCUTS = {
        "早安": "早安模式",
        "晚安": "睡眠模式",
        "回家": "回家模式",
    }
    DEVICE_ALIASES = {
        "灯": "灯光",
        "灯光": "灯光",
        "空调": "空调",
        "电视": "电视",
        "音响": "音响",
        "风扇": "风扇",
        "窗户": "窗户",
        "热水器": "热水器",
    }

    def __init__(self, llm_decider=None):
        self.llm = llm_decider

    def parse(self, nl_text: str) -> Optional[Dict]:
        text = self._normalize_text(nl_text)
        if not text:
            return None

        action = self._extract_action(text)
        if not action:
            return None

        return {
            "name": text[:40],
            "enabled": True,
            "trigger": self._extract_time_condition(text),
            "conditions": [],
            "action": action,
            "priority": NL_TAP_CONFIG.get("default_priority", 50),
        }

    def parse_scene_creation(self, nl_text: str) -> Optional[Dict]:
        text = self._normalize_text(nl_text)
        match = re.search(r"(?:叫|命名为|名称为)\s*['\"]?([^'\"，。；\s]+模式)['\"]?", text)
        if not match:
            match = re.search(r"(?:创建|新建)(?:一个)?(?:名为|叫)?\s*['\"]?([^'\"，。；\s]+模式)['\"]?", text)
        if not match:
            return None
        scene_name = match.group(1).strip()
        config = {}
        clauses = [item.strip() for item in re.split(r"[，。；;]", text) if item.strip()]
        for clause in clauses or [text]:
            for alias, device in self.DEVICE_ALIASES.items():
                if alias not in clause:
                    continue
                action = self._extract_device_action(clause)
                if action:
                    config[device] = {"action": action, "params": self._extract_params(clause, device)}
        return {"name": scene_name, "config": config}

    def _normalize_text(self, text: str) -> str:
        normalized = str(text or "").strip()
        for shortcut, scene in self.SCENE_SHORTCUTS.items():
            if shortcut in normalized and scene not in normalized:
                normalized = normalized.replace(shortcut, scene)
        return normalized

    def _extract_time_condition(self, text: str) -> Dict:
        explicit = self.extract_trigger(text)
        if explicit:
            return explicit
        default_time = NL_TAP_CONFIG.get("default_time", "08:00")
        return {"type": "time", "at": default_time}

    def extract_trigger(self, text: str) -> Optional[Dict]:
        for pattern in self.HOLIDAY_PATTERNS:
            if re.search(pattern, text):
                if pattern in (r"周六", r"周日", r"周末"):
                    return {"type": "day_of_week", "days": [5, 6]}
                holiday = self.FIXED_HOLIDAYS.get(pattern)
                if holiday:
                    return dict(holiday)
                return {"type": "holiday", "name": pattern}

        exact_match = re.search(r"(早上|上午|中午|下午|晚上|今晚)?\s*(\d{1,2}):(\d{2})", text)
        if exact_match:
            hour = self._normalize_hour(int(exact_match.group(2)), exact_match.group(1) or "")
            return {"type": "time", "at": f"{hour:02d}:{exact_match.group(3)}"}

        hour_match = re.search(r"(早上|上午|中午|下午|晚上|今晚)?\s*(\d{1,2})点(半|(\d{1,2})分)?", text)
        if hour_match:
            period = hour_match.group(1) or ""
            minute_token = hour_match.group(3)
            if minute_token == "半":
                minute = "30"
            elif hour_match.group(4):
                minute = f"{int(hour_match.group(4)):02d}"
            else:
                minute = "00"
            hour = self._normalize_hour(int(hour_match.group(2)), period)
            return {"type": "time", "at": f"{hour:02d}:{minute}"}

        return None

    def _normalize_hour(self, hour: int, period: str) -> int:
        hour = hour % 24
        if period in {"下午", "晚上", "今晚"} and hour < 12:
            hour += 12
        elif period == "中午" and hour < 11:
            hour += 12
        elif period in {"早上", "上午"} and hour == 12:
            hour = 0
        return hour

    def _extract_action(self, text: str) -> Optional[Dict]:
        for scene in self.SCENE_NAMES:
            if scene in text:
                return {"type": "scene_switch", "scene": scene}

        for alias, device in self.DEVICE_ALIASES.items():
            if alias not in text:
                continue
            action = self._extract_device_action(text)
            if action:
                return {
                    "type": "device_control",
                    "device": device,
                    "device_action": action,
                    "params": self._extract_params(text, device),
                }
        return None

    def _extract_device_action(self, text: str) -> Optional[str]:
        if any(word in text for word in ("关闭", "关掉", "关")):
            return "off"
        if any(word in text for word in ("打开", "开启", "开")):
            return "on"
        if any(word in text for word in ("调", "设置", "设为")):
            return "adjust"
        return None

    def _extract_params(self, text: str, device: str) -> Dict:
        params = {}
        if device == "空调":
            temp_match = re.search(r"(\d{2})\s*(?:度|℃)?", text)
            if temp_match:
                params["temperature"] = int(temp_match.group(1))
        if device in ("灯光", "电视", "音响"):
            percent_match = re.search(r"(\d{1,3})\s*%", text)
            if percent_match:
                key = "brightness" if device == "灯光" else "volume"
                params[key] = max(0, min(100, int(percent_match.group(1))))
        return params
