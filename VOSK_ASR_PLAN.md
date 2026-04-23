# HomeMind Vosk 本地语音识别方案

更新日期：2026-04-23

## 1. 目标

本方案用于替换原先依赖浏览器 `SpeechRecognition / webkitSpeechRecognition` 的语音输入路径，改为在 HomeMind 服务端进行本地语音识别。

核心目标：

- 降低端侧部署内存与算力压力。
- 支持中文与英文语音输入。
- 支持中文口语和部分方言表达的归一化。
- 避免将语音识别能力绑定到浏览器厂商 API。
- 保持现有 BSR / LSR / LLM / 执行链路不被推翻。

## 2. 总体架构

```text
网页麦克风录音
-> MediaRecorder 生成音频
-> POST /api/voice/transcribe
-> Vosk 本地 ASR
-> LanguageNormalizer 中英/方言归一化
-> BSR 候选召回
-> LSR 精排
-> LLM/mock 决策
-> DeviceController / SceneSwitcher 执行
```

这套设计把语音能力拆成两层：

- **Vosk ASR**：负责把声音转成文本。
- **LanguageNormalizer**：负责把英文、口语、方言表达转成 HomeMind 标准指令。

例如：

```text
turn on the AC       -> 打开空调
make it cooler       -> 太热了
帮我开哈空调          -> 打开空调
灯搞亮点              -> 调亮灯光
屋里闷得很            -> 有点闷
```

## 3. 为什么选择 Vosk

Vosk 相比 Whisper / faster-whisper 更适合低内存端侧部署：

| 方案 | 优点 | 代价 |
| --- | --- | --- |
| 浏览器 Web Speech API | 不占本地 ASR 内存，实现简单 | 不可控，浏览器差异大，离线和隐私边界不稳定 |
| faster-whisper | 中英文效果较好，Python 接入方便 | 模型和运行内存较高，不适合很低配端侧 |
| whisper.cpp | 可量化，端侧友好 | C/C++ 接入和音频链路更复杂 |
| Vosk small | 模型小、离线、本地、适合短指令 | 准确率不如大模型，需要文本归一化补强 |

HomeMind 的语音输入主要是智能家居短指令，Vosk small 模型配合规则归一化，可以在内存占用和可用性之间取得比较好的平衡。

## 4. 文件结构

本次方案涉及的新增或修改文件：

```text
core/
  voice/
    __init__.py
    vosk_asr.py
  language/
    __init__.py
    normalizer.py

models/
  asr/
    README.md

web/
  server.py
  client/
    index.html

main.py
core/bsr/candidate_recall.py
requirements-web.txt
.env.example
```

## 5. 模型放置

推荐使用 Vosk small 中文和英文模型：

```text
models/asr/
  vosk-model-small-cn-0.22/
  vosk-model-small-en-us-0.15/
```

也可以通过环境变量覆盖模型路径：

```text
HOMEMIND_VOSK_ZH_MODEL=models/asr/vosk-model-small-cn-0.22
HOMEMIND_VOSK_EN_MODEL=models/asr/vosk-model-small-en-us-0.15
```

模型目录已在 [models/asr/README.md](models/asr/README.md) 中说明。

## 6. 后端接口

语音识别接口：

```http
POST /api/voice/transcribe
Content-Type: multipart/form-data
```

请求字段：

| 字段 | 说明 |
| --- | --- |
| `audio` | 前端录制的音频文件 |
| `lang` | `zh` / `en` / `auto`，默认 `auto` |

成功返回：

```json
{
  "status": "success",
  "text": "turn on the ac",
  "language": "en",
  "confidence": 0.86,
  "engine": "vosk",
  "normalized": "打开空调",
  "normalization": {
    "original": "turn on the ac",
    "normalized": "打开空调",
    "language": "en",
    "confidence": 0.95,
    "matched_rule": "en_open_ac",
    "extra_candidates": []
  }
}
```

缺少依赖或模型时，接口不会导致 Web 服务崩溃，而是返回明确错误：

```json
{
  "status": "unavailable",
  "error": "vosk_not_installed",
  "hint": "Install with: pip install vosk"
}
```

或：

```json
{
  "status": "unavailable",
  "error": "model_not_found",
  "hint": "Place Vosk models under models/asr/ or set HOMEMIND_VOSK_ZH_MODEL / HOMEMIND_VOSK_EN_MODEL."
}
```

## 7. 前端录音链路

前端不再调用浏览器语音识别 API。

新的流程：

```text
点击麦克风按钮
-> navigator.mediaDevices.getUserMedia({ audio: true })
-> MediaRecorder 开始录音
-> 再次点击麦克风结束录音
-> 上传 audio/webm 到 /api/voice/transcribe
-> 使用返回的 normalized 文本提交给 HomeMind
```

注意：

- 浏览器只负责录音，不负责识别。
- 识别由服务端 Vosk 完成。
- 如果上传的是 `audio/webm`，服务端需要 `ffmpeg` 转成 16 kHz mono WAV。

## 8. 依赖安装

在 conda 环境中安装：

