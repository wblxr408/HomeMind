import unittest

from core.automation import TapRuleEngine
from core.schema import validate_device_command, validate_device_mapping_payload, validate_tap_code, validate_tap_rule_payload


class SchemaValidationTests(unittest.TestCase):
    def test_device_mapping_accepts_object_rows(self):
        result = validate_device_mapping_payload(
            {"devices": [{"entity_id": "light.bedroom_main", "area": "bedroom", "device_type": "light"}]},
            supported_device_types={"light", "fan"},
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.tuples, [["light.bedroom_main", "bedroom", "light"]])

    def test_device_command_rejects_invalid_brightness(self):
        result = validate_device_command("light", {"action": "adjust", "params": {"brightness": 120}})

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].field, "brightness")

    def test_tap_yaml_validation_parses_structured_rule(self):
        yaml_code = (
            'alias: "Sleep rule"\n'
            'trigger:\n'
            '  - platform: time\n'
            '    at: "22:30:00"\n'
            'condition:\n'
            '  - condition: state\n'
            '    entity_id: group.family\n'
            '    state: home\n'
            'action:\n'
            '  - service: scene.turn_on\n'
            '    target:\n'
            '      entity_id: scene.sleep_mode\n'
        )

        payload = validate_tap_code(yaml_code, auto_fix=True)

        self.assertTrue(payload["validation"]["valid"])
        self.assertEqual(payload["parsedRule"]["alias"], "Sleep rule")

    def test_structured_rule_payload_requires_trigger_and_action(self):
        result = validate_tap_rule_payload({"alias": "broken"})

        self.assertFalse(result.valid)
        self.assertGreaterEqual(len(result.errors), 2)


class TapRuleEngineTests(unittest.TestCase):
    def test_engine_executes_matching_state_rule(self):
        engine = TapRuleEngine()
        rules = [{
            "id": "rule-1",
            "alias": "Light on rule",
            "enabled": True,
            "trigger": [{"platform": "state", "entity_id": "light", "to": "on"}],
            "condition": [{"condition": "scene", "scene": "sleep"}],
            "action": [{"service": "notify.notify", "data": {"message": "ok"}}],
        }]
        executed = []

        result = engine.evaluate(
            rules,
            event={"platform": "state", "entity_id": "light", "from": "off", "to": "on"},
            snapshot={"devices": {"light": {"is_on": True}}, "context": {"scene": "sleep", "occupancy": 1, "time": "22:31:00"}},
            executor=lambda action: executed.append(action) or {"status": "success", "service": action["service"]},
        )

        self.assertEqual(result.matched_rules, ["rule-1"])
        self.assertEqual(len(result.executed_rules), 1)
        self.assertEqual(executed[0]["service"], "notify.notify")

    def test_engine_skips_disabled_rule(self):
        engine = TapRuleEngine()
        result = engine.evaluate(
            [{
                "id": "rule-off",
                "alias": "Disabled",
                "enabled": False,
                "trigger": [{"platform": "scene", "scene": "sleep"}],
                "condition": [],
                "action": [{"service": "notify.notify", "data": {"message": "skip"}}],
            }],
            event={"platform": "scene", "scene": "sleep"},
            snapshot={"devices": {}, "context": {"scene": "sleep", "occupancy": 1, "time": "21:00:00"}},
        )

        self.assertEqual(result.executed_rules, [])
        self.assertEqual(result.skipped_rules[0]["reason"], "disabled")


if __name__ == "__main__":
    unittest.main(verbosity=2)
