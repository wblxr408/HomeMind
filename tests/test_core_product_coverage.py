import os
import tempfile
import unittest
from pathlib import Path

import numpy as np

from core.bsr.candidate_recall import BSRecall
from core.dqn.policy import DQNPolicy, QNetwork, ReplayBuffer
from core.llm.decision import LLMDecider
from core.rag.knowledge_base import KnowledgeBase
from demo.context import HomeContext


REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_FILES = [
    REPO_ROOT / "data" / ".key",
    REPO_ROOT / "data" / ".key.salt",
]

USER_HABIT = "\u7528\u6237\u4e60\u60ef"
OPEN_AC = "\u6253\u5f00\u7a7a\u8c03"
OPEN_FAN = "\u6253\u5f00\u98ce\u6247"
UNABLE_TO_UNDERSTAND = "\u65e0\u6cd5\u7406\u89e3"
DEVICE_CONTROL = "\u8bbe\u5907\u63a7\u5236"
AC_DEVICE = "\u7a7a\u8c03"
INFO_QUERY = "\u4fe1\u606f\u67e5\u8be2"
ACCEPTED = "\u63a5\u53d7"
HOT = "\u70ed"
AC_KEYWORD = "\u7a7a\u8c03"


def _cleanup_keys():
    for path in KEY_FILES:
        if path.exists():
            path.unlink()


class FakeKnowledgeBase:
    def __init__(self, records=None):
        self.records = records or []
        self.calls = []

    def query(self, text, top_k=3, category=None):
        self.calls.append({"text": text, "top_k": top_k, "category": category})
        if category is None:
            return self.records[:top_k]
        return [item for item in self.records if item.get("category") == category][:top_k]


class KnowledgeBaseProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        _cleanup_keys()

    def tearDown(self):
        _cleanup_keys()

    def test_add_query_and_category_filter_use_memory_fallback(self):
        kb = KnowledgeBase(persist_dir=tempfile.mkdtemp())
        kb._collection = None
        kb.add("thermostat target 26", category="habit", accepted=True)
        kb.add("movie scene lowers lamp", category="scene", accepted=True)

        habit_results = kb.query("thermostat 26", top_k=3, category="habit")
        missing_results = kb.query("thermostat 26", top_k=3, category="missing")

        self.assertEqual(len(habit_results), 1)
        self.assertEqual(habit_results[0]["category"], "habit")
        self.assertIn("26", habit_results[0]["content"])
        self.assertEqual(missing_results, [])

    def test_empty_query_without_keyword_overlap_returns_no_context(self):
        kb = KnowledgeBase(persist_dir=tempfile.mkdtemp())
        kb._collection = None

        results = kb.query("zzzz-no-overlap", top_k=2, category="missing")
        prompt = kb.get_context_prompt("zzzz-no-overlap", HomeContext())

        self.assertEqual(results, [])
        self.assertEqual(prompt, "")

    def test_preference_score_uses_positive_feedback_history(self):
        kb = KnowledgeBase(persist_dir=tempfile.mkdtemp())
        kb._collection = None
        kb.add(f"action {OPEN_AC} accepted before", category=USER_HABIT, accepted=True)

        score = kb.get_user_preference_score(OPEN_AC, HomeContext())

        self.assertGreaterEqual(score, 0.7)


class BSRProductTests(unittest.TestCase):
    def test_rule_recall_deduplicates_candidates_and_caps_top_k(self):
        kb = FakeKnowledgeBase()
        recall = BSRecall(kb, top_k=2)

        candidates = recall.recall(f"{HOT}{AC_KEYWORD}", HomeContext())

        self.assertLessEqual(len(candidates), 2)
        self.assertEqual(len({item["action"] for item in candidates}), len(candidates))
        self.assertEqual(candidates[0]["source"], "rule")
        self.assertEqual(kb.calls[-1]["category"], USER_HABIT)

    def test_history_recall_extracts_actions_from_user_habits(self):
        kb = FakeKnowledgeBase([
            {
                "content": f"when it is humid, action {OPEN_FAN} worked",
                "category": USER_HABIT,
                "accepted": True,
            }
        ])
        recall = BSRecall(kb, top_k=5)

        candidates = recall.recall("humid evening", HomeContext())

        self.assertIn(
            {"action": OPEN_FAN, "source": "history", "score": 0.95},
            candidates,
        )

    def test_no_candidate_returns_safe_fallback(self):
        recall = BSRecall(FakeKnowledgeBase(), top_k=5)

        candidates = recall.recall("no matching product request", HomeContext())

        self.assertEqual(candidates, [{"action": UNABLE_TO_UNDERSTAND, "source": "fallback", "score": 0.0}])


