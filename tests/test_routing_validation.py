import unittest

from core.execution import CommandValidator
from core.router import InferenceRouter


class InferenceRouterTests(unittest.TestCase):
    def setUp(self):
        self.router = InferenceRouter()

    def test_explicit_command_routes_local(self):
        ranked = [{"action": "切换睡眠模式", "final_score": 0.72}]
        result = self.router.decide_route("切换到睡眠模式", ranked, normalized_query="切换睡眠模式", cloud_available=True)
        self.assertEqual(result["route"], "local")
        self.assertEqual(result["reason"], "explicit_command")

    def test_mid_score_routes_cloud_when_available(self):
        ranked = [{"action": "打开空调", "final_score": 0.70}]
        result = self.router.decide_route("有点闷", ranked, normalized_query="有点闷", cloud_available=True)
        self.assertEqual(result["route"], "cloud")
        self.assertEqual(result["reason"], "mid_confidence_cloud")

    def test_mid_score_routes_fallback_when_cloud_unavailable(self):
        ranked = [{"action": "打开空调", "final_score": 0.70}]
        result = self.router.decide_route("有点闷", ranked, normalized_query="有点闷", cloud_available=False)
        self.assertEqual(result["route"], "fallback")
        self.assertEqual(result["reason"], "cloud_unavailable")

    def test_low_score_routes_clarify(self):
        ranked = [{"action": "打开空调", "final_score": 0.30}]
        result = self.router.decide_route("像昨天晚上那样", ranked, normalized_query="像昨天晚上那样", cloud_available=True)
        self.assertEqual(result["route"], "clarify")


class CommandValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = CommandValidator()

    def test_valid_air_conditioner_command_passes(self):
        result = self.validator.validate({
            "action": "设备控制",
            "device": "空调",
            "device_action": "on",
            "params": {"temperature": 26},
            "confidence": 0.9,
        })
        self.assertTrue(result["valid"])
        self.assertEqual(result["risk_level"], "low")

    def test_invalid_temperature_is_rejected(self):
        result = self.validator.validate({
            "action": "设备控制",
            "device": "空调",
            "device_action": "on",
            "params": {"temperature": 35},
            "confidence": 0.9,
        })
        self.assertFalse(result["valid"])
        self.assertTrue(any("temperature" in item for item in result["errors"]))

    def test_high_risk_water_heater_requires_confirmation(self):
        result = self.validator.validate({
            "action": "设备控制",
            "device": "热水器",
            "device_action": "on",
            "params": {"temperature": 65},
            "confidence": 0.9,
        })
        self.assertTrue(result["valid"])
        self.assertTrue(result["requires_confirmation"])
        self.assertEqual(result["risk_level"], "high")

    def test_invalid_scene_is_rejected(self):
        result = self.validator.validate({
            "action": "场景切换",
            "scene": "工作模式",
            "device_action": "scene",
            "params": {},
            "confidence": 0.9,
        })
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
