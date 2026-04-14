"""
信息查询工具
支持：temperature / humidity / history / weather / schedule / preference
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class InfoQuery:
    """信息查询工具，查询家庭环境、历史、天气等数据"""

    def __init__(self):
        self.temperature = 28.0
        self.humidity = 75.0
        self._schedule: Dict[str, str] = {}
        self._preference_cache: Dict[str, Any] = {}

    def execute(self, query_type: str, params: Optional[Dict[str, Any]] = None) -> str:
        """
        执行信息查询
        query_type: temperature / humidity / history / weather / schedule / preference
        """
        if params is None:
            params = {}

        if query_type == "temperature":
            return f"当前室内温度：{self.temperature}°C。"
        elif query_type == "humidity":
            return f"当前室内湿度：{self.humidity}%。"
        elif query_type == "weather":
            return f"室外天气：晴，气温32°C，适合开窗通风。"
        elif query_type == "history":
            date = params.get("date", "昨天")
            return f"{date}晚上22:30，灯光亮度10%，空调26°C，睡眠模式。"
        elif query_type == "schedule":
            time_key = params.get("time", "")
            result = self._schedule.get(time_key, "暂无日程安排。")
            return result
        elif query_type == "preference":
            temp = params.get("temperature", self.temperature)
            if temp > 28:
                return "28°C以上偏好开空调降温。"
            elif temp < 18:
                return "18°C以下偏好开暖气。"
            else:
                return "当前温度适宜，暂无特殊偏好。"
        else:
            return f"不支持的查询类型: {query_type}"

    def set_temperature(self, temp: float):
        self.temperature = temp

    def set_humidity(self, humidity: float):
        self.humidity = humidity

    def add_schedule(self, time: str, content: str):
        self._schedule[time] = content
        logger.info(f"日程添加: {time} - {content}")

    def get_preference(self, key: str, default: Any = None) -> Any:
        return self._preference_cache.get(key, default)

    def set_preference(self, key: str, value: Any):
        self._preference_cache[key] = value
