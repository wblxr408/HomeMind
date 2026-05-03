"""OpenAI-compatible cloud LLM client for HomeMind."""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from core.config import OPENAI_CONFIG, STORAGE_CONFIG

logger = logging.getLogger(__name__)


class CloudClient:
    """Thin wrapper around an OpenAI-compatible chat completion API."""

    def __init__(self, api_base: str = "", api_key: str = "", model: str = ""):
        self.api_base = api_base or os.getenv("LLM_API_BASE", "")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.model = model or os.getenv("LLM_MODEL", "gpt-3.5-turbo")
        self.log_policy = str(OPENAI_CONFIG.get("log_policy") or "none").strip().lower()
        if self.log_policy not in {"none", "metadata"}:
            logger.warning("CloudClient: unsupported CLOUD_LOG_POLICY=%s, using none", self.log_policy)
            self.log_policy = "none"
        self.log_retention_days = int(OPENAI_CONFIG.get("log_retention_days") or 0)
        self.log_path = Path(
            OPENAI_CONFIG.get("log_path")
            or os.path.join(STORAGE_CONFIG["logs_dir"], "cloud_calls.jsonl")
        )
        self._call_count = 0
        self._last_call_metadata: Dict[str, Any] = {}
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

    def logging_status(self) -> dict:
        return {
            "policy": self.log_policy,
            "retention_days": self.log_retention_days,
            "path": str(self.log_path) if self.log_policy == "metadata" else "",
            "raw_payload_retained": False,
            "call_count": self._call_count,
            "last_call": dict(self._last_call_metadata),
        }

    def _record_call_metadata(self, *, provider: str, status: str, max_tokens: int, error: str = "") -> None:
        self._call_count += 1
        metadata = {
            "timestamp": datetime.now().astimezone().isoformat(),
            "provider": provider,
            "model": self.model,
            "status": status,
            "max_tokens": int(max_tokens or 0),
            "error_code": str(error or "")[:80],
        }
        self._last_call_metadata = metadata

        if self.log_policy != "metadata":
            return

        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(metadata, ensure_ascii=False) + "\n")
            self._prune_cloud_logs()
        except Exception as exc:
            logger.warning("CloudClient: metadata log write failed: %s", exc)

    def _prune_cloud_logs(self) -> None:
        if self.log_policy != "metadata" or self.log_retention_days <= 0 or not self.log_path.exists():
            return
        cutoff = datetime.now().astimezone() - timedelta(days=self.log_retention_days)
        try:
            kept = []
            with open(self.log_path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        item = json.loads(line)
                        timestamp = datetime.fromisoformat(str(item.get("timestamp", "")))
                    except Exception:
                        continue
                    if timestamp >= cutoff:
                        kept.append(line)
            with open(self.log_path, "w", encoding="utf-8") as handle:
                handle.writelines(kept)
        except Exception as exc:
            logger.warning("CloudClient: metadata log prune failed: %s", exc)

    def complete(self, prompt: str, max_tokens: int = 256) -> str:
        if not self.is_available():
            raise RuntimeError("cloud client is not available")
        if self._requests is not None and self._client is None:
            base = (self.api_base or "https://api.openai.com/v1").rstrip("/")
            url = f"{base}/chat/completions"
            try:
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
                self._record_call_metadata(provider="requests", status="success", max_tokens=max_tokens)
                return payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            except Exception as exc:
                self._record_call_metadata(provider="requests", status="error", max_tokens=max_tokens, error=type(exc).__name__)
                raise
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            self._record_call_metadata(provider="openai", status="success", max_tokens=max_tokens)
            return response.choices[0].message.content or ""
        except Exception as exc:
            self._record_call_metadata(provider="openai", status="error", max_tokens=max_tokens, error=type(exc).__name__)
            raise
