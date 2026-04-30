import os
import time
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
    REPO_ROOT / "data" / "test_dqn_models" / "dqn_policy.pkl",
    REPO_ROOT / "data" / "kb_backup.enc",
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
        os.environ["HOMEMIND_DQN_MODEL_DIR"] = str(REPO_ROOT / "data" / "test_dqn_models")
        from web import server as web_server

        cls.web_server = web_server
        cls.web_server.init_agent(mode="simulated")
        cls.client = cls.web_server.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.web_server.agent = None
        _cleanup()
        os.environ.pop("HOMEMIND_DQN_MODEL_DIR", None)

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


    def test_scheduler_runs_dqn_daily_learning_once_per_date(self):
        agent = self.web_server.agent
        original_dqn = agent.dqn
        original_dqn_fb = agent.dqn_fb

        class StubDQN:
            def __init__(self):
                self.calls = []
                self.save_count = 0

            def daily_incremental_update(self):
                self.calls.append(len(self.calls) + 1)
                return {
                    "status": "updated",
                    "trigger": "daily",
                    "buffer_size": 12,
                    "epsilon": 0.25,
                    "update_counter": len(self.calls),
                }

            def save(self):
                self.save_count += 1
                return True

        stub = StubDQN()
        try:
            agent.dqn = stub
            agent.dqn_fb = None
            agent.scheduler_enabled = True
            agent._last_dqn_daily_learning_date = ""
            agent.dqn_scheduler_interval = 10**9
            agent._last_dqn_recommend_at = time.time()

            first = agent._scheduler_tick(now=datetime(2026, 1, 1, 3, 0))
            second = agent._scheduler_tick(now=datetime(2026, 1, 1, 4, 0))
            third = agent._scheduler_tick(now=datetime(2026, 1, 2, 3, 0))

            self.assertEqual(len(stub.calls), 2)
            self.assertEqual(stub.save_count, 2)
            self.assertEqual([item.get("type") for item in first["executed"]], ["dqn_daily_learning"])
            self.assertEqual(second["executed"], [])
            self.assertEqual([item.get("type") for item in third["executed"]], ["dqn_daily_learning"])
            self.assertGreaterEqual(len(agent.preference_store.snapshot()["dqn"]["learning"]), 2)
            self.assertTrue(any(item.get("event_type") == "daily_learning" for item in agent.kb.memory_store))
        finally:
            agent.dqn = original_dqn
            agent.dqn_fb = original_dqn_fb


if __name__ == "__main__":
    unittest.main(verbosity=2)
