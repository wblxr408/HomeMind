import os
import unittest
from datetime import datetime
from pathlib import Path

from core.automation import TAPEngine, TAPRuleStore
from demo.context import HomeContext


REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
    REPO_ROOT / "data" / "tap_rules.json",
    REPO_ROOT / "data" / "session_state.json",
    REPO_ROOT / "data" / "preferences.json",
]


def _cleanup():
    for path in DATA_FILES:
        if path.exists():
            path.unlink()


class TAPEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        _cleanup()

    def tearDown(self):
        _cleanup()

    def test_temperature_rule_matches_with_occupancy_condition(self):
        engine = TAPEngine()
        context = HomeContext(hour=21, temperature=31.0, humidity=60.0, members_home=2)
        context.current_scene = "回家模式"
        rules = [{
            "id": "rule_hot",
            "name": "高温开空调",
            "enabled": True,
            "priority": 10,
            "trigger": {"type": "temperature", "op": ">", "value": 30},
            "conditions": [{"type": "occupancy", "op": ">", "value": 0}],
            "action": {
                "type": "device_control",
                "device": "空调",
                "device_action": "on",
                "params": {"temperature": 26},
            },
        }]

        matches = engine.evaluate(context, rules)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["command"]["action"], "设备控制")
        self.assertEqual(matches[0]["command"]["device"], "空调")

    def test_time_scene_rule_matches(self):
        engine = TAPEngine()
        context = HomeContext(hour=22, temperature=26.0, humidity=50.0, members_home=1)
        context.current_scene = "观影模式"
        rules = [{
            "id": "rule_sleep",
            "name": "夜间睡眠",
            "enabled": True,
            "priority": 20,
            "trigger": {"type": "time", "at": "22:30"},
            "conditions": [{"type": "occupancy", "op": ">", "value": 0}],
            "action": {"type": "scene_switch", "scene": "睡眠模式"},
        }]

        matches = engine.evaluate(context, rules, now=datetime.strptime("22:30", "%H:%M"))

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["command"]["scene"], "睡眠模式")


class TAPWebApiTests(unittest.TestCase):
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

    def test_rule_crud_and_evaluate_execute(self):
        create_response = self.client.post("/api/rules", json={
            "name": "高温开空调",
            "enabled": True,
            "priority": 10,
            "trigger": {"type": "temperature", "op": ">", "value": 30},
            "conditions": [{"type": "occupancy", "op": ">", "value": 0}],
            "action": {
                "type": "device_control",
                "device": "空调",
                "device_action": "on",
                "params": {"temperature": 26},
            },
        })
        self.assertEqual(create_response.status_code, 200)
        rule = create_response.get_json()["rule"]
        self.assertTrue(rule["enabled"])

        list_response = self.client.get("/api/rules")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.get_json()["rules"]), 1)

        eval_response = self.client.post("/api/rules/evaluate", json={
            "execute": True,
            "time": "22:30",
            "context": {"temperature": 31.0, "members_home": 1},
        })
        self.assertEqual(eval_response.status_code, 200)
        payload = eval_response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertEqual(len(payload["matches"]), 1)
        self.assertEqual(payload["matches"][0]["execution"]["status"], "success")
        self.assertEqual(self.web_server.agent.device_control.get_state("空调").get("status"), "开")

        toggle_response = self.client.post(f"/api/rules/{rule['id']}/toggle", json={"enabled": False})
        self.assertEqual(toggle_response.status_code, 200)
        self.assertFalse(toggle_response.get_json()["rule"]["enabled"])

        delete_response = self.client.delete(f"/api/rules/{rule['id']}")
        self.assertEqual(delete_response.status_code, 200)

    def test_scheduler_status_and_toggle(self):
        status_response = self.client.get("/api/rules/scheduler")
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.get_json()["status"], "success")

        disable_response = self.client.post("/api/rules/scheduler", json={"enabled": False})
        self.assertEqual(disable_response.status_code, 200)
        self.assertFalse(disable_response.get_json()["enabled"])

        enable_response = self.client.post("/api/rules/scheduler", json={"enabled": True})
        self.assertEqual(enable_response.status_code, 200)
        self.assertTrue(enable_response.get_json()["enabled"])

    def test_scheduler_tick_executes_rule_once_per_minute(self):
        self.web_server.agent.tap_rule_store.add_rule({
            "name": "夜间睡眠",
            "enabled": True,
            "priority": 20,
            "trigger": {"type": "time", "at": "22:30"},
            "conditions": [{"type": "occupancy", "op": ">", "value": 0}],
            "action": {"type": "scene_switch", "scene": "睡眠模式"},
        })
        self.web_server.agent.scheduler_enabled = True

        first = self.web_server.agent._scheduler_tick(now=datetime.strptime("22:30", "%H:%M"))
        second = self.web_server.agent._scheduler_tick(now=datetime.strptime("22:30", "%H:%M"))

        self.assertEqual(len(first["executed"]), 1)
        self.assertEqual(len(second["executed"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
