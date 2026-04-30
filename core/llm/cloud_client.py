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
        self._requests = None
        self._available = False
        self._init_client()

    def _init_client(self):
        if not self.api_key:
            logger.info("CloudClient disabled: missing API key")
            return
        if self.api_base and self.api_base.startswith("http://"):
            logger.warning("CloudClient: forcing HTTPS for external API call")
            self.api_base = self.api_base.replace("http://", "https://", 1)
        if self.api_base and not self.api_base.startswith("https://"):
            logger.warning("CloudClient disabled: api_base must use HTTPS")
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
        if not self._available:
            self._init_requests_client()

    def _init_requests_client(self):
        try:
            import requests

            self._requests = requests
            self._available = True
            logger.info("CloudClient initialized with requests fallback%s", f" for {self.api_base}" if self.api_base else "")
        except ImportError:
            logger.warning("requests package is not installed; CloudClient unavailable")

    def is_available(self) -> bool:
        return self._available and (self._client is not None or self._requests is not None)

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        if not self.is_available():
            raise RuntimeError("cloud client is not available")
        if self._requests is not None and self._client is None:
            base = (self.api_base or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            response = self._requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                },
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            return payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""
