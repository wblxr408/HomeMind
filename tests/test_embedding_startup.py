import os
import sys
import types
import unittest

from core.utils import embedding


class EmbeddingStartupTests(unittest.TestCase):
    def setUp(self):
        self._original_env = {
            "HOMEMIND_EMBEDDING_ALLOW_NETWORK": os.environ.get("HOMEMIND_EMBEDDING_ALLOW_NETWORK"),
            "HOMEMIND_EMBEDDING_MODEL": os.environ.get("HOMEMIND_EMBEDDING_MODEL"),
            "HOMEMIND_EMBEDDING_CACHE_DIR": os.environ.get("HOMEMIND_EMBEDDING_CACHE_DIR"),
            "HF_HUB_OFFLINE": os.environ.get("HF_HUB_OFFLINE"),
            "TRANSFORMERS_OFFLINE": os.environ.get("TRANSFORMERS_OFFLINE"),
        }
        self._original_sentence_transformers = sys.modules.get("sentence_transformers")
        embedding.reset_model_state()

    def tearDown(self):
        embedding.reset_model_state()
        for key, value in self._original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if self._original_sentence_transformers is None:
            sys.modules.pop("sentence_transformers", None)
        else:
            sys.modules["sentence_transformers"] = self._original_sentence_transformers

    def _install_fake_sentence_transformers(self, sentence_transformer_cls):
        module = types.ModuleType("sentence_transformers")
        module.SentenceTransformer = sentence_transformer_cls
        sys.modules["sentence_transformers"] = module

    def test_get_model_defaults_to_local_cache_only(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, **kwargs):
                calls.append((model_name, dict(kwargs)))

        self._install_fake_sentence_transformers(FakeSentenceTransformer)
        os.environ.pop("HOMEMIND_EMBEDDING_ALLOW_NETWORK", None)

        model = embedding.get_model()

        self.assertIsNotNone(model)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], embedding.DEFAULT_MODEL_NAME)
        self.assertTrue(calls[0][1]["local_files_only"])

    def test_get_model_skips_network_fallback_by_default(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, **kwargs):
                calls.append((model_name, dict(kwargs)))
                raise RuntimeError("local cache missing")

        self._install_fake_sentence_transformers(FakeSentenceTransformer)
        os.environ.pop("HOMEMIND_EMBEDDING_ALLOW_NETWORK", None)

        model = embedding.get_model()

        self.assertIsNone(model)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0][1]["local_files_only"])

    def test_get_model_can_fallback_to_network_when_explicitly_enabled(self):
        calls = []

        class FakeSentenceTransformer:
            def __init__(self, model_name, **kwargs):
                calls.append((model_name, dict(kwargs)))
                if kwargs.get("local_files_only"):
                    raise RuntimeError("local cache missing")

        self._install_fake_sentence_transformers(FakeSentenceTransformer)
        os.environ["HOMEMIND_EMBEDDING_ALLOW_NETWORK"] = "1"

        model = embedding.get_model()

        self.assertIsNotNone(model)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[0][1]["local_files_only"])
        self.assertNotIn("local_files_only", calls[1][1])


if __name__ == "__main__":
    unittest.main(verbosity=2)
