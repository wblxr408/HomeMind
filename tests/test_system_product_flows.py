import io
import json
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
    REPO_ROOT / "data" / "scenes.json",
    REPO_ROOT / "data" / "device-registry.json",
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

        self.assertTrue(ASK_PREFIX in response or "无法理解" in response or "告诉我" in response)
        self.assertTrue((REPO_ROOT / "data" / "session_state.json").exists())

    def test_main_agent_unknown_request_uses_cloud_rescue_before_clarification(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        original_plan_intent = agent.llm.plan_intent
        original_is_cloud_available = agent.llm.is_cloud_available
        original_rescue_intent_with_cloud = agent.llm.rescue_intent_with_cloud
        agent.llm.plan_intent = lambda raw_text, normalized_query="", context=None: {
            "intent_type": "clarification_needed",
            "route": "clarify",
            "reply_message": "请问你是想控制设备、切换场景，还是创建定时任务？",
            "normalized_goal": normalized_query or raw_text,
            "requires_candidates": False,
            "requires_automation": False,
            "decision_confidence": 0.4,
            "reasoning": "forced clarification for test",
        }
        agent.llm.is_cloud_available = lambda: True
        agent.llm.rescue_intent_with_cloud = lambda *args, **kwargs: {
            "intent_type": "action_command",
            "route": "action",
            "reply_message": "",
            "normalized_goal": "打开空调",
            "requires_candidates": True,
            "requires_automation": False,
            "decision_confidence": 0.96,
            "reasoning": "test cloud rescue intent",
        }

        try:
            response = agent.process("completely unrelated request")
        finally:
            agent.llm.plan_intent = original_plan_intent
            agent.llm.is_cloud_available = original_is_cloud_available
            agent.llm.rescue_intent_with_cloud = original_rescue_intent_with_cloud

        self.assertIn(AC_DEVICE, response)
        self.assertNotIn(ASK_PREFIX, response)

    def test_main_agent_unsupported_alarm_request_returns_guardrail_message(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        response = agent.process("帮我打开闹钟")

        self.assertIn("闹钟", response)
        self.assertIn("不能控制", response)
        self.assertIn("起床模式", response)
        self.assertNotEqual(agent.device_ctrl.get_state(AC_DEVICE).get("status"), ON_STATUS)


    def test_main_agent_greeting_returns_chat_reply(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        response = agent.process("\u4f60\u597d")

        self.assertIn("\u4f60\u597d", response)
        self.assertEqual(agent.session_store.get_runtime_context()["recent_turns"][-1]["role"], "assistant")

    def test_main_agent_time_command_requires_confirmation_then_creates_rule(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        proposal = agent.process("\u665a\u4e0a7:00\u6253\u5f00\u7a7a\u8c03")
        created = agent.process("\u597d\u7684")

        self.assertIn("19:00", proposal)
        self.assertIn("\u521b\u5efa\u5b9a\u65f6\u4efb\u52a1", created)
        rules = agent.tap_rule_store.list_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["trigger"]["at"], "19:00")


class DefaultFloorPlanFixtureTests(unittest.TestCase):
    def test_default_floor_plan_and_device_mapping_are_aligned(self):
        floor_plan_path = REPO_ROOT / "data" / "floor-plans.json"
        device_path = REPO_ROOT / "data" / "devices.json"
        svg_path = REPO_ROOT / "uploads" / "floor-plans" / "floorPlan-sample.svg"

        floor_plans = json.loads(floor_plan_path.read_text(encoding="utf-8"))
        devices = json.loads(device_path.read_text(encoding="utf-8"))

        self.assertTrue(svg_path.exists())
        self.assertEqual(floor_plans[0]["id"], "floorPlan-sample.svg")
        self.assertTrue(floor_plans[0].get("active"))
        self.assertEqual(devices[0]["floorPlanId"], "floorPlan-sample.svg")
        self.assertGreater(len(devices[0]["devices"]), 0)
        mapped_types = {item.get("type") for item in devices[0]["devices"]}
        self.assertTrue(
            {"light", "air_conditioner", "tv", "speaker", "fan", "window", "water_heater"}.issubset(mapped_types)
        )


class WebApiSystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        os.environ["LLM_BACKEND"] = "mock"
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
        self.original_floor_plan_upload_dir = self.web_server.FLOOR_PLAN_UPLOAD_DIR
        self.original_floor_plan_store_path = self.web_server.FLOOR_PLAN_STORE_PATH
        self.original_floor_plan_device_store_path = self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH
        self.original_device_registry_path = self.web_server.DEVICE_REGISTRY_PATH
        self.floor_plan_tmp = tempfile.TemporaryDirectory()
        floor_plan_root = Path(self.floor_plan_tmp.name)
        self.web_server.FLOOR_PLAN_UPLOAD_DIR = floor_plan_root / "uploads" / "floor-plans"
        self.web_server.FLOOR_PLAN_STORE_PATH = floor_plan_root / "data" / "floor-plans.json"
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH = floor_plan_root / "data" / "devices.json"
        self.web_server.DEVICE_REGISTRY_PATH = floor_plan_root / "data" / "device-registry.json"
        self.web_server.FLOOR_PLAN_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self._seed_floor_plan_mapping()
        self.web_server.agent.tap_rule_store.rules = []
        self.web_server.agent.tap_rule_store.save()
        self.web_server.agent.scene_store.load()
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

    def _seed_floor_plan_mapping(self):
        plan_id = "test-floor-plan.svg"
        svg_path = self.web_server.FLOOR_PLAN_UPLOAD_DIR / plan_id
        svg_path.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 660"><rect width="640" height="660"/></svg>',
            encoding="utf-8",
        )
        self.web_server.FLOOR_PLAN_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        floor_plan = {
            "id": plan_id,
            "name": "Test Plan",
            "description": "seeded for spatial execution tests",
            "filePath": str(svg_path),
            "url": f"/uploads/floor-plans/{plan_id}",
            "width": 640,
            "height": 660,
            "active": True,
        }
        devices = [
            {"id": "light.living_room_main", "name": "\u5ba2\u5385\u706f", "type": "light", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 12, "y": 20},
            {"id": "climate.living_room_ac", "name": "\u5ba2\u5385\u7a7a\u8c03", "type": "air_conditioner", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 18, "y": 20},
            {"id": "media.living_room_tv", "name": "\u5ba2\u5385\u7535\u89c6", "type": "tv", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 24, "y": 20},
            {"id": "speaker.living_room", "name": "\u5ba2\u5385\u97f3\u54cd", "type": "speaker", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 30, "y": 20},
            {"id": "fan.bedroom", "name": "\u5367\u5ba4\u98ce\u6247", "type": "fan", "area": "bedroom", "areaName": "\u4e3b\u5367", "x": 50, "y": 30},
            {"id": "water_heater.bathroom", "name": "\u70ed\u6c34\u5668", "type": "water_heater", "area": "bathroom1", "areaName": "\u4e3b\u536b", "x": 58, "y": 72},
            {"id": "cover.bedroom_window", "name": "\u5367\u5ba4\u7a97\u6237", "type": "window", "area": "bedroom", "areaName": "\u4e3b\u5367", "x": 55, "y": 30},
        ]
        mapping = {
            "floorPlanId": plan_id,
            "devices": devices,
            "rawDevices": devices,
            "areaNames": {"living_room": "\u5ba2\u5385", "bedroom": "\u4e3b\u5367", "bathroom1": "\u4e3b\u536b"},
        }
        self.web_server.FLOOR_PLAN_STORE_PATH.write_text(json.dumps([floor_plan], ensure_ascii=False), encoding="utf-8")
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.write_text(json.dumps([mapping], ensure_ascii=False), encoding="utf-8")

    def tearDown(self):
        self.web_server.voice_feedback_store = self.original_voice_feedback_store
        self.web_server.language_normalizer = self.original_language_normalizer
        self.voice_feedback_path.unlink(missing_ok=True)
        self.web_server.FLOOR_PLAN_UPLOAD_DIR = self.original_floor_plan_upload_dir
        self.web_server.FLOOR_PLAN_STORE_PATH = self.original_floor_plan_store_path
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH = self.original_floor_plan_device_store_path
        self.web_server.DEVICE_REGISTRY_PATH = self.original_device_registry_path
        self.floor_plan_tmp.cleanup()

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

    def test_query_regression_device_action_mapping_uses_explicit_target(self):
        cases = [
            ("\u5173\u95ed\u97f3\u54cd", "\u97f3\u54cd_off"),
            ("\u6253\u5f00\u70ed\u6c34\u5668", "\u70ed\u6c34\u5668_on"),
            ("\u5173\u95ed\u70ed\u6c34\u5668", "\u70ed\u6c34\u5668_off"),
        ]

        for query, expected_action in cases:
            with self.subTest(query=query):
                response = self.client.post("/api/query", json={"query": query})
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["action"], expected_action)

    def test_query_regression_natural_comfort_and_scene_coverage(self):
        cases = [
            ("\u6709\u70b9\u51b7", "\u7a7a\u8c03_on"),
            ("\u5c4b\u91cc\u95f7\u5f97\u5f88", "\u7a7a\u8c03_on"),
            ("\u5207\u6362\u5230\u5de5\u4f5c\u6a21\u5f0f", "scene_switch"),
            ("\u5207\u6362\u5230\u665a\u5f52\u6a21\u5f0f", "scene_switch"),
        ]

        for query, expected_action in cases:
            with self.subTest(query=query):
                response = self.client.post("/api/query", json={"query": query})
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "success")
                self.assertEqual(payload["action"], expected_action)

    def test_query_regression_unsupported_device_targets(self):
        for query, expected_target in [
            ("\u6253\u5f00\u51b0\u7bb1", "\u51b0\u7bb1"),
            ("\u6253\u5f00\u5496\u5561\u673a", "\u5496\u5561\u673a"),
            ("\u6253\u5f00\u6295\u5f71\u4eea", "\u6295\u5f71\u4eea"),
        ]:
            with self.subTest(query=query):
                response = self.client.post("/api/query", json={"query": query})
                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "unsupported")
                self.assertEqual(payload["target"], expected_target)

    def test_query_security_sensitive_door_lock_always_clarifies(self):
        for query in ["\u6253\u5f00\u95e8\u9501", "\u9501\u95e8"]:
            with self.subTest(query=query):
                response = self.client.post("/api/query", json={"query": query})

                self.assertEqual(response.status_code, 200)
                payload = response.get_json()
                self.assertEqual(payload["status"], "clarification")
                self.assertEqual(payload["response_type"], "clarification")
                self.assertIn("\u5bb6\u5ead\u5b89\u5168", payload.get("response") or payload.get("question", ""))
                self.assertIn(payload["route_reason"], {"safety_sensitive_target", "safety_sensitive_door_action"})

    def test_query_security_clarification_preempts_pending_generic_clarification(self):
        first = self.client.post("/api/query", json={"query": "\u6253\u5f00"})
        second = self.client.post("/api/query", json={"query": "\u6253\u5f00\u5927\u95e8\u95e8\u9501"})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        payload = second.get_json()
        self.assertEqual(payload["status"], "clarification")
        self.assertEqual(payload["route_reason"], "safety_sensitive_target")
        self.assertIn("\u95e8\u9501", payload.get("response") or payload.get("question", ""))
        self.assertNotIn("\u7a97\u6237", payload.get("response") or payload.get("question", ""))
        self.assertNotIn("\u97f3\u54cd", payload.get("response") or payload.get("question", ""))

    def test_query_requires_device_in_active_floor_plan_mapping(self):
        mapping = json.loads(self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.read_text(encoding="utf-8"))
        mapping[0]["devices"] = [
            {"id": "light.living_room_main", "name": "\u5ba2\u5385\u706f", "type": "light", "area": "living_room", "areaName": "\u5ba2\u5385", "x": 12, "y": 20}
        ]
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.write_text(
            json.dumps(mapping, ensure_ascii=False),
            encoding="utf-8",
        )

        response = self.client.post("/api/query", json={"query": "\u6709\u70b9\u70ed"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "unsupported")
        self.assertEqual(payload["route_reason"], "device_not_in_floor_plan")
        self.assertIn("SVG", payload["response"])

    def test_query_uses_custom_area_name_from_floor_plan_mapping(self):
        plan_id = "test-floor-plan.svg"
        response = self.client.post(
            f"/api/floor-plans/{plan_id}/devices",
            json={
                "areaNames": {"living_room": "\u5f71\u97f3\u533a"},
                "devices": [
                    {
                        "entity_id": "light.media_zone",
                        "area": "living_room",
                        "type": "light",
                        "name": "\u5f71\u97f3\u533a\u706f",
                        "areaName": "\u5f71\u97f3\u533a",
                    }
                ],
            },
        )
        query = self.client.post("/api/query", json={"query": "\u6253\u5f00\u5f71\u97f3\u533a\u706f"})

        self.assertEqual(response.status_code, 200)
        payload = query.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["action"], "\u706f\u5149_on")
        self.assertIn("\u5f71\u97f3\u533a\u706f", payload["response"])

    def test_query_regression_multi_turn_clarification_and_pronoun(self):
        first = self.client.post("/api/query", json={"query": "\u5e2e\u6211\u5f04\u4e00\u4e0b"})
        second = self.client.post("/api/query", json={"query": "\u7a7a\u8c03"})
        third = self.client.post("/api/query", json={"query": "\u6253\u5f00"})

        self.assertEqual(first.get_json()["status"], "clarification")
        self.assertEqual(second.get_json()["status"], "clarification")
        self.assertEqual(third.get_json()["status"], "success")
        self.assertEqual(third.get_json()["action"], "\u7a7a\u8c03_on")

        light = self.client.post("/api/query", json={"query": "\u628a\u706f\u8c03\u6697\u4e00\u70b9"})
        darker = self.client.post("/api/query", json={"query": "\u518d\u6697\u4e00\u70b9"})
        speaker = self.client.post("/api/query", json={"query": "\u6253\u5f00\u97f3\u54cd"})
        close_it = self.client.post("/api/query", json={"query": "\u5173\u6389\u5b83"})

        self.assertEqual(light.get_json()["action"], "\u706f\u5149_adjust")
        self.assertEqual(darker.get_json()["action"], "\u706f\u5149_adjust")
        self.assertEqual(speaker.get_json()["action"], "\u97f3\u54cd_on")
        self.assertEqual(close_it.get_json()["action"], "\u97f3\u54cd_off")

    def test_query_regression_multiple_clarification_sessions(self):
        first = self.client.post("/api/query", json={"query": "\u5e2e\u6211\u5f04\u4e00\u4e0b"})
        first_target = self.client.post("/api/query", json={"query": "\u7a7a\u8c03"})
        first_action = self.client.post("/api/query", json={"query": "\u6253\u5f00"})
        second = self.client.post("/api/query", json={"query": "\u518d\u5e2e\u6211\u5f04\u4e00\u4e0b"})
        second_target = self.client.post("/api/query", json={"query": "\u706f\u5149"})
        second_action = self.client.post("/api/query", json={"query": "\u5173\u95ed"})

        self.assertEqual(first.get_json()["status"], "clarification")
        self.assertEqual(first_target.get_json()["status"], "clarification")
        self.assertEqual(first_action.get_json()["status"], "success")
        self.assertEqual(first_action.get_json()["action"], "\u7a7a\u8c03_on")
        self.assertEqual(second.get_json()["status"], "clarification")
        self.assertEqual(second_target.get_json()["status"], "clarification")
        self.assertEqual(second_action.get_json()["status"], "success")
        self.assertEqual(second_action.get_json()["action"], "\u706f\u5149_off")

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

    def test_device_registry_crud_and_control(self):
        listed = self.client.get("/api/devices")
        self.assertEqual(listed.status_code, 200)
        self.assertIn("light", [device["id"] for device in listed.get_json()["devices"]])

        created = self.client.post(
            "/api/devices",
            json={
                "id": "desk_lamp",
                "name": "\u4e66\u684c\u706f",
                "type": "light",
                "area": "study",
                "areaName": "\u4e66\u623f",
            },
        )
        self.assertEqual(created.status_code, 200)
        created_payload = created.get_json()
        self.assertEqual(created_payload["status"], "success")
        self.assertEqual(created_payload["device"]["id"], "desk_lamp")
        self.assertEqual(created_payload["device"]["protocol"], "simulated")
        self.assertEqual(created_payload["device"]["area"], "study")
        self.assertEqual(created_payload["device"]["areaName"], "\u4e66\u623f")
        self.assertFalse(created_payload["device"]["state"]["is_on"])

        turned_on = self.client.post("/api/devices/desk_lamp/control", json={"action": "on"})
        self.assertEqual(turned_on.status_code, 200)
        self.assertTrue(turned_on.get_json()["state"]["is_on"])

        rejected_state_update = self.client.put(
            "/api/devices/desk_lamp",
            json={"name": "\u9605\u8bfb\u706f", "state": {"is_on": False}},
        )
        self.assertEqual(rejected_state_update.status_code, 400)
        self.assertIn("runtime fields", rejected_state_update.get_json()["error"])
        self.assertTrue(self.client.get("/api/devices").get_json()["devices"][-1]["state"]["is_on"])

        rejected_id_update = self.client.put(
            "/api/devices/desk_lamp",
            json={"id": "renamed_lamp", "name": "\u9605\u8bfb\u706f"},
        )
        self.assertEqual(rejected_id_update.status_code, 400)
        self.assertIn("device id cannot be changed", rejected_id_update.get_json()["error"])

        updated = self.client.put(
            "/api/devices/desk_lamp",
            json={"name": "\u9605\u8bfb\u706f", "type": "switch", "area": "bedroom2", "areaName": "\u6b21\u5367"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["device"]["name"], "\u9605\u8bfb\u706f")
        self.assertEqual(updated.get_json()["device"]["area"], "bedroom2")
        self.assertEqual(updated.get_json()["device"]["areaName"], "\u6b21\u5367")

        status_payload = self.client.get("/api/status").get_json()
        self.assertIn("desk_lamp", status_payload["devices"])
        self.assertTrue(status_payload["devices"]["desk_lamp"]["is_on"])

        deleted = self.client.delete("/api/devices/desk_lamp")
        missing_control = self.client.post("/api/devices/desk_lamp/control", json={"action": "off"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(missing_control.status_code, 404)

    def test_device_registry_rejects_duplicate_ids_and_id_rename(self):
        created = self.client.post(
            "/api/devices",
            json={"id": "light", "name": "\u91cd\u590d\u706f", "type": "light"},
        )
        renamed = self.client.put("/api/devices/light", json={"id": "light_2", "name": "\u706f\u5149"})

        self.assertEqual(created.status_code, 409)
        self.assertEqual(renamed.status_code, 400)

    def test_simulated_devices_use_space_to_disambiguate_generic_names(self):
        main = self.client.post(
            "/api/devices",
            json={"id": "light.main_bedroom", "name": "\u706f", "type": "light", "area": "bedroom", "areaName": "\u4e3b\u5367"},
        )
        second = self.client.post(
            "/api/devices",
            json={"id": "light.second_bedroom", "name": "\u706f", "type": "light", "area": "bedroom2", "areaName": "\u6b21\u5367"},
        )

        self.assertEqual(main.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(main.get_json()["device"]["name"], "\u4e3b\u5367\u706f")
        self.assertEqual(second.get_json()["device"]["name"], "\u6b21\u5367\u706f")
        names = [device["name"] for device in self.client.get("/api/devices").get_json()["devices"]]
        self.assertIn("\u4e3b\u5367\u706f", names)
        self.assertIn("\u6b21\u5367\u706f", names)

    def test_scene_crud_endpoints_manage_custom_scene_configs(self):
        scene_name = "\u9605\u8bfb\u6a21\u5f0f"
        config = {
            "\u706f\u5149": {"action": "adjust", "params": {"brightness": 55}},
            "\u97f3\u54cd": {"action": "on", "params": {"volume": 15}},
        }

        created = self.client.post("/api/scenes", json={"name": scene_name, "config": config})
        listed = self.client.get("/api/scenes")
        detail = self.client.get(f"/api/scenes/{scene_name}")
        updated = self.client.put(
            f"/api/scenes/{scene_name}",
            json={"config": {"\u706f\u5149": {"action": "off", "params": {}}}},
        )
        deleted = self.client.delete(f"/api/scenes/{scene_name}")
        missing = self.client.get(f"/api/scenes/{scene_name}")

        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.get_json()["scene"], config)
        self.assertEqual(listed.status_code, 200)
        self.assertIn(scene_name, listed.get_json()["scenes"])
        self.assertIn("items", listed.get_json())
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["scene"]["\u706f\u5149"]["action"], "off")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(missing.status_code, 404)

    def test_scene_crud_rejects_non_object_config(self):
        created = self.client.post("/api/scenes", json={"name": "\u574f\u573a\u666f", "config": []})
        updated = self.client.put("/api/scenes/\u7761\u7720\u6a21\u5f0f", json={"config": []})

        self.assertEqual(created.status_code, 400)
        self.assertEqual(updated.status_code, 400)

    def test_scene_from_nl_returns_editable_draft_before_save(self):
        text = "\u65b0\u5efa\u6df1\u591c\u9605\u8bfb\u6a21\u5f0f\uff0c\u706f\u8c03\u523060%\uff0c\u5173\u95ed\u7535\u89c6"

        draft_response = self.client.post("/api/scenes/from-nl", json={"text": text})
        listed_before = self.client.get("/api/scenes").get_json()["scenes"]
        save_response = self.client.post("/api/scenes/from-nl", json={"text": text, "save": True})

        self.assertEqual(draft_response.status_code, 200)
        draft = draft_response.get_json()
        self.assertEqual(draft["response_type"], "scene_draft")
        self.assertEqual(draft["name"], "\u6df1\u591c\u9605\u8bfb\u6a21\u5f0f")
        self.assertTrue(draft["validation"]["valid"])
        self.assertIn("\u706f\u5149", draft["config"])
        self.assertNotIn("\u6df1\u591c\u9605\u8bfb\u6a21\u5f0f", listed_before)
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(save_response.get_json()["response_type"], "scene_saved")
        self.assertIn("\u6df1\u591c\u9605\u8bfb\u6a21\u5f0f", self.client.get("/api/scenes").get_json()["scenes"])

    def test_rule_from_nl_returns_draft_and_saves_only_when_requested(self):
        text = "\u4e94\u4e00\u5173\u95ed\u7a7a\u8c03"

        draft_response = self.client.post("/api/rules/from-nl", json={"text": text})
        rules_before = self.web_server.agent.tap_rule_store.list_rules()
        save_response = self.client.post("/api/rules/from-nl", json={"text": text, "save": True})

        self.assertEqual(draft_response.status_code, 200)
        draft = draft_response.get_json()
        self.assertEqual(draft["response_type"], "tap_rule_draft")
        self.assertTrue(draft["validation"]["valid"])
        self.assertEqual(draft["rule"]["trigger"], {"type": "holiday", "name": "\u4e94\u4e00", "month": 5, "day": 1})
        self.assertEqual(draft["rule"]["action"]["device_action"], "off")
        self.assertEqual(rules_before, [])
        self.assertEqual(save_response.status_code, 200)
        self.assertEqual(save_response.get_json()["response_type"], "tap_rule_saved")
        self.assertEqual(len(self.web_server.agent.tap_rule_store.list_rules()), 1)

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

    def test_svg_floor_plan_upload_is_validated_saved_and_listed(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="10" height="10"/></svg>'

        upload = self.client.post(
            "/api/floor-plans",
            data={
                "floorPlan": (io.BytesIO(svg), "home plan.svg"),
                "name": "Home Plan",
                "description": "Test plan",
            },
            content_type="multipart/form-data",
        )
        listed = self.client.get("/api/floor-plans")

        self.assertEqual(upload.status_code, 200)
        payload = upload.get_json()
        self.assertEqual(payload["status"], "success")
        plan = payload["floorPlan"]
        self.assertEqual(plan["name"], "Home Plan")
        self.assertEqual(plan["description"], "Test plan")
        self.assertEqual(plan["width"], 10.0)
        self.assertEqual(plan["height"], 10.0)
        self.assertTrue((self.web_server.FLOOR_PLAN_UPLOAD_DIR / plan["id"]).exists())
        self.assertEqual(listed.status_code, 200)
        self.assertIn(plan["id"], [item["id"] for item in listed.get_json()["floorPlans"]])

        svg_response = self.client.get(f"/api/floor-plans/{plan['id']}/svg")
        self.assertEqual(svg_response.status_code, 200)
        self.assertIn(b"<svg", svg_response.data)

    def test_svg_floor_plan_upload_rejects_script_content(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

        response = self.client.post(
            "/api/floor-plans",
            data={"floorPlan": (io.BytesIO(svg), "unsafe.svg")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "error")

    def test_floor_plan_crud_and_device_mapping(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 660"><rect width="640" height="660"/></svg>'
        upload = self.client.post(
            "/api/floor-plans",
            data={"floorPlan": (io.BytesIO(svg), "map.svg"), "name": "Map"},
            content_type="multipart/form-data",
        )
        plan = upload.get_json()["floorPlan"]

        update = self.client.put(
            f"/api/floor-plans/{plan['id']}",
            json={"name": "Updated Map", "description": "With devices"},
        )
        mapping = self.client.post(
            f"/api/floor-plans/{plan['id']}/devices",
            json={"devices": [["light.living_room", "living_room", "light"], ["sensor.motion", "living_room", "motion_sensor"]]},
        )
        loaded_mapping = self.client.get(f"/api/floor-plans/{plan['id']}/devices")
        delete = self.client.delete(f"/api/floor-plans/{plan['id']}")
        after_delete_mapping = self.client.get(f"/api/floor-plans/{plan['id']}/devices")

        self.assertEqual(update.status_code, 200)
        self.assertEqual(update.get_json()["floorPlan"]["name"], "Updated Map")
        self.assertEqual(mapping.status_code, 200)
        devices = mapping.get_json()["devices"]
        self.assertEqual(len(devices), 2)
        self.assertIn("x", devices[0])
        self.assertIn("y", devices[0])
        self.assertEqual(loaded_mapping.get_json()["deviceMapping"]["floorPlanId"], plan["id"])
        self.assertEqual(delete.status_code, 200)
        self.assertEqual(after_delete_mapping.get_json()["devices"], [])


    def test_query_greeting_returns_chat_response(self):
        response = self.client.post("/api/query", json={"query": "你好"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "chat")
        self.assertIn("你好", payload["response"])

    def test_query_time_command_returns_automation_proposal(self):
        response = self.client.post("/api/query", json={"query": "晚上7:00打开空调"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "automation_proposal")
        self.assertEqual(payload["proposal"]["rule_preview"]["trigger"]["at"], "19:00")

    def test_scene_switch_endpoint_updates_scene_and_devices(self):
        response = self.client.post("/api/scenes/sleep/switch")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["scene"], "sleep")
        self.assertIn("\u7761\u7720\u6a21\u5f0f", payload["result"])
        self.assertEqual(self.web_server.agent.session_store.get_current_scene(), "\u7761\u7720\u6a21\u5f0f")

    def test_query_may_day_command_returns_holiday_automation_proposal(self):
        response = self.client.post("/api/query", json={"query": "\u4e94\u4e00\u7684\u65f6\u5019\u7ed9\u6211\u5173\u6389\u7a7a\u8c03"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "automation_proposal")
        self.assertEqual(payload["proposal"]["rule_preview"]["trigger"], {"type": "holiday", "name": "\u4e94\u4e00", "month": 5, "day": 1})
        self.assertIn("\u4e94\u4e00\u5f53\u5929", payload["response"])
        self.assertIn("\u5173\u95ed\u7a7a\u8c03", payload["response"])

    def test_query_national_day_bulk_device_off_returns_away_scene_automation(self):
        response = self.client.post("/api/query", json={"query": "\u56fd\u5e86\u7684\u65f6\u5019\u7ed9\u6211\u5173\u6389\u6240\u6709\u7535\u5668"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "automation_proposal")
        self.assertEqual(payload["proposal"]["rule_preview"]["trigger"], {"type": "holiday", "name": "\u56fd\u5e86", "month": 10, "day": 1})
        self.assertEqual(payload["proposal"]["rule_preview"]["action"], {"type": "scene_switch", "scene": "\u79bb\u5bb6\u6a21\u5f0f"})
        self.assertIn("\u56fd\u5e86\u5f53\u5929", payload["response"])
        self.assertIn("\u79bb\u5bb6\u6a21\u5f0f", payload["response"])

    def test_query_clarify_route_uses_cloud_rescue_before_asking_question(self):
        agent = self.web_server.agent
        original_plan_intent = agent.llm.plan_intent
        original_is_cloud_available = agent.llm.is_cloud_available
        original_rescue_decision_with_cloud = agent.llm.rescue_decision_with_cloud
        original_decide_route = agent.router.decide_route

        agent.llm.plan_intent = lambda raw_text, normalized_query="", context=None: {
            "intent_type": "action_command",
            "route": "action",
            "reply_message": "",
            "normalized_goal": normalized_query or raw_text,
            "requires_candidates": True,
            "requires_automation": False,
            "decision_confidence": 0.8,
            "reasoning": "forced action command",
        }
        agent.llm.is_cloud_available = lambda: True
        agent.router.decide_route = lambda *args, **kwargs: {
            "route": "clarify",
            "reason": "forced_clarify_for_test",
            "top_candidates": ["打开空调"],
        }
        agent.llm.rescue_decision_with_cloud = lambda *args, **kwargs: {
            "action": "设备控制",
            "device": "空调",
            "scene": "",
            "device_action": "on",
            "params": {"temperature": 26},
            "confidence": 0.97,
            "reasoning": "test cloud rescue decision",
        }

        try:
            response = self.client.post("/api/query", json={"query": "完全听不懂的句子"})
        finally:
            agent.llm.plan_intent = original_plan_intent
            agent.llm.is_cloud_available = original_is_cloud_available
            agent.llm.rescue_decision_with_cloud = original_rescue_decision_with_cloud
            agent.router.decide_route = original_decide_route

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "execution_result")
        self.assertEqual(payload["route"], "cloud")
        self.assertIn("空调", payload["response"])

    def test_followup_may_day_updates_pending_automation_trigger(self):
        first = self.client.post("/api/query", json={"query": "\u665a\u4e0a7:00\u7ed9\u6211\u5173\u6389\u7a7a\u8c03"}).get_json()
        self.assertEqual(first["proposal"]["rule_preview"]["trigger"]["at"], "19:00")

        second = self.client.post("/api/query", json={"query": "\u4e94\u4e00\u7684\u65f6\u5019"}).get_json()

        self.assertEqual(second["status"], "success")
        self.assertEqual(second["response_type"], "automation_proposal")
        self.assertEqual(second["proposal"]["rule_preview"]["trigger"], {"type": "holiday", "name": "\u4e94\u4e00", "month": 5, "day": 1})
        self.assertEqual(second["proposal"]["rule_preview"]["action"]["device_action"], "off")

    def test_accepting_automation_proposal_creates_rule(self):
        proposal_response = self.client.post("/api/query", json={"query": "晚上7:00打开空调"})
        proposal = proposal_response.get_json()

        feedback_response = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": proposal["message_id"],
                "target_type": "automation_proposal",
                "feedback_type": "accept",
            },
        )

        self.assertEqual(feedback_response.status_code, 200)
        payload = feedback_response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "automation_created")
        rules = self.web_server.agent.tap_rule_store.list_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["trigger"]["at"], "19:00")

    def test_change_feedback_revises_automation_proposal_for_next_round(self):
        proposal = self.client.post("/api/query", json={"query": "\u665a\u4e0a7:00\u6253\u5f00\u7a7a\u8c03"}).get_json()

        response = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": proposal["message_id"],
                "target_type": "automation_proposal",
                "feedback_type": "change",
                "correction": "\u6539\u6210\u4e94\u4e00\u5173\u95ed\u7a7a\u8c03",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["response_type"], "automation_proposal")
        revised = payload["proposal"]["rule_preview"]
        self.assertEqual(revised["trigger"], {"type": "holiday", "name": "\u4e94\u4e00", "month": 5, "day": 1})
        self.assertEqual(revised["action"]["device_action"], "off")
        self.assertEqual(payload["proposal"]["feedback_target"]["target_type"], "automation_proposal")

    def test_change_feedback_records_correction_mapping(self):
        response = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": "manual_feedback_case",
                "target_type": "decision",
                "feedback_type": "change",
                "original_input": "早安",
                "normalized_input": "早安",
                "correction": "切换早安模式",
            },
        )

        self.assertEqual(response.status_code, 200)
        normalized = self.web_server.agent.preference_store.snapshot()["language"]["dialect_terms"]
        self.assertEqual(normalized.get("早安"), "切换早安模式")

    def test_change_feedback_reexecutes_correction_and_keeps_feedback_loop(self):
        mapping = json.loads(self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.read_text(encoding="utf-8"))
        mapping[0]["devices"].extend([
            {"id": "light.main_bedroom", "name": "\u4e3b\u5367\u706f", "type": "light", "area": "bedroom", "areaName": "\u4e3b\u5367", "x": 51, "y": 31},
            {"id": "light.second_bedroom", "name": "\u6b21\u5367\u706f", "type": "light", "area": "bedroom2", "areaName": "\u6b21\u5367", "x": 61, "y": 31},
        ])
        mapping[0]["areaNames"]["bedroom2"] = "\u6b21\u5367"
        self.web_server.FLOOR_PLAN_DEVICE_STORE_PATH.write_text(
            json.dumps(mapping, ensure_ascii=False),
            encoding="utf-8",
        )

        initial = self.client.post("/api/query", json={"query": "\u5173\u6389\u4e3b\u5367\u706f"}).get_json()
        response = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": initial["message_id"],
                "target_type": "execution",
                "feedback_type": "change",
                "correction": "\u662f\u6b21\u5367\u706f",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["response_type"], "execution_result")
        self.assertEqual(payload["action"], "\u706f\u5149_off")
        self.assertIn("\u6b21\u5367\u706f", payload["response"])
        self.assertEqual(payload["feedback_target"]["target_type"], "execution")

        accepted = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": payload["feedback_target"]["message_id"],
                "target_type": "execution",
                "feedback_type": "accept",
            },
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.get_json()["status"], "success")

    def test_reject_feedback_updates_interaction_metrics(self):
        response = self.client.post(
            "/api/interaction/feedback",
            json={
                "message_id": "manual_reject_case",
                "target_type": "execution",
                "feedback_type": "reject",
                "original_input": "打开空调",
                "normalized_input": "打开空调",
            },
        )

        self.assertEqual(response.status_code, 200)
        snapshot = self.web_server.agent.preference_store.snapshot()
        self.assertEqual(snapshot["interaction_feedback"]["execution"]["rejected"], 1)

if __name__ == "__main__":
    unittest.main(verbosity=2)
