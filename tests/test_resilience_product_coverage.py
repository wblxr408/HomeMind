import os
import unittest
from pathlib import Path

from core.memory import PreferenceStore, SessionStore
from core.rag.knowledge_base import KnowledgeBase
from core.security import ENCRYPTED_PICKLE_MAGIC, EncryptedStorage, reset_encrypted_storage
from core.voice.feedback_store import VoiceFeedbackStore
from tools.kb_write import KBWriter


REPO_ROOT = Path(__file__).resolve().parents[1]
class PersistenceResilienceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"
        self.session_path = REPO_ROOT / "data" / "test_session_state.json"
        self.preference_path = REPO_ROOT / "data" / "test_preferences.json"
        self.voice_path = REPO_ROOT / "data" / "test_voice_feedback.jsonl"
        self.backup_path = REPO_ROOT / "data" / "test_missing_backup.enc"
        for path in (self.session_path, self.preference_path, self.voice_path, self.backup_path):
            path.unlink(missing_ok=True)
        reset_encrypted_storage()

    def tearDown(self):
        for path in (self.session_path, self.preference_path, self.voice_path, self.backup_path):
            path.unlink(missing_ok=True)
        reset_encrypted_storage()

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

    def test_knowledge_base_status_reports_fallback_state(self):
        kb = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"))
        kb._collection = None

        status = kb.get_status()

        self.assertIn("chromadb_importable", status)
        self.assertFalse(status["chromadb_enabled"])
        self.assertEqual(status["memory_store_count"], 0)

    def test_restore_rehydrates_collection_records_when_collection_exists(self):
        class FakeCollection:
            def __init__(self):
                self.docs = {}

            def upsert(self, embeddings=None, documents=None, metadatas=None, ids=None):
                for doc, meta, record_id in zip(documents or [], metadatas or [], ids or []):
                    self.docs[record_id] = {"document": doc, "metadata": meta}

            def delete(self, ids=None):
                for record_id in ids or []:
                    self.docs.pop(record_id, None)

            def count(self):
                return len(self.docs)

        backup_path = REPO_ROOT / "data" / "test_kb_backup.enc"
        backup_path.unlink(missing_ok=True)
        try:
            kb = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"))
            kb._collection = None
            kb.add(
                "rehydrate me",
                category="用户反馈",
                accepted=True,
                memory_key="rehydrate-key",
                record_id="rehydrate-id",
            )
            self.assertTrue(kb.backup(path=str(backup_path)))

            restored = KnowledgeBase(persist_dir=str(REPO_ROOT / "data" / "chroma_db"))
            restored._collection = FakeCollection()

            ok = restored.restore(path=str(backup_path))

            self.assertTrue(ok)
            self.assertEqual(len(restored.memory_store), 1)
            self.assertEqual(restored._collection.count(), 1)
            self.assertIn("rehydrate-id", restored._collection.docs)
            self.assertEqual(restored._collection.docs["rehydrate-id"]["document"], "rehydrate me")
        finally:
            backup_path.unlink(missing_ok=True)

    def test_encrypted_storage_requires_external_key_and_creates_no_local_key_files(self):
        os.environ.pop("HOMEMIND_STORAGE_KEY", None)
        reset_encrypted_storage()

        storage = EncryptedStorage()

        self.assertFalse(storage.is_available())
        self.assertEqual(storage.status()["reason"], "missing_key")
        self.assertFalse((REPO_ROOT / "data" / ".key").exists())
        self.assertFalse((REPO_ROOT / "data" / ".key.salt").exists())

    def test_sensitive_stores_do_not_write_plaintext_without_key(self):
        os.environ.pop("HOMEMIND_STORAGE_KEY", None)
        reset_encrypted_storage()

        session_store = SessionStore(path=str(self.session_path))
        preference_store = PreferenceStore(path=str(self.preference_path))
        voice_store = VoiceFeedbackStore(path=str(self.voice_path))

        session_store.update_from_query("你好", "你好")
        preference_store.record_feedback("早安", "切换早安模式", "纠正")
        voice_store.add({"asr_text": "hello", "normalized": "hello", "feedback": "accepted"})

        self.assertFalse(self.session_path.exists())
        self.assertFalse(self.preference_path.exists())
        self.assertFalse(self.voice_path.exists())

    def test_session_store_migrates_plaintext_json_to_encrypted_blob(self):
        self.session_path.write_text('{"current_scene":"睡眠模式","recent_turns":[],"last_route":"local"}', encoding="utf-8")
        reset_encrypted_storage()

        store = SessionStore(path=str(self.session_path))
        self.assertTrue(store.legacy_plaintext_loaded)
        self.assertEqual(store.get_current_scene(), "睡眠模式")

        self.assertTrue(store.save())
        self.assertTrue(self.session_path.read_bytes().startswith(ENCRYPTED_PICKLE_MAGIC))
        self.assertNotIn(b'"current_scene"', self.session_path.read_bytes())

    def test_voice_feedback_store_migrates_plaintext_jsonl_to_encrypted_blob(self):
        self.voice_path.write_text(
            '{"timestamp":"2026-01-01T00:00:00","asr_text":"hello","normalized":"打开空调","feedback":"corrected"}\n',
            encoding="utf-8",
        )
        reset_encrypted_storage()

        store = VoiceFeedbackStore(path=str(self.voice_path))
        records = store.recent()
        self.assertTrue(store.legacy_plaintext_loaded)
        self.assertEqual(records[0]["normalized"], "打开空调")

        store.add({"asr_text": "hello again", "normalized": "打开灯光", "feedback": "accepted"})
        self.assertTrue(self.voice_path.read_bytes().startswith(ENCRYPTED_PICKLE_MAGIC))


if __name__ == "__main__":
    unittest.main(verbosity=2)
