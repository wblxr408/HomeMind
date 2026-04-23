"""
Lightweight local ASR based on Vosk.

The recognizer is intentionally optional: importing this module does not require
Vosk or model files to exist. Runtime calls return clear configuration errors
instead of breaking the Web server.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

logger = logging.getLogger(__name__)


DEFAULT_ZH_MODEL = Path("models/asr/vosk-model-small-cn-0.22")
DEFAULT_EN_MODEL = Path("models/asr/vosk-model-small-en-us-0.15")


@dataclass
class ASRResult:
    status: str
    text: str = ""
    language: str = "unknown"
    confidence: float = 0.0
    engine: str = "vosk"
    error: str = ""
    hint: str = ""

    def to_dict(self) -> Dict[str, object]:
        return {
            "status": self.status,
            "text": self.text,
            "language": self.language,
            "confidence": self.confidence,
            "engine": self.engine,
            "error": self.error,
            "hint": self.hint,
        }


class VoskASR:
    """Small-footprint local ASR wrapper with zh/en model selection."""

    def __init__(
        self,
        zh_model_path: Optional[str] = None,
        en_model_path: Optional[str] = None,
        sample_rate: int = 16000,
    ):
        self.sample_rate = sample_rate
        self.model_paths = {
            "zh": Path(zh_model_path or os.getenv("HOMEMIND_VOSK_ZH_MODEL", DEFAULT_ZH_MODEL)),
            "en": Path(en_model_path or os.getenv("HOMEMIND_VOSK_EN_MODEL", DEFAULT_EN_MODEL)),
        }
        self._models = {}
        self._vosk = None

    def available_languages(self) -> Iterable[str]:
        for lang, path in self.model_paths.items():
            if path.exists():
                yield lang

    def is_available(self, lang: str = "auto") -> bool:
        if not self._load_vosk():
            return False
        if lang == "auto":
            return any(self.available_languages())
        return self.model_paths.get(lang, Path()).exists()

    def transcribe_bytes(self, audio: bytes, filename: str = "voice.webm", lang: str = "auto") -> ASRResult:
        if not audio:
            return ASRResult(status="error", error="empty_audio", hint="No audio file was uploaded.")
        if not self._load_vosk():
            return ASRResult(
                status="unavailable",
                error="vosk_not_installed",
                hint="Install with: pip install vosk",
            )

        languages = self._candidate_languages(lang)
        if not languages:
            return ASRResult(
                status="unavailable",
                error="model_not_found",
                hint=(
                    "Place Vosk models under models/asr/ or set "
                    "HOMEMIND_VOSK_ZH_MODEL / HOMEMIND_VOSK_EN_MODEL."
                ),
            )

        with tempfile.TemporaryDirectory(prefix="homemind_voice_") as tmp:
            source = Path(tmp) / self._safe_filename(filename)
            source.write_bytes(audio)
            wav_path = Path(tmp) / "voice.wav"
            converted = self._ensure_wav(source, wav_path)
            if not converted:
                return ASRResult(
                    status="error",
                    error="audio_convert_failed",
                    hint="Upload 16kHz mono WAV, or install ffmpeg so WebM/Opus can be converted.",
                )

            best = ASRResult(status="error", error="no_speech", hint="No speech was recognized.")
            for candidate_lang in languages:
                result = self._recognize_wav(wav_path, candidate_lang)
                if result.text and result.confidence >= best.confidence:
                    best = result
            return best

    def _candidate_languages(self, lang: str) -> Tuple[str, ...]:
        if lang in ("zh", "en"):
            return (lang,) if self.model_paths[lang].exists() else tuple()
        # In auto mode, prefer Chinese for this project, then English.
        return tuple(language for language in ("zh", "en") if self.model_paths[language].exists())

    def _load_vosk(self) -> bool:
        if self._vosk is not None:
            return True
        try:
            import vosk
            vosk.SetLogLevel(-1)
            self._vosk = vosk
            return True
        except ImportError:
            return False

    def _load_model(self, lang: str):
        if lang in self._models:
            return self._models[lang]
        path = self.model_paths[lang]
        if not path.exists():
            raise FileNotFoundError(path)
        model = self._vosk.Model(str(path))
        self._models[lang] = model
        return model

    def _recognize_wav(self, path: Path, lang: str) -> ASRResult:
        try:
            model = self._load_model(lang)
            with wave.open(str(path), "rb") as wf:
                if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                    return ASRResult(
                        status="error",
                        language=lang,
                        error="invalid_wav_format",
                        hint="Audio must be 16-bit mono WAV.",
                    )
                recognizer = self._vosk.KaldiRecognizer(model, wf.getframerate())
                recognizer.SetWords(True)
                while True:
                    data = wf.readframes(4000)
                    if not data:
                        break
                    recognizer.AcceptWaveform(data)
                payload = json.loads(recognizer.FinalResult() or "{}")
        except Exception as exc:
            logger.warning("Vosk recognition failed: %s", exc)
            return ASRResult(status="error", language=lang, error="recognition_failed", hint=str(exc))

        text = (payload.get("text") or "").strip()
        confidence = self._average_confidence(payload)
        return ASRResult(
            status="success" if text else "error",
            text=text,
            language=lang,
            confidence=confidence,
            error="" if text else "no_speech",
        )

    def _average_confidence(self, payload: Dict[str, object]) -> float:
        words = payload.get("result") or []
        if not words:
            return 0.0
        confidences = [float(item.get("conf", 0.0)) for item in words if isinstance(item, dict)]
        if not confidences:
            return 0.0
        return round(sum(confidences) / len(confidences), 3)

    def _ensure_wav(self, source: Path, target: Path) -> bool:
        if source.suffix.lower() == ".wav" and self._is_readable_wav(source):
            shutil.copyfile(source, target)
            return True

        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return False
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-ac",
            "1",
            "-ar",
            str(self.sample_rate),
            "-sample_fmt",
            "s16",
            str(target),
        ]
        completed = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return completed.returncode == 0 and self._is_readable_wav(target)

    def _is_readable_wav(self, path: Path) -> bool:
        try:
            with wave.open(str(path), "rb") as wf:
                return wf.getnchannels() == 1 and wf.getsampwidth() == 2
        except wave.Error:
            return False

    def _safe_filename(self, filename: str) -> str:
        name = Path(filename or "voice.webm").name
        return name if name else "voice.webm"

