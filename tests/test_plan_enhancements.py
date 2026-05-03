import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from core.automation.nl_to_tap import NLToTAPConverter
from core.automation.scene_store import SceneStore
from core.automation.tap_engine import TAPEngine
from core.execution import CommandValidator
from core.llm.cloud_client import CloudClient
from core.protocols.smart_home_gateway import HomeAssistantProtocol, MQTTProtocol
from demo.context import HomeContext
from tools.scene_switch import SceneSwitcher


class SceneStoreEnhancementTests(unittest.TestCase):
    def test_scene_store_persists_crud_operations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scenes.json"
            store = SceneStore(path=str(path))

            created = store.add_scene("健身模式", {
                "灯光": {"action": "off", "params": {}},
                "空调": {"action": "on", "params": {"temperature": 24}},
            })
            self.assertEqual(created["灯光"]["action"], "off")
            self.assertIn("健身模式", SceneStore(path=str(path)).list_scenes())

            updated = store.update_scene("健身模式", {"风扇": {"action": "on", "params": {}}})
            self.assertEqual(updated["风扇"]["action"], "on")
            self.assertTrue(store.delete_scene("健身模式"))
            self.assertIsNone(store.get_scene("健身模式"))

    def test_scene_switcher_uses_persisted_scene_store(self):
        class FakeDeviceController:
            def __init__(self):
                self.calls = []

            def execute(self, device, action, params):
                self.calls.append((device, action, params))
                return f"{device}:{action}"

        with tempfile.TemporaryDirectory() as tmpdir:
            store = SceneStore(path=str(Path(tmpdir) / "scenes.json"))
            store.add_scene("健身模式", {"灯光": {"action": "off", "params": {}}})
            controller = FakeDeviceController()

            result = SceneSwitcher(controller, scene_store=store).execute("健身模式")

            self.assertIn("已切换到健身模式", result)
            self.assertEqual(controller.calls, [("灯光", "off", {})])

    def test_command_validator_accepts_dynamic_scene_store_entries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = SceneStore(path=str(Path(tmpdir) / "scenes.json"))
            store.add_scene("健身模式", {"灯光": {"action": "off", "params": {}}})
            validator = CommandValidator(scene_store=store)

            result = validator.validate({
                "action": "场景切换",
                "scene": "健身模式",
                "device_action": "scene",
                "params": {},
                "confidence": 0.9,
            })

            self.assertTrue(result.valid)


class NLToTAPEnhancementTests(unittest.TestCase):
    def test_nl_to_tap_parses_time_scene_rule(self):
        rule = NLToTAPConverter().parse("每天 07:30 切换早安模式")

        self.assertEqual(rule["trigger"], {"type": "time", "at": "07:30"})
        self.assertEqual(rule["action"], {"type": "scene_switch", "scene": "早安模式"})

    def test_nl_to_tap_parses_weekend_trigger(self):
        rule = NLToTAPConverter().parse("周末打开空调")

        self.assertEqual(rule["trigger"], {"type": "day_of_week", "days": [5, 6]})
        self.assertEqual(rule["action"]["device"], "空调")
        self.assertEqual(rule["action"]["device_action"], "on")

    def test_nl_to_tap_parses_may_day_as_fixed_holiday(self):
        rule = NLToTAPConverter().parse("五一的时候给我关掉空调")

        self.assertEqual(rule["trigger"], {"type": "holiday", "name": "五一", "month": 5, "day": 1})
        self.assertEqual(rule["action"]["device"], "空调")
        self.assertEqual(rule["action"]["device_action"], "off")

    def test_nl_to_tap_extracts_trigger_without_action_for_followup(self):
        trigger = NLToTAPConverter().extract_trigger("五一的时候")

        self.assertEqual(trigger, {"type": "holiday", "name": "五一", "month": 5, "day": 1})

    def test_nl_to_tap_parses_scene_creation_actions_per_clause(self):
        parsed = NLToTAPConverter().parse_scene_creation(
            "创建一个叫健身模式的场景，关闭所有灯，打开空调"
        )

        self.assertEqual(parsed["name"], "健身模式")
        self.assertEqual(parsed["config"]["灯光"]["action"], "off")
        self.assertEqual(parsed["config"]["空调"]["action"], "on")

    def test_tap_engine_supports_day_of_week_trigger(self):
        engine = TAPEngine()
        context = HomeContext(day_of_week=6)
        rules = [{
            "enabled": True,
            "priority": 10,
            "trigger": {"type": "day_of_week", "days": [5, 6]},
            "conditions": [],
            "action": {"type": "scene_switch", "scene": "早安模式"},
        }]

        matches = engine.evaluate(context, rules, now=datetime.strptime("08:00", "%H:%M"))

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["command"]["scene"], "早安模式")

    def test_tap_engine_supports_fixed_holiday_trigger(self):
        engine = TAPEngine()
        context = HomeContext()
        rules = [{
            "enabled": True,
            "priority": 10,
            "trigger": {"type": "holiday", "name": "五一", "month": 5, "day": 1},
            "conditions": [],
            "action": {"type": "device_control", "device": "空调", "device_action": "off", "params": {}},
        }]

        matches = engine.evaluate(context, rules, now=datetime(2026, 5, 1, 8, 0))
        misses = engine.evaluate(context, rules, now=datetime(2026, 4, 30, 8, 0))

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["command"]["device"], "空调")
        self.assertEqual(misses, [])


class ProtocolSecurityEnhancementTests(unittest.TestCase):
    def test_cloud_client_forces_https_api_base_before_client_init(self):
        previous = os.environ.get("LLM_API_KEY")
        os.environ["LLM_API_KEY"] = "test-key"
        try:
            client = CloudClient(api_base="http://example.com/v1", api_key="test-key", model="test")
            self.assertEqual(client.api_base, "https://example.com/v1")
        finally:
            if previous is None:
                os.environ.pop("LLM_API_KEY", None)
            else:
                os.environ["LLM_API_KEY"] = previous

    def test_cloud_client_does_not_retain_raw_payloads_by_default(self):
        client = CloudClient(api_base="https://example.com/v1", api_key="", model="test")
        status = client.logging_status()

        self.assertEqual(status["policy"], "none")
        self.assertFalse(status["raw_payload_retained"])
        self.assertEqual(status["path"], "")

    def test_mqtt_tls_defaults_to_secure_port(self):
        protocol = MQTTProtocol(use_tls=True)

        self.assertTrue(protocol.use_tls)
        self.assertEqual(protocol.port, 8883)

    def test_home_assistant_forces_https(self):
        protocol = HomeAssistantProtocol(url="http://192.168.1.200:8123")

        self.assertEqual(protocol.url, "https://192.168.1.200:8123")


if __name__ == "__main__":
    unittest.main(verbosity=2)
