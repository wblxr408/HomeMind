# Vosk ASR Models

Place the lightweight Vosk models here:

```text
models/asr/
  vosk-model-small-cn-0.22/
  vosk-model-small-en-us-0.15/
```

You can also override the paths with environment variables:

```text
HOMEMIND_VOSK_ZH_MODEL
HOMEMIND_VOSK_EN_MODEL
```

The Web voice endpoint accepts uploaded audio at `/api/voice/transcribe`.
For browser `audio/webm` uploads, install `ffmpeg` so the server can convert
audio to 16 kHz mono WAV before passing it to Vosk.
