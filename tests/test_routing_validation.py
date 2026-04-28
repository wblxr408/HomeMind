import unittest

from core.execution import CommandValidator
from core.llm.decision import LLMDecider
from core.router import InferenceRouter


class LLMIntentPlanningTests(unittest.TestCase):
    def setUp(self):
        self.llm = LLMDecider()

    def test_greeting_is_planned_as_chat_reply(self):
        result = self.llm.plan_intent("你好")
        self.assertEqual(result["intent_type"], "chat_reply")
        self.assertFalse(result["requires_candidates"])

    def test_thanks_is_planned_as_chat_reply(self):
        result = self.llm.plan_intent("谢谢")
        self.assertEqual(result["intent_type"], "chat_reply")

    def test_morning_shortcut_is_planned_as_action_command(self):
        result = self.llm.plan_intent("早安")
        self.assertEqual(result["intent_type"], "action_command")
        self.assertEqual(result["normalized_goal"], "切换早安模式")
        self.assertTrue(result["requires_candidates"])

    def test_hot_phrase_is_planned_as_action_command(self):
        result = self.llm.plan_intent("有点热")
        self.assertEqual(result["intent_type"], "action_command")
        self.assertEqual(result["normalized_goal"], "打开空调")

    def test_ambiguous_phrase_is_planned_as_clarification(self):
        result = self.llm.plan_intent("像昨天那样")
        self.assertEqual(result["intent_type"], "clarification_needed")
        self.assertIn("请问", result["reply_message"])

    def test_time_command_is_planned_as_automation(self):
        result = self.llm.plan_intent("晚上7:00打开空调")
        self.assertEqual(result["intent_type"], "automation_request")
        self.assertTrue(result["requires_automation"])


class RouterHelperTests(unittest.TestCase):
    def setUp(self):
        self.router = InferenceRouter()

    def test_unsupported_target_helper_detects_device(self):
        result = self.router.detect_unsupported_request("打开扫地机器人")
        self.assertIsNotNone(result)
        self.assertEqual(result["route"], "unsupported")
        self.assertEqual(result["target"], "扫地机器人")

    def test_mid_score_routes_cloud_when_query_is_not_explicit(self):
        ranked = [{"action": "打开空调", "final_score": 0.70}]
        result = self.router.decide_route("环境有点不舒服", ranked, cloud_available=True)
        self.assertEqual(result["route"], "cloud")
        self.assertEqual(result["reason"], "mid_confidence_cloud")

    def test_low_score_routes_clarify_when_query_is_not_explicit(self):
        ranked = [{"action": "打开空调", "final_score": 0.30}]
        result = self.router.decide_route("环境有点不舒服", ranked, cloud_available=True)
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
            "scene": "未知模式",
            "device_action": "scene",
            "params": {},
            "confidence": 0.9,
        })
        self.assertFalse(result["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
