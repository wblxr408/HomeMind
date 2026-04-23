import os
import unittest
from io import BytesIO
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_KEY_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
]


def _cleanup_generated_files():
    for path in DATA_KEY_FILES:
        if path.exists():
            path.unlink()


class HomeMindCliMockFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    @classmethod
    def tearDownClass(cls):
        _cleanup_generated_files()

    def test_cli_mock_ventilation_query_controls_air_conditioner(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()

        result = agent.process("有点闷")

        self.assertIn("已开启空调", result)
        self.assertEqual(agent.device_ctrl.get_state("空调").get("status"), "开")
        self.assertEqual(agent.device_ctrl.get_state("空调").get("temperature"), 26)

    def test_cli_mock_scene_query_switches_sleep_scene(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()

        result = agent.process("切换到睡眠模式")

        self.assertIn("已切换到睡眠模式", result)
        self.assertEqual(agent.device_ctrl.get_state("灯光").get("brightness"), 10)
        self.assertEqual(agent.device_ctrl.get_state("电视").get("status"), "关")


class HomeMindWebMockFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        try:
            from web import server as web_server
        except OSError as exc:
            raise unittest.SkipTest(f"Web tests skipped due to local Python/asyncio environment issue: {exc}")

        cls.web_server = web_server
        cls.web_server.init_agent(mode="simulated")
        cls.client = cls.web_server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.web_server.agent = None
        _cleanup_generated_files()

    def test_status_endpoint_returns_context_and_devices(self):
        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("context", payload)
        self.assertIn("devices", payload)
        self.assertIn("air_conditioner", payload["devices"])

    def test_query_endpoint_handles_device_and_scene_commands_in_mock_mode(self):
        hot_response = self.client.post("/api/query", json={"query": "有点闷"})
        sleep_response = self.client.post("/api/query", json={"query": "我要睡觉了"})

        self.assertEqual(hot_response.status_code, 200)
        self.assertEqual(sleep_response.status_code, 200)

        hot_payload = hot_response.get_json()
        sleep_payload = sleep_response.get_json()

        self.assertEqual(hot_payload["status"], "success")
        self.assertEqual(hot_payload["action"], "空调_on")
        self.assertIn("已开启空调", hot_payload["response"])

        self.assertEqual(sleep_payload["status"], "success")
        self.assertEqual(sleep_payload["action"], "scene_switch")
        self.assertIn("已切换到睡眠模式", sleep_payload["response"])

    def test_device_and_scene_endpoints_apply_mock_state_changes(self):
        device_response = self.client.post("/api/devices/light/control", json={"action": "on", "params": {}})
        scene_response = self.client.post("/api/scenes/sleep/switch")

        self.assertEqual(device_response.status_code, 200)
        self.assertEqual(scene_response.status_code, 200)

        device_payload = device_response.get_json()
        scene_payload = scene_response.get_json()

        self.assertEqual(device_payload["status"], "success")
        self.assertTrue(device_payload["state"]["is_on"])

        self.assertEqual(scene_payload["status"], "success")
        self.assertEqual(scene_payload["scene"], "sleep")
        self.assertTrue(scene_payload["devices"]["light"]["is_on"])

    def test_info_dqn_and_kb_endpoints_work_in_mock_mode(self):
        info_response = self.client.get("/api/info/temperature")
        recommend_response = self.client.get("/api/dqn/recommend")
        feedback_response = self.client.post("/api/dqn/feedback", json={"id": "dqn_0", "response": "接受"})
        add_response = self.client.post(
            "/api/kb/add",
            json={"text": "用户喜欢26度空调", "category": "用户习惯"},
        )
        query_response = self.client.post("/api/kb/query", json={"query": "26度空调", "top_k": 1})

        self.assertEqual(info_response.status_code, 200)
        self.assertEqual(recommend_response.status_code, 200)
        self.assertEqual(feedback_response.status_code, 200)
        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(query_response.status_code, 200)

        info_payload = info_response.get_json()
        self.assertEqual(info_payload["status"], "success")
        self.assertIn("温度", info_payload["result"])
        self.assertEqual(recommend_response.get_json()["status"], "success")
        self.assertEqual(feedback_response.get_json()["status"], "success")
        self.assertEqual(add_response.get_json()["status"], "success")

        kb_results = query_response.get_json()["results"]
        self.assertGreaterEqual(len(kb_results), 1)
        self.assertIn("26度空调", kb_results[0]["content"])

    def test_voice_endpoint_reports_browser_only_mode(self):
        response = self.client.post("/api/voice/transcribe")

        self.assertEqual(response.status_code, 501)
        payload = response.get_json()
        self.assertEqual(payload["status"], "browser_only")

    def test_spatial_endpoints_can_read_seed_data_or_accept_uploads(self):
        plans_response = self.client.get("/api/floor-plans")
        mappings_response = self.client.get("/api/devices")

        self.assertEqual(plans_response.status_code, 200)
        self.assertEqual(mappings_response.status_code, 200)

        plans_payload = plans_response.get_json()
        mappings_payload = mappings_response.get_json()
        floor_plans = plans_payload["floorPlans"]

        if not floor_plans:
            upload_response = self.client.post(
                "/api/floor-plans",
                data={
                    "name": "Test Plan",
                    "description": "Uploaded during unit test",
                    "floorPlan": (BytesIO(b'<svg viewBox="0 0 100 100"></svg>'), "test.svg"),
                },
                content_type="multipart/form-data",
            )
            self.assertEqual(upload_response.status_code, 200)
            floor_plans = self.client.get("/api/floor-plans").get_json()["floorPlans"]

        self.assertGreaterEqual(len(floor_plans), 1)
        self.assertIn("deviceMappings", mappings_payload)

        first_plan_id = floor_plans[0]["id"]
        svg_response = self.client.get(f"/api/floor-plans/{first_plan_id}/svg")
        mapping_response = self.client.get(f"/api/devices/{first_plan_id}")

        self.assertEqual(svg_response.status_code, 200)
        self.assertEqual(mapping_response.status_code, 200)
        self.assertIn("svg", svg_response.get_data(as_text=True).lower())
        self.assertIn("devices", mapping_response.get_json())

    def test_schema_compression_and_sse_endpoints_work(self):
        yaml_code = (
            "alias: 睡眠模式自动化\n"
            "trigger:\n"
            "  - platform: time\n"
            "    at: \"22:30:00\"\n"
            "action:\n"
            "  - service: scene.turn_on\n"
            "    target:\n"
            "      entity_id: scene.sleep_mode\n"
        )

        check_response = self.client.post("/api/check-code", json={"code": yaml_code, "autoFix": True})
        compress_response = self.client.post(
            "/api/compress-context",
            json={"text": "// comment\nsleep mode\nsleep mode\nscene.turn_on", "options": {"aggressive": True}},
        )
        sse_response = self.client.post(
            "/api/generate-tap",
            json={
                "message": "我准备睡觉了",
                "deviceMapping": {
                    "devices": [
                        {"entity_id": "light.bedroom_main", "area": "bedroom", "device_type": "light"}
                    ]
                },
            },
        )

        self.assertEqual(check_response.status_code, 200)
        self.assertEqual(compress_response.status_code, 200)
        self.assertEqual(sse_response.status_code, 200)

        check_payload = check_response.get_json()
        compress_payload = compress_response.get_json()
        sse_text = sse_response.get_data(as_text=True)

        self.assertTrue(check_payload["success"])
        self.assertIn("validation", check_payload)
        self.assertTrue(compress_payload["success"])
        self.assertIn("compressedText", compress_payload)
        self.assertIn("data:", sse_text)
        self.assertIn('"type": "complete"', sse_text)
        self.assertIn("[DONE]", sse_text)

    def test_tap_rule_endpoints_support_crud_and_evaluation(self):
        create_response = self.client.post(
            "/api/tap-rules",
            json={
                "alias": "Sleep trigger",
                "description": "Turn on scene when sleep scene selected",
                "trigger": [{"platform": "scene", "scene": "sleep"}],
                "condition": [],
                "action": [{"service": "notify.notify", "data": {"message": "sleep"}}],
            },
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.get_json()["rule"]
        rule_id = created["id"]

        list_response = self.client.get("/api/tap-rules")
        toggle_response = self.client.post(f"/api/tap-rules/{rule_id}/toggle", json={"enabled": False})
        evaluate_response = self.client.post("/api/tap-rules/evaluate", json={"event": {"platform": "scene", "scene": "sleep"}})
        delete_response = self.client.delete(f"/api/tap-rules/{rule_id}")

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(toggle_response.status_code, 200)
        self.assertEqual(evaluate_response.status_code, 200)
        self.assertEqual(delete_response.status_code, 200)
        self.assertIn("rules", list_response.get_json())
        self.assertFalse(toggle_response.get_json()["rule"]["enabled"])
        self.assertIn("evaluation", evaluate_response.get_json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
