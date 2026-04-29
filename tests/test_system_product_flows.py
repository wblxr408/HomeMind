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
        self.assertEqual(devices[0]["floorPlanId"], "floorPlan-sample.svg")
        self.assertGreater(len(devices[0]["devices"]), 0)


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
            json={"id": "desk_lamp", "name": "\u4e66\u684c\u706f", "type": "light"},
        )
        self.assertEqual(created.status_code, 200)
        created_payload = created.get_json()
        self.assertEqual(created_payload["status"], "success")
        self.assertEqual(created_payload["device"]["id"], "desk_lamp")
        self.assertFalse(created_payload["device"]["state"]["is_on"])

        turned_on = self.client.post("/api/devices/desk_lamp/control", json={"action": "on"})
        self.assertEqual(turned_on.status_code, 200)
        self.assertTrue(turned_on.get_json()["state"]["is_on"])

        updated = self.client.put(
            "/api/devices/desk_lamp",
            json={"name": "\u9605\u8bfb\u706f", "type": "switch"},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["device"]["name"], "\u9605\u8bfb\u706f")

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
        self.assertEqual(len(listed.get_json()["floorPlans"]), 1)

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