```powershell
conda activate used_pytorch
pip install flask flask-cors flask-socketio python-socketio eventlet vosk
conda install -c conda-forge ffmpeg
```

也可以使用：

```powershell
pip install -r requirements-web.txt
conda install -c conda-forge ffmpeg
```

`ffmpeg` 的作用是把浏览器上传的 `webm/opus` 音频转为 Vosk 更稳定处理的 `16kHz mono wav`。

## 9. 启动方式

```powershell
cd C:\Users\25977\Desktop\HomeMind
conda activate used_pytorch
python run_web.py --mode simulated --port 5000
```

打开：

```text
http://localhost:5000
```

如果 5000 端口被占用：

```powershell
python run_web.py --mode simulated --port 5001
```

## 10. 归一化规则

当前 `LanguageNormalizer` 覆盖了两类表达。

英文：

```text
turn on the AC              -> 打开空调
turn off the air conditioner -> 关闭空调
make it cooler              -> 太热了
make it warmer              -> 太冷了
turn on the lights          -> 打开灯光
make the lights brighter    -> 调亮灯光
dim the lights              -> 调暗灯光
movie mode                  -> 切换观影模式
sleep mode                  -> 切换睡眠模式
I'm leaving                 -> 切换离家模式
```

中文口语/方言：

```text
开哈空调 / 空调开起        -> 打开空调
热煞了 / 热得很 / 遭不住   -> 太热了
屋里闷得很 / 闷得慌        -> 有点闷
灯搞亮点 / 灯整亮点        -> 调亮灯光
灯暗点 / 灯柔和点          -> 调暗灯光
我要歇了 / 我要睡了        -> 切换睡眠模式
```

## 11. 与现有链路的关系

归一化结果会在 BSR 前生效。

CLI 中：

```text
用户输入 -> LanguageNormalizer -> BSR -> LSR -> LLM -> 执行
```

Web 中：

```text
语音上传 -> VoskASR -> LanguageNormalizer -> WebSocket user_input -> BSR -> LSR -> LLM -> 执行
```

这样可以避免重写 BSR / LSR / LLM，只在输入层把多语言和方言表达统一成系统已经理解的标准中文指令。

## 12. 已验证内容

已做的轻量验证：

```text
turn on the AC       -> 打开空调 -> 已开启空调，温度26°C
灯搞亮点              -> 调亮灯光 -> 已为您亮度调整为100%
屋里闷得很            -> 有点闷，并生成空调/风扇/窗户候选
```

已通过：

```text
python -m py_compile core/voice/vosk_asr.py core/language/normalizer.py core/bsr/candidate_recall.py web/server.py main.py tests/test_mock_flows.py
python -m unittest tests.test_mock_flows.HomeMindCliMockFlowTests -v
```

当前未完成的验证：

- 当前 conda 环境尚未安装 `flask_cors`，因此 Web 端到端测试需要先补齐 Web 依赖。
- 当前仓库未包含 Vosk 模型文件，需要手动放入 `models/asr/`。
- 真实麦克风录音上传需要浏览器授权麦克风，并确保服务端可调用 `ffmpeg`。

## 13. 语音反馈历史

语音识别结果支持用户反馈，并会写入本地历史。

前端在每次语音识别后展示：

```text
语音识别：<ASR 原始文本>
归一化：<HomeMind 标准指令>
[正确] [纠正] [忽略]
```

反馈接口：

```http
POST /api/voice/feedback
Content-Type: application/json
```

请求示例：

```json
{
  "asr_text": "turn on the ac",
  "normalized": "打开空调",
  "corrected_text": "",
  "corrected_normalized": "",
  "language": "en",
  "confidence": 0.86,
  "engine": "vosk",
  "feedback": "accepted"
}
```

纠正示例：

```json
{
  "asr_text": "灯高亮点",
  "normalized": "打开灯光",
  "corrected_text": "灯搞亮点",
  "feedback": "corrected"
}
```

系统会将反馈写入：

```text
data/voice_feedback.jsonl
```

同时，如果 Web Agent 的知识库已初始化，也会把反馈写入知识库的“语音反馈”类别。

后续同一条 ASR 文本再次出现时，`LanguageNormalizer` 会优先读取用户纠正历史，再使用内置规则。

## 14. 答辩表述

可以这样描述：

> HomeMind 的语音输入从依赖浏览器 Web Speech API 升级为端侧本地 ASR。系统使用 Vosk small 中文与英文模型在本地完成语音转写，避免语音数据依赖浏览器或云端服务；随后通过语言归一化模块，将英文指令、中文口语和方言表达统一映射为 HomeMind 标准中文指令，再进入原有 BSR / LSR / LLM 决策链路。该方案在保持低内存端侧部署的同时，增强了多语言和家庭场景口语表达的适应能力。

## 15. 后续优化

后续可以继续增强：

- 增加前端语言选择：中文 / English / Auto。
- 增加更多方言词表和用户纠错学习。
- 在低置信度时返回候选，而不是直接执行。
- 保存 `original_text` 与 `normalized_text`，用于解释和反馈。
- 在高配设备上增加 `whisper.cpp` 或云端 ASR 作为兜底。
