import tempfile
import unittest
from pathlib import Path

from core.language.normalizer import LanguageNormalizer
from core.voice.feedback_store import VoiceFeedbackStore


class VoiceFeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.gettempdir()) / "homemind_voice_feedback_test.jsonl"
        self.path.unlink(missing_ok=True)
        self.store = VoiceFeedbackStore(str(self.path))

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def test_corrected_feedback_is_persisted_and_used_by_normalizer(self):
        self.store.add({
            "asr_text": "turn on the thing",
            "normalized": "打开电视",
            "corrected_text": "turn on the ac",
            "corrected_normalized": "打开空调",
            "feedback": "corrected",
        })

        normalizer = LanguageNormalizer(feedback_store=self.store)
        result = normalizer.normalize("turn on the thing")

        self.assertEqual(result.normalized, "打开空调")
        self.assertEqual(result.matched_rule, "voice_feedback_history")
        self.assertEqual(result.confidence, 0.98)

    def test_ignored_feedback_does_not_override_rules(self):
        self.store.add({
            "asr_text": "turn on the ac",
            "normalized": "关闭空调",
            "corrected_normalized": "关闭空调",
            "feedback": "ignored",
        })

        normalizer = LanguageNormalizer(feedback_store=self.store)
        result = normalizer.normalize("turn on the ac")

        self.assertEqual(result.normalized, "打开空调")
        self.assertNotEqual(result.matched_rule, "voice_feedback_history")

    def test_recent_skips_malformed_lines(self):
        self.path.write_text("{bad json}\n", encoding="utf-8")
        saved = self.store.add({
            "asr_text": "lights brighter",
            "normalized": "调亮灯光",
            "feedback": "accepted",
        })

        self.assertEqual(self.store.recent(limit=10), [saved])


if __name__ == "__main__":
    unittest.main(verbosity=2)