class LLMDecisionProductTests(unittest.TestCase):
    def test_mock_decider_maps_top_device_candidate_to_structured_command(self):
        decider = LLMDecider(backend="mock")

        decision = decider.decide(
            HOT,
            [{"action": OPEN_AC, "final_score": 0.62}],
            HomeContext(),
        )

        self.assertEqual(decision["action"], DEVICE_CONTROL)
        self.assertEqual(decision["device"], AC_DEVICE)
        self.assertEqual(decision["device_action"], "on")
        self.assertEqual(decision["params"]["temperature"], 26)
        self.assertGreaterEqual(decision["confidence"], 0.9)

    def test_parse_output_recovers_json_embedded_in_text(self):
        decider = LLMDecider(backend="mock")

        parsed = decider._parse_output(
            f'prefix {{"action": "{INFO_QUERY}", "query_type": "temperature", "confidence": 0.88}} suffix'
        )

        self.assertEqual(parsed["action"], INFO_QUERY)
        self.assertEqual(parsed["query_type"], "temperature")
        self.assertEqual(parsed["device"], "")
        self.assertEqual(parsed["params"], {})

    def test_parse_output_returns_low_confidence_fallback_for_invalid_json(self):
        decider = LLMDecider(backend="mock")

        parsed = decider._parse_output("not-json")

        self.assertEqual(parsed["action"], UNABLE_TO_UNDERSTAND)
        self.assertEqual(parsed["confidence"], 0.0)


class DQNProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HOMEMIND_STORAGE_KEY"] = "test-storage-key"

    def setUp(self):
        _cleanup_keys()

    def tearDown(self):
        _cleanup_keys()

    def test_replay_buffer_keeps_latest_items_when_capacity_is_exceeded(self):
        replay = ReplayBuffer(capacity=2)
        state = np.zeros(5, dtype=np.float32)

        replay.push(state, 0, 0.0, state)
        replay.push(state, 1, 0.1, state)
        replay.push(state, 2, 0.2, state)

        self.assertEqual(len(replay), 2)
        self.assertEqual({item["action"] for item in replay.buffer}, {1, 2})

    def test_q_network_output_shape_matches_scene_action_space(self):
        q_net = QNetwork(seed=123)

        q_values = q_net.forward(np.zeros(7, dtype=np.float32))

        self.assertEqual(q_values.shape, (9,))
        self.assertEqual(q_net.num_params(), 5257)

    def test_feedback_records_reward_and_preserves_valid_recommendation_range(self):
        policy = DQNPolicy.__new__(DQNPolicy)
        policy.q_net = QNetwork(seed=123)
        policy.target_net = QNetwork(seed=123)
        policy.replay = ReplayBuffer()
        policy.epsilon = 0.0
        policy.gamma = 0.95
        policy.lr = 0.01
        policy.update_counter = 0
        policy.update_freq = 50
        context = HomeContext(hour=22, temperature=25.0, humidity=50.0, members_home=2)
        before = len(policy.replay)

        ok = policy.record_feedback(context, 0, ACCEPTED)
        action, confidence = policy.recommend(context)

        self.assertTrue(ok)
        self.assertEqual(len(policy.replay), before + 1)
        self.assertEqual(policy.last_feedback_event["reward"], 1.0)
        self.assertEqual(policy.last_feedback_event["buffer_size"], before + 1)
        self.assertGreaterEqual(action, 0)
        self.assertLessEqual(action, 8)
        self.assertIsInstance(confidence, float)

    def test_daily_incremental_update_saves_dqn_policy_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            policy = DQNPolicy(model_dir=tmp_dir, seed=123)
            context = HomeContext(hour=22, temperature=25.0, humidity=50.0, members_home=2)
            while len(policy.replay) < 10:
                policy.record_feedback(context, 0, ACCEPTED)

            summary = policy.daily_incremental_update()
            saved = policy.save()

            self.assertEqual(summary["status"], "updated")
            self.assertEqual(summary["trigger"], "daily")
            self.assertTrue(saved)
            self.assertTrue((Path(tmp_dir) / "dqn_policy.pkl").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
