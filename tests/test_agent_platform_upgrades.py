import json
import os
import tempfile
import unittest
from pathlib import Path

from core.governance import PolicyEngine, PolicyRule
from core.rag.knowledge_base import KnowledgeBase
from core.tools import ToolRegistry
from tools.device_control import DeviceController


class PolicyEngineUpgradeTests(unittest.TestCase):
    def test_default_policies_are_coerced_and_sorted(self):
        engine = PolicyEngine(policy_dir="nonexistent_policy_dir")
        policies = engine.get_policies()
        self.assertTrue(policies)
        self.assertEqual(policies[0]["name"], "deny_rate_limited_operations")

    def test_rule_matching_requires_conjunction(self):
        engine = PolicyEngine(policy_dir="nonexistent_policy_dir")
        engine.add_policy(
            PolicyRule(
                name="night_window_confirm",
                condition={"device": ["窗户"], "denied_hours": [23]},
                effect="confirm",
                priority=300,
            )
        )
        result = engine.evaluate({"device": "窗户", "hour": 23, "risk_level": "low"})
        self.assertEqual(result["policy"], "night_window_confirm")
        mismatch = engine.evaluate({"device": "窗户", "hour": 12, "risk_level": "low"})
        self.assertNotEqual(mismatch["policy"], "night_window_confirm")


class ToolRegistryUpgradeTests(unittest.TestCase):
    def test_registry_executes_bound_device_tool(self):
        registry = ToolRegistry()
        controller = DeviceController()
        registry.bind("device_control", controller)
        result = registry.execute(
            {
                "action": "设备控制",
                "device": "灯光",
                "device_action": "on",
                "params": {"brightness": 100},
            }
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(controller.get_state("灯光").get("status"), "开")


class KnowledgeBaseUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        self.backup_path = Path(self.tmp.name) / "kb.enc"
        self.kb = KnowledgeBase(
            persist_dir=str(Path(self.tmp.name) / "chroma"),
            backup_path=str(self.backup_path),
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_multisource_conflict_and_timeseries_are_recorded(self):
        self.kb.add(
            "空调最佳温度为 24 度",
            category="用户反馈",
            fact_key="ac.temp",
            fact_value=24,
            source_bucket="feedback",
        )
        self.kb.add(
            "空调最佳温度为 26 度",
            category="纠正记录",
            fact_key="ac.temp",
            fact_value=26,
            source_bucket="corrections",
        )
        conflicts = self.kb.list_conflicts()
        self.assertTrue(conflicts)
        self.kb.add_timeseries_summary("场景=sleep 温度=26 湿度=60 触发=query 路由=local 设备=空调:开")
        status = self.kb.get_status()
        self.assertGreaterEqual(status["timeseries_count"], 1)
        context = self.kb.get_context_prompt("空调温度", type("Ctx", (), {"hour": 22, "temperature": 26, "humidity": 60, "current_scene": "sleep"})())
        self.assertIn("时序摘要", context)


class AuditApiUpgradeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        os.environ["LLM_BACKEND"] = "mock"
        from web import server as web_server

        cls.web_server = web_server
        cls.web_server.init_agent(mode="simulated", force_reinit=True)
        cls.client = cls.web_server.app.test_client()

    def test_audit_endpoint_returns_records_after_query(self):
        self.client.post("/api/query", json={"query": "打开灯光"})
        response = self.client.get("/api/audit/logs?limit=5")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "success")
        self.assertGreaterEqual(payload["count"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
