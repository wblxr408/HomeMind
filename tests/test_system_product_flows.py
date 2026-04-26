import os
import tempfile
import unittest
from pathlib import Path

from core.language.normalizer import LanguageNormalizer
from core.voice.feedback_store import VoiceFeedbackStore


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
    REPO_ROOT / "data" / "session_state.json",
    REPO_ROOT / "data" / "preferences.json",
    REPO_ROOT / "data" / "tap_rules.json",
]

HOT = "\u70ed"
AC_DEVICE = "\u7a7a\u8c03"
ON_STATUS = "\u5f00"
ASK_PREFIX = "\u8bf7\u95ee"


def _cleanup():
    for path in DATA_FILES:
        if path.exists():
            path.unlink()


class HomeMindSystemRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_main_agent_hot_request_updates_device_state_and_memory(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        result = agent.process(HOT)

        self.assertIn(AC_DEVICE, result)
        self.assertEqual(agent.device_ctrl.get_state(AC_DEVICE).get("status"), ON_STATUS)
        self.assertTrue((REPO_ROOT / "data" / "session_state.json").exists())
        self.assertEqual(agent.kb.count(), len(agent.kb.preset_knowledge))
        self.assertEqual(
            agent.preference_store.snapshot()["devices"].get(AC_DEVICE, {}).get("preferred_temperature"),
            26,
        )

    def test_main_agent_leave_home_phrase_switches_away_scene(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        result = agent.process("我要走了")

        self.assertIn("离家模式", result)
        self.assertEqual(agent.context.current_scene, "离家模式")
        self.assertEqual(agent.device_ctrl.get_state(AC_DEVICE).get("status"), "关")

    def test_low_confidence_unknown_request_asks_for_clarification(self):
        from main import HomeMindAgent

        agent = HomeMindAgent(confidence_threshold=0.95)
        response = agent.process("completely unrelated request")

        self.assertIn(ASK_PREFIX, response)
        self.assertTrue((REPO_ROOT / "data" / "session_state.json").exists())

    def test_main_agent_unsupported_alarm_request_returns_guardrail_message(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        response = agent.process("帮我打开闹钟")

        self.assertIn("闹钟", response)
        self.assertIn("不能控制", response)
        self.assertIn("起床模式", response)
        self.assertNotEqual(agent.device_ctrl.get_state(AC_DEVICE).get("status"), ON_STATUS)


class WebApiSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        from web import server as web_server

        cls.web_server = web_server
        cls.web_server.init_agent(mode="simulated")
        cls.client = cls.web_server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.web_server.agent = None
        _cleanup()

    def setUp(self):
        _cleanup()
        self.web_server.agent.tap_rule_store.rules = []
        self.web_server.agent.tap_rule_store.save()
        self.web_server.agent.last_cloud_context = {}
        self.web_server.agent.last_route_info = {}
        self.web_server.agent.session_store.data = self.web_server.agent.session_store._default_data()
        self.web_server.agent.session_store.save()
        self.web_server.agent.preference_store.data = self.web_server.agent.preference_store._default_data()
        self.web_server.agent.preference_store.save()
        self.original_voice_feedback_store = self.web_server.voice_feedback_store
        self.original_language_normalizer = self.web_server.language_normalizer
        self.voice_feedback_path = Path(tempfile.gettempdir()) / "homemind_voice_feedback_api_test.jsonl"
        self.voice_feedback_path.unlink(missing_ok=True)
        self.web_server.voice_feedback_store = VoiceFeedbackStore(str(self.voice_feedback_path))
        self.web_server.language_normalizer = LanguageNormalizer(
            feedback_store=self.web_server.voice_feedback_store
        )

    def tearDown(self):
        self.web_server.voice_feedback_store = self.original_voice_feedback_store
        self.web_server.language_normalizer = self.original_language_normalizer
        self.voice_feedback_path.unlink(missing_ok=True)

    def test_query_endpoint_rejects_empty_user_input(self):
        response = self.client.post("/api/query", json={"query": ""})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "query \u4e0d\u80fd\u4e3a\u7a7a")

    def test_rule_evaluate_rejects_invalid_time_format(self):
        response = self.client.post("/api/rules/evaluate", json={"time": "25:99"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("time", response.get_json()["error"])

    def test_privacy_status_exposes_minimal_context_after_query(self):
        query_response = self.client.post("/api/query", json={"query": HOT})
        privacy_response = self.client.get("/api/privacy/status")

        self.assertEqual(query_response.status_code, 200)
        self.assertEqual(privacy_response.status_code, 200)
        payload = privacy_response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(
            payload["minimal_fields"],
            ["hour", "temperature", "humidity", "occupancy", "scene", "top_candidates", "preference_summary"],
        )
        self.assertNotIn("recent_turns", payload.get("last_cloud_context", {}))
        self.assertNotIn("last_user_input", payload.get("last_cloud_context", {}))

    def test_query_leave_home_phrase_switches_away_scene_not_air_conditioner(self):
        response = self.client.post("/api/query", json={"query": "我要走了"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "scene_switch")
        self.assertIn("离家模式", payload["response"])
        self.assertNotEqual(payload["action"], "空调_on")

    def test_query_leave_home_phrase_is_not_overridden_by_return_home_preference(self):
        self.web_server.agent.preference_store.data["scenes"] = {
            "回家模式": {"accept_count": 8, "preferred_hour": 11}
        }
        self.web_server.agent.preference_store.save()

        response = self.client.post("/api/query", json={"query": "我要出门了"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "scene_switch")
        self.assertIn("离家模式", payload["response"])
        self.assertNotIn("回家模式", payload["response"])

    def test_query_open_light_is_not_overridden_by_dim_preference(self):
        self.web_server.agent.preference_store.data["devices"] = {
            "灯光": {"preferred_brightness": 30}
        }
        self.web_server.agent.preference_store.save()

        response = self.client.post("/api/query", json={"query": "打开灯光"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "灯光_on")
        self.assertIn("灯光", payload["response"])
        self.assertNotIn("30%", payload["response"])

    def test_query_follow_up_brighten_uses_previous_light_context(self):
        first = self.client.post("/api/query", json={"query": "打开灯光"})
        second = self.client.post("/api/query", json={"query": "再调亮"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        payload = second.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "灯光_adjust")
        self.assertIn("亮度", payload["response"])
        self.assertNotIn("空调", payload["response"])

    def test_query_close_ac_is_not_overridden_by_temperature_preference(self):
        self.web_server.agent.preference_store.data["devices"] = {
            "空调": {"preferred_temperature": 24}
        }
        self.web_server.agent.preference_store.save()

        response = self.client.post("/api/query", json={"query": "关闭空调"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "空调_off")
        self.assertIn("空调", payload["response"])
        self.assertNotIn("24", payload["response"])

    def test_query_unsupported_alarm_request_returns_unsupported_status(self):
        response = self.client.post("/api/query", json={"query": "帮我打开闹钟"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "unsupported")
        self.assertEqual(payload["target"], "闹钟")
        self.assertIn("不能控制", payload["response"])
        self.assertIn("起床模式", payload["response"])

    def test_endpoints_return_500_when_agent_is_not_initialized(self):
        previous_agent = self.web_server.agent
        self.web_server.agent = None
        try:
            status_response = self.client.get("/api/status")
            preferences_response = self.client.get("/api/preferences")
        finally:
            self.web_server.agent = previous_agent

        self.assertEqual(status_response.status_code, 500)
        self.assertEqual(preferences_response.status_code, 500)

    def test_voice_feedback_requires_source_text(self):
        response = self.client.post("/api/voice/feedback", json={"feedback": "accepted"})

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")
        self.assertIn("asr_text", payload["error"])

    def test_voice_feedback_corrected_text_is_recorded_and_reused(self):
        response = self.client.post(
            "/api/voice/feedback",
            json={
                "asr_text": "turn on the thing",
                "normalized": "turn on the thing",
                "corrected_text": "turn on the ac",
                "feedback": "corrected",
                "language": "en",
                "confidence": 0.4,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["record"]["feedback"], "corrected")

        normalized = self.web_server.language_normalizer.normalize("turn on the thing")
        self.assertEqual(normalized.matched_rule, "voice_feedback_history")
        self.assertTrue(normalized.normalized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
