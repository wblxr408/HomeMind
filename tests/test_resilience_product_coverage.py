import os
import unittest
from pathlib import Path

from core.memory import PreferenceStore, SessionStore
from core.rag.knowledge_base import KnowledgeBase
from tools.kb_write import KBWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
]


def _cleanup_keys():
    for path in KEY_FILES:
        if path.exists():
            path.unlink()


class PersistenceResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        _cleanup_keys()
        self.session_path = REPO_ROOT / "data" / "test_session_state.json"
        self.preference_path = REPO_ROOT / "data" / "test_preferences.json"
        self.backup_path = REPO_ROOT / "data" / "test_missing_backup.enc"
        for path in (self.session_path, self.preference_path, self.backup_path):
            path.unlink(missing_ok=True)

    def tearDown(self):
        for path in (self.session_path, self.preference_path, self.backup_path):
            path.unlink(missing_ok=True)
        _cleanup_keys()

    def test_session_store_recovers_from_corrupted_json(self):
        path = self.session_path
        path.write_text("{bad json", encoding="utf-8")

        store = SessionStore(path=str(path))

        snapshot = store.get_runtime_context()
        self.assertEqual(snapshot["current_scene"], "")
        self.assertEqual(snapshot["recent_turns"], [])
        self.assertEqual(snapshot["last_route"], "local")

    def test_preference_store_recovers_from_corrupted_json(self):
        path = self.preference_path
        path.write_text("{bad json", encoding="utf-8")

        store = PreferenceStore(path=str(path))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["devices"], {})
        self.assertEqual(snapshot["scenes"], {})
        self.assertEqual(snapshot["recommendation"], {})
        self.assertEqual(snapshot["language"], {"dialect_terms": {}})

    def test_preference_store_normalizes_wrong_field_shapes(self):
        path = self.preference_path
        path.write_text(
            '{"devices": [], "scenes": "bad", "recommendation": null, "language": {"dialect_terms": []}}',
            encoding="utf-8",
        )

        store = PreferenceStore(path=str(path))

        snapshot = store.snapshot()
        self.assertEqual(snapshot["devices"], {})
        self.assertEqual(snapshot["scenes"], {})
        self.assertEqual(snapshot["recommendation"], {})
        self.assertEqual(snapshot["language"]["dialect_terms"], {})

    def test_knowledge_base_restore_returns_false_for_missing_backup(self):
        kb = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"))
        kb._collection = None

        restored = kb.restore(path=str(self.backup_path))

        self.assertFalse(restored)

    def test_knowledge_base_aggregates_duplicate_high_value_events(self):
        kb = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"), max_records=10)
        kb._collection = None
        writer = KBWriter(kb)
        decision = {
            "action": "设备控制",
            "device": "灯光",
            "device_action": "adjust",
            "params": {"brightness": 30},
        }

        writer.write_feedback("太亮了", decision, "纠正")
        writer.write_feedback("太亮了", decision, "纠正")

        self.assertEqual(len(kb.memory_store), 1)
        self.assertEqual(kb.memory_store[0]["count"], 2)
        self.assertEqual(kb.memory_store[0]["category"], "纠正记录")

    def test_knowledge_base_prunes_to_max_records(self):
        kb = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"), max_records=3)
        kb._collection = None

        for index in range(5):
            kb.add(
                f"高价值事件{index}",
                category="用户反馈",
                accepted=False,
                memory_key=f"event-{index}",
                value_score=1.0 + index,
            )

        self.assertEqual(len(kb.memory_store), 3)
        keys = {item["memory_key"] for item in kb.memory_store}
        self.assertEqual(keys, {"event-2", "event-3", "event-4"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
