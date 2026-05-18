import os
import unittest
from pathlib import Path

from core.memory import PreferenceStore, SessionStore
from core.privacy import PrivacyRedactor
from core.security import ENCRYPTED_PICKLE_MAGIC, reset_encrypted_storage
from demo.context import HomeContext


REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_PATH = REPO_ROOT / "data" / "session_state.json"
PREFERENCE_PATH = REPO_ROOT / "data" / "preferences.json"
VOICE_PATH = REPO_ROOT / "data" / "voice_feedback.jsonl"


def _cleanup():
    for path in [SESSION_PATH, PREFERENCE_PATH, VOICE_PATH]:
        if path.exists():
            path.unlink()


class ContextPersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        os.environ["LLM_BACKEND"] = "mock"

    def setUp(self):
        _cleanup()
        reset_encrypted_storage()

    def tearDown(self):
        _cleanup()
        reset_encrypted_storage()

    def test_agent_persists_session_and_preference_after_scene_command(self):
        from main import HomeMindAgent

        agent = HomeMindAgent()
        result = agent.process("切换到睡眠模式")

        self.assertIn("睡眠模式", result)
        self.assertTrue(SESSION_PATH.exists())
        self.assertTrue(PREFERENCE_PATH.exists())
        self.assertTrue(SESSION_PATH.read_bytes().startswith(ENCRYPTED_PICKLE_MAGIC))
        self.assertTrue(PREFERENCE_PATH.read_bytes().startswith(ENCRYPTED_PICKLE_MAGIC))

        session_data = SessionStore(path=str(SESSION_PATH)).get_runtime_context()
        preference_data = PreferenceStore(path=str(PREFERENCE_PATH)).snapshot()

        self.assertEqual(session_data["current_scene"], "睡眠模式")
        self.assertEqual(session_data["last_action"]["scene"], "睡眠模式")
        self.assertEqual(preference_data["scenes"]["睡眠模式"]["accept_count"], 1)

    def test_new_agent_restores_latest_scene_from_session_store(self):
        from main import HomeMindAgent

        first_agent = HomeMindAgent()
        first_agent.process("切换到睡眠模式")

        second_agent = HomeMindAgent()

        self.assertEqual(second_agent.context.current_scene, "睡眠模式")
        self.assertEqual(second_agent.context.last_scene, 0)


class PrivacyRedactorTests(unittest.TestCase):
    def setUp(self):
        _cleanup()
        reset_encrypted_storage()
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def tearDown(self):
        _cleanup()
        reset_encrypted_storage()

    def test_redactor_builds_minimal_cloud_context(self):
        session_store = SessionStore(path=str(SESSION_PATH))
        preference_store = PreferenceStore(path=str(PREFERENCE_PATH))
        redactor = PrivacyRedactor()

        context = HomeContext(hour=22, temperature=28.0, humidity=72.0, members_home=2)
        context.current_scene = "观影模式"
        session_store.update_from_query("昨天像那样开一个", "切换到观影模式")
        preference_store.record_action_accept(
            {
                "action": "设备控制",
                "device": "空调",
                "device_action": "on",
                "params": {"temperature": 26},
            },
            context,
        )

        payload = redactor.build_cloud_context(
            context,
            [
                {"action": "打开空调", "score": 0.9},
                {"action": "打开风扇", "score": 0.8},
                {"action": "打开窗户", "score": 0.7},
                {"action": "切换观影模式", "score": 0.6},
            ],
            session_store=session_store,
            preference_store=preference_store,
        )

        self.assertEqual(
            sorted(payload.keys()),
            sorted(["hour", "temperature", "humidity", "occupancy", "scene", "top_candidates", "preference_summary"]),
        )
        self.assertEqual(payload["scene"], "观影模式")
        self.assertEqual(payload["top_candidates"], ["打开空调", "打开风扇", "打开窗户"])
        self.assertEqual(payload["preference_summary"]["preferred_ac_temp"], 26)
        self.assertNotIn("recent_turns", payload)
        self.assertNotIn("last_user_input", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
