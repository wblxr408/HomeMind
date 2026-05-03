import sys
import types
import unittest

import core.llm.decision as decision_module
from core.llm.decision import LLMDecider


class LlamaRuntimeFallbackTests(unittest.TestCase):
    def _build_decider(self, fake_module, cfg):
        original_module = sys.modules.get("llama_cpp")
        original_cfg = decision_module.LLAMA_CPP_CONFIG
        try:
            sys.modules["llama_cpp"] = fake_module
            decision_module.LLAMA_CPP_CONFIG = cfg
            return LLMDecider(backend="llama_cpp")
        finally:
            decision_module.LLAMA_CPP_CONFIG = original_cfg
            if original_module is None:
                sys.modules.pop("llama_cpp", None)
            else:
                sys.modules["llama_cpp"] = original_module

    def test_auto_mode_falls_back_to_cpu_when_gpu_init_fails(self):
        calls = []

        class FakeLlama:
            def __init__(self, **kwargs):
                calls.append(kwargs["n_gpu_layers"])
                if kwargs["n_gpu_layers"] != 0:
                    raise RuntimeError("gpu unavailable")

        fake_module = types.SimpleNamespace(Llama=FakeLlama)
        cfg = {
            "model_path": "models/test.gguf",
            "n_ctx": 2048,
            "n_threads": 4,
            "n_gpu_layers": -1,
            "gpu_mode": "auto",
            "use_mlock": True,
        }
        decider = self._build_decider(fake_module, cfg)

        self.assertEqual(calls, [-1, 0])
        self.assertEqual(decider.backend, "llama_cpp")
        self.assertEqual(decider._llama_runtime["effective_gpu_layers"], 0)
        self.assertEqual(decider._llama_runtime["gpu_mode"], "cpu_fallback")

    def test_force_mode_does_not_retry_cpu(self):
        calls = []

        class FakeLlama:
            def __init__(self, **kwargs):
                calls.append(kwargs["n_gpu_layers"])
                raise RuntimeError("gpu unavailable")

        fake_module = types.SimpleNamespace(Llama=FakeLlama)
        cfg = {
            "model_path": "models/test.gguf",
            "n_ctx": 2048,
            "n_threads": 4,
            "n_gpu_layers": -1,
            "gpu_mode": "force",
            "use_mlock": True,
        }
        decider = self._build_decider(fake_module, cfg)

        self.assertEqual(calls, [-1])
        self.assertEqual(decider.backend, "mock")

    def test_cpu_mode_skips_gpu_attempt(self):
        calls = []

        class FakeLlama:
            def __init__(self, **kwargs):
                calls.append(kwargs["n_gpu_layers"])

        fake_module = types.SimpleNamespace(Llama=FakeLlama)
        cfg = {
            "model_path": "models/test.gguf",
            "n_ctx": 2048,
            "n_threads": 4,
            "n_gpu_layers": -1,
            "gpu_mode": "cpu",
            "use_mlock": True,
        }
        decider = self._build_decider(fake_module, cfg)

        self.assertEqual(calls, [0])
        self.assertEqual(decider.backend, "llama_cpp")
        self.assertEqual(decider._llama_runtime["effective_gpu_layers"], 0)
        self.assertEqual(decider._llama_runtime["gpu_mode"], "cpu")

    def test_llama_backend_enables_optional_cloud_fallback_channel(self):
        class FakeLlama:
            def __init__(self, **kwargs):
                pass

        class FakeCloudClient:
            def __init__(self, api_base="", api_key="", model=""):
                self.api_base = api_base
                self.api_key = api_key
                self.model = model

            def is_available(self):
                return True

            def complete(self, prompt: str, max_tokens: int = 256) -> str:
                return ""

            def logging_status(self):
                return {"policy": "none", "raw_payload_retained": False, "call_count": 0}

        fake_module = types.SimpleNamespace(Llama=FakeLlama)
        cfg = {
            "model_path": "models/test.gguf",
            "n_ctx": 2048,
            "n_threads": 4,
            "n_gpu_layers": 0,
            "gpu_mode": "cpu",
            "use_mlock": True,
        }
        original_client = decision_module.CloudClient
        try:
            decision_module.CloudClient = FakeCloudClient
            decider = self._build_decider(fake_module, cfg)
        finally:
            decision_module.CloudClient = original_client

        self.assertEqual(decider.backend, "llama_cpp")
        self.assertTrue(decider.is_cloud_available())

    def test_llama_intent_falls_back_to_cloud_when_local_parse_fails(self):
        class FakeLlama:
            def __init__(self, **kwargs):
                pass

            def __call__(self, prompt, max_tokens=256, stop=None):
                return {"choices": [{"text": "not-json"}]}

        class FakeCloudClient:
            def __init__(self, api_base="", api_key="", model=""):
                pass

            def is_available(self):
                return True

            def complete(self, prompt: str, max_tokens: int = 256) -> str:
                return (
                    '{"intent_type":"action_command","route":"action","reply_message":"",'
                    '"normalized_goal":"打开空调","requires_candidates":true,'
                    '"requires_automation":false,"decision_confidence":0.93,"reasoning":"cloud fallback"}'
                )

            def logging_status(self):
                return {"policy": "none", "raw_payload_retained": False, "call_count": 0}

        fake_module = types.SimpleNamespace(Llama=FakeLlama)
        cfg = {
            "model_path": "models/test.gguf",
            "n_ctx": 2048,
            "n_threads": 4,
            "n_gpu_layers": 0,
            "gpu_mode": "cpu",
            "use_mlock": True,
        }
        original_client = decision_module.CloudClient
        try:
            decision_module.CloudClient = FakeCloudClient
            decider = self._build_decider(fake_module, cfg)
            result = decider.plan_intent("像昨天那样")
        finally:
            decision_module.CloudClient = original_client

        self.assertEqual(decider.backend, "llama_cpp")
        self.assertEqual(result["intent_type"], "action_command")
        self.assertEqual(result["normalized_goal"], "打开空调")


if __name__ == "__main__":
    unittest.main(verbosity=2)
