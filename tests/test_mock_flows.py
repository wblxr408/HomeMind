import os
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_KEY_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
    REPO_ROOT / "data" / "session_state.json",
    REPO_ROOT / "data" / "preferences.json",
    REPO_ROOT / "data" / "tap_rules.json",
    REPO_ROOT / "data" / "test_dqn_models" / "dqn_policy.pkl",
    REPO_ROOT / "data" / "kb_backup.enc",
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

    def test_cli_mock_hot_query_controls_air_conditioner(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()

        result = agent.process("有点热")

        self.assertIn("空调", result)
        self.assertEqual(agent.device_ctrl.get_state("空调").get("status"), "开")
        self.assertEqual(agent.device_ctrl.get_state("空调").get("temperature"), 26)

    def test_cli_mock_scene_query_switches_sleep_scene(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()

        result = agent.process("切换到睡眠模式")

        self.assertIn("睡眠模式", result)
        self.assertEqual(agent.device_ctrl.get_state("灯光").get("brightness"), 10)
        self.assertEqual(agent.device_ctrl.get_state("电视").get("status"), "关")


class HomeMindWebMockFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        os.environ["HOMEMIND_DQN_MODEL_DIR"] = str(REPO_ROOT / "data" / "test_dqn_models")
        from web import server as web_server

        cls.web_server = web_server
        cls.web_server.init_agent(mode="simulated")
        cls.client = cls.web_server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.web_server.agent = None
        _cleanup_generated_files()
        os.environ.pop("HOMEMIND_DQN_MODEL_DIR", None)

    def test_status_endpoint_returns_context_devices_and_kb_status(self):
        response = self.client.get("/api/status")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("context", payload)
        self.assertIn("devices", payload)
        self.assertIn("kb_status", payload)
        self.assertIn("storage_security", payload)
        self.assertIn("agent_init_metrics", payload)
        self.assertIn("startup_metrics", payload)
        self.assertIn("air_conditioner", payload["devices"])

    def test_init_agent_reuses_existing_instance_without_restarting_threads(self):
        first_agent = self.web_server.agent
        first_agent_thread = getattr(first_agent, "agent_thread", None)
        first_scheduler_thread = getattr(first_agent, "scheduler_thread", None)
        initial_completed = self.web_server.agent_init_metrics["completed_count"]

        second_agent = self.web_server.init_agent(mode="simulated", init_reason="test_reuse")

        self.assertIs(first_agent, second_agent)
        self.assertIs(first_agent_thread, getattr(second_agent, "agent_thread", None))
        self.assertIs(first_scheduler_thread, getattr(second_agent, "scheduler_thread", None))
        self.assertEqual(self.web_server.agent_init_metrics["completed_count"], initial_completed)
        self.assertEqual(self.web_server.agent_init_metrics["last_reason"], "test_reuse")

    def test_query_endpoint_handles_device_and_scene_commands_in_mock_mode(self):
        hot_response = self.client.post("/api/query", json={"query": "有点热"})
        sleep_response = self.client.post("/api/query", json={"query": "我要睡觉了"})

        self.assertEqual(hot_response.status_code, 200)
        self.assertEqual(sleep_response.status_code, 200)

        hot_payload = hot_response.get_json()
        sleep_payload = sleep_response.get_json()

        self.assertEqual(hot_payload["status"], "success")
        self.assertEqual(hot_payload["action"], "空调_on")
        self.assertIn("空调", hot_payload["response"])

        self.assertEqual(sleep_payload["status"], "success")
        self.assertEqual(sleep_payload["action"], "scene_switch")
        self.assertIn("睡眠模式", sleep_payload["response"])

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
        probe_text = "kb_probe_unique_26_temp"
        info_response = self.client.get("/api/info/temperature")
        recommend_response = self.client.get("/api/dqn/recommend")
        feedback_response = self.client.post("/api/dqn/feedback", json={"id": "dqn_0", "response": "接受"})
        add_response = self.client.post(
            "/api/kb/add",
            json={"text": probe_text, "category": "测试"},
        )
        query_response = self.client.post("/api/kb/query", json={"query": probe_text, "top_k": 5})
        preferences_response = self.client.get("/api/preferences")
        memory_response = self.client.get("/api/memory/summary")
        privacy_response = self.client.get("/api/privacy/status")

        self.assertEqual(info_response.status_code, 200)
        self.assertEqual(recommend_response.status_code, 200)
        self.assertEqual(feedback_response.status_code, 200)
        self.assertEqual(add_response.status_code, 200)
        self.assertEqual(query_response.status_code, 200)
        self.assertEqual(preferences_response.status_code, 200)
        self.assertEqual(memory_response.status_code, 200)
        self.assertEqual(privacy_response.status_code, 200)

        info_payload = info_response.get_json()
        self.assertEqual(info_payload["status"], "success")
        self.assertIn("温度", info_payload["result"])
        self.assertIn(recommend_response.get_json()["status"], ("success", "no_recommendation"))
        feedback_payload = feedback_response.get_json()
        self.assertEqual(feedback_payload["status"], "success")
        self.assertTrue(feedback_payload["model_saved"])
        self.assertEqual(add_response.get_json()["status"], "success")
        self.assertEqual(preferences_response.get_json()["status"], "success")
        self.assertEqual(memory_response.get_json()["status"], "success")
        self.assertEqual(privacy_response.get_json()["status"], "success")
        self.assertIn("storage_security", privacy_response.get_json())

        kb_results = query_response.get_json()["results"]
        self.assertGreaterEqual(len(kb_results), 1)
        self.assertTrue(any(probe_text in item["content"] for item in kb_results))
        dqn_preferences = self.web_server.agent.preference_store.snapshot()["dqn"]
        self.assertGreaterEqual(len(dqn_preferences["feedback"]), 1)
        self.assertEqual(dqn_preferences["feedback"][-1]["reward"], 1.0)
        self.assertTrue(any(item.get("event_type") == "feedback" for item in self.web_server.agent.kb.memory_store))

    def test_voice_endpoint_requires_uploaded_audio(self):
        response = self.client.post("/api/voice/transcribe")

        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["status"], "error")


if __name__ == "__main__":
    unittest.main(verbosity=2)
