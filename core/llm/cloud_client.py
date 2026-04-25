"""OpenAI-compatible cloud LLM client for HomeMind."""

import logging
import os

logger = logging.getLogger(__name__)


class CloudClient:
    """Thin wrapper around an OpenAI-compatible chat completion API."""

    def __init__(self, api_base: str = "", api_key: str = "", model: str = ""):
        self.api_base = api_base or os.getenv("LLM_API_BASE", "")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self._client = None
        self._available = False
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.info("CloudClient disabled: missing API key")
            return
        try:
            import openai

            kwargs = {"api_key": self.api_key}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = openai.OpenAI(**kwargs)
            self._available = True
            logger.info("CloudClient initialized%s", f" with base {self.api_base}" if self.api_base else "")
        except ImportError:
            logger.warning("openai package is not installed; CloudClient unavailable")
        except Exception as exc:
            logger.warning("CloudClient init failed: %s", exc)

    def is_available(self) -> bool:
        return self._available and self._client is not None

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        if not self.is_available():
            raise RuntimeError("cloud client is not available")
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
