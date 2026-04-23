"""
信息查询工具
支持：temperature / humidity / history / weather / schedule / preference
"""

import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class _WeatherCache:
    """简单内存天气缓存，带 TTL"""
    def __init__(self, ttl_seconds: int = 1800):  # 默认30分钟
        self._cache: Dict[str, tuple] = {}  # key -> (data, timestamp)
        self._ttl = ttl_seconds

    def get(self, key: str) -> Optional[Dict]:
        if key not in self._cache:
            return None
        data, timestamp = self._cache[key]
        if time.time() - timestamp > self._ttl:
            del self._cache[key]
            return None
        return data

    def set(self, key: str, data: Dict):
        self._cache[key] = (data, time.time())

    def clear(self):
        self._cache.clear()


class InfoQuery:
    """信息查询工具，查询家庭环境、历史、天气等数据"""

    # 默认城市坐标 (北京)
    DEFAULT_LATITUDE = 39.9042
    DEFAULT_LONGITUDE = 116.4074
    DEFAULT_LOCATION_NAME = "北京"

    # Open-Meteo 天气码转中文描述
    WEATHER_CODES = {
        0: "晴",
        1: "晴间多云",
        2: "多云",
        3: "阴",
        45: "雾",
        48: "雾凇",
        51: "小毛毛雨",
        53: "中毛毛雨",
        55: "大毛毛雨",
        56: "冻毛毛雨",
        57: "强冻毛毛雨",
        61: "小雨",
        63: "中雨",
        65: "大雨",
        66: "冻雨",
        67: "强冻雨",
        71: "小雪",
        73: "中雪",
        75: "大雪",
        77: "雪粒",
        80: "小阵雨",
        81: "中阵雨",
        82: "大阵雨",
        85: "小阵雪",
        86: "大阵雪",
        95: "雷暴",
        96: "雷暴伴小冰雹",
        99: "雷暴伴大冰雹",
    }

    def __init__(self):
        self.temperature = 28.0
        self.humidity = 75.0
        self._schedule: Dict[str, str] = {}
        self._preference_cache: Dict[str, Any] = {}
        self._weather_cache = _WeatherCache(ttl_seconds=1800)

        # 自定义位置
        self._latitude = self.DEFAULT_LATITUDE
        self._longitude = self.DEFAULT_LONGITUDE
        self._location_name = self.DEFAULT_LOCATION_NAME

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
            location = params.get("location", "本地")
            weather_data = self._fetch_weather(location)
            if weather_data:
                return (f"当前{location}天气：{weather_data['condition']}，"
                        f"气温{weather_data['temperature']}°C，"
                        f"湿度{weather_data['humidity']}%。"
                        f"{weather_data.get('tips', '')}")
            return "天气信息暂时不可用。"
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

    def _fetch_weather(self, location: str = "本地") -> Optional[Dict]:
        """获取天气数据，支持缓存和降级处理"""
        cache_key = f"{self._latitude}_{self._longitude}"

        # 先检查缓存
        cached = self._weather_cache.get(cache_key)
        if cached:
            logger.debug(f"使用缓存的天气数据: {cache_key}")
            return cached

        # 尝试从 Open-Meteo API 获取
        weather_data = self._fetch_from_openmeteo()
        if weather_data:
            self._weather_cache.set(cache_key, weather_data)
            return weather_data

        # 降级：使用模拟数据
        logger.warning("无法获取真实天气数据，使用模拟数据")
        return self._get_mock_weather()

    def _fetch_from_openmeteo(self) -> Optional[Dict]:
        """从 Open-Meteo API 获取天气数据（免费，无需 API Key）"""
        try:
            import requests

            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": self._latitude,
                "longitude": self._longitude,
                "current": "temperature_2m,relative_humidity_2m,weather_code",
                "timezone": "Asia/Shanghai",
            }

            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            current = data.get("current", {})
            temp = current.get("temperature_2m", 25)
            humidity = current.get("relative_humidity_2m", 50)
            weather_code = current.get("weather_code", 0)

            condition = self.WEATHER_CODES.get(weather_code, "未知")
            tips = self._generate_weather_tips(condition, temp)

            logger.info(f"成功获取天气数据: {self._location_name} {temp}°C {condition}")

            return {
                "temperature": temp,
                "humidity": humidity,
                "condition": condition,
                "weather_code": weather_code,
                "location": self._location_name,
                "tips": tips,
            }

        except ImportError:
            logger.warning("requests 库未安装，无法获取真实天气")
            return None
        except Exception as e:
            logger.error(f"获取天气数据失败: {e}")
            return None

    def _generate_weather_tips(self, condition: str, temperature: float) -> str:
        """根据天气状况和温度生成建议"""
        tips = []

        # 温度建议
        if temperature > 30:
            tips.append("气温较高，建议注意防暑。")
        elif temperature < 10:
            tips.append("气温较低，建议添加衣物。")

        # 天气状况建议
        if "雨" in condition or "雪" in condition:
            tips.append("建议外出携带雨具。")
        elif "雾" in condition:
            tips.append("有雾，能见度较低，出行注意安全。")
        elif condition == "晴":
            tips.append("适合开窗通风。")

        return " ".join(tips) if tips else ""

    def _get_mock_weather(self) -> Dict:
        """获取模拟天气数据（降级使用）"""
        return {
            "temperature": 25,
            "humidity": 55,
            "condition": "晴",
            "weather_code": 0,
            "location": self._location_name,
            "tips": "适合开窗通风。",
        }

    def set_location(self, latitude: float, longitude: float, name: str = "本地"):
        """设置自定义位置用于天气查询"""
        self._latitude = latitude
        self._longitude = longitude
        self._location_name = name
        # 清除旧缓存，使用新位置重新获取
        self._weather_cache.clear()
        logger.info(f"位置已设置为: {name} ({latitude}, {longitude})")

    def get_location(self) -> Dict[str, Any]:
        """获取当前位置信息"""
        return {
            "name": self._location_name,
            "latitude": self._latitude,
            "longitude": self._longitude,
        }

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
