# HomeMind：家庭场景智能家居 Agent

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Architecture](https://img.shields.io/badge/Architecture-Edge--Cloud-orange.svg)

**赛题方向：** 兴享智家（2026 中兴捧月）  
**当前形态：** 端侧优先、可选端云协同、可持续学习、可渐进自动化的家庭智能体

---

## 项目概述

HomeMind 面向家庭智能家居场景，目标不是做一个只能识别固定命令的控制器，而是做一个：

- 能理解模糊表达的 Agent
- 能保留上下文和用户偏好的 Agent
- 能在需要时调用云端、但始终由端侧掌握执行权的 Agent
- 能逐步引入自动化规则的 Agent

当前仓库已经具备这些核心能力：

- `BSR + LSR + LLM` 的自然语言主链路
- 本地 `RAG` 记忆与偏好闭环
- `DQN` 主动推荐骨架
- `Vosk` 本地语音识别 + 中英/口语/方言归一化
- `SessionStore / PreferenceStore` 结构化持久化
- `PrivacyRedactor` 最小必要上下文上传
- `InferenceRouter` 本地 / 云端 / 澄清 / fallback 路由
- `CommandValidator` 执行前校验
- `TAP` 最小规则引擎、规则存储、规则自动调度
- Web 控制台中的规则、记忆、隐私可视化面板

---

## 当前架构

当前 HomeMind 更接近下面这条链路：

```text
用户文本 / 语音输入
-> LanguageNormalizer
-> BSR 候选召回
-> LSR 轻量精排
-> InferenceRouter
   -> local
   -> cloud
   -> clarify
   -> fallback
-> PrivacyRedactor（仅 cloud path）
-> LLMDecider / CloudClient
-> CommandValidator
-> DeviceController / SceneSwitcher / InfoQuery
-> SessionStore / PreferenceStore / KnowledgeBase 写回
```

自动化链路独立存在：

```text
TAPRuleStore
-> TAPEngine
-> Scheduler
-> CommandValidator
-> 执行层
```

---

## 核心能力

| 能力 | 当前状态 |
|------|------|
| 模糊意图理解 | 已实现 |
| 端云协同路由 | 已实现 |
| 执行前白名单/范围校验 | 已实现 |
| 上下文持久化 | 已实现 |
| 长期结构化偏好 | 已实现 |
| 隐私脱敏与最小必要上传 | 已实现 |
| 本地语音识别 | 已实现（Vosk） |
| 中英/口语/方言归一化 | 已实现基础版 |
| TAP 规则管理 | 已实现基础版 |
| TAP 自动调度 | 已实现基础版 |
| Web 可视化面板 | 已实现基础版 |

---

## 目录结构

```text
HomeMind/
├── main.py
├── run_web.py
├── web/
│   ├── server.py
│   ├── protocol_config.json
│   └── client/
│       └── index.html
├── core/
│   ├── constants.py
│   ├── security.py
│   ├── automation/
│   │   ├── tap_engine.py
│   │   └── tap_rules.py
│   ├── bsr/
│   │   └── candidate_recall.py
│   ├── dqn/
│   │   └── policy.py
│   ├── execution/
│   │   └── command_validator.py
│   ├── language/
│   │   └── normalizer.py
│   ├── llm/
│   │   ├── cloud_client.py
│   │   └── decision.py
│   ├── lsr/
│   │   └── precision_ranking.py
│   ├── memory/
│   │   ├── preference_store.py
│   │   └── session_store.py
│   ├── privacy/
│   │   └── redactor.py
│   ├── rag/
│   │   └── knowledge_base.py
│   ├── router/
│   │   └── inference_router.py
│   ├── utils/
│   │   └── embedding.py
│   └── voice/
│       ├── feedback_store.py
│       └── vosk_asr.py
├── demo/
├── tools/
├── models/
│   └── asr/
├── tests/
└── data/
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
pip install -r requirements-web.txt
```

如果你使用 conda：

```bash
conda activate homemind
pip install -r requirements-web.txt
```

### 2. 准备 Vosk 模型

将模型放到：

```text
models/asr/vosk-model-small-cn-0.22/
models/asr/vosk-model-small-en-us-0.15/
```

说明见：

- [models/asr/README.md](./models/asr/README.md)
- [VOSK_ASR_PLAN.md](./VOSK_ASR_PLAN.md)

### 3. 运行 CLI

```bash
python main.py
```

### 4. 运行 Web

```bash
python run_web.py --mode simulated --port 5000
```

访问：

```text
http://localhost:5000
```

---

## Web 控制台当前支持

- 文本指令输入
- 本地语音上传识别
- 设备控制
- 场景切换
- BSR / LSR / LLM / 执行 流水线可视化
- 语音反馈提交
- 自动化规则创建、启停、删除、手动评估
- 规则自动调度开关
- 记忆与偏好面板
- 隐私与云调用面板

---

## 关键模块说明

### 1. Inference Router

文件：

- `core/router/inference_router.py`

职责：

- 明确命令本地直达
- 中等置信度请求决定是否上云
- 低置信度请求直接澄清
- 云端不可用时 fallback

### 2. Command Validator

文件：

- `core/execution/command_validator.py`

职责：

- 字段校验
- 设备白名单校验
- 动作白名单校验
- 参数范围校验
- 风险等级判断

### 3. Structured Memory

文件：

- `core/memory/session_store.py`
- `core/memory/preference_store.py`
- `core/rag/knowledge_base.py`

分工：

- `SessionStore`：短期会话状态
- `PreferenceStore`：长期结构化偏好
- `KnowledgeBase`：可检索文本记忆

### 4. Privacy

文件：

- `core/privacy/redactor.py`

职责：

- 构造最小必要云端上下文
- 避免上传完整历史、原始日志和敏感细节

### 5. TAP Automation

文件：

- `core/automation/tap_engine.py`
- `core/automation/tap_rules.py`

职责：

- 规则存储
- 规则评估
- 简单冲突消解
- 后台自动调度

---

## 当前主要接口

### 基础接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 系统状态 |
| `/api/query` | POST | 自然语言查询 |
| `/api/devices/<device>/control` | POST | 设备控制 |
| `/api/scenes/<scene>/switch` | POST | 场景切换 |
| `/api/info/<info_type>` | GET | 信息查询 |

### 记忆 / 隐私

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/preferences` | GET | 结构化偏好快照 |
| `/api/memory/summary` | GET | 记忆摘要 |
| `/api/privacy/status` | GET | 隐私与云调用状态 |

### 语音

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voice/transcribe` | POST | 本地语音识别 |
| `/api/voice/feedback` | POST | 语音反馈写回 |

### DQN

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/dqn/recommend` | GET | 主动推荐 |
| `/api/dqn/feedback` | POST | 推荐反馈 |

### TAP 规则

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/rules` | GET | 列出规则 |
| `/api/rules` | POST | 创建规则 |
| `/api/rules/<id>` | PUT | 更新规则 |
| `/api/rules/<id>` | DELETE | 删除规则 |
| `/api/rules/<id>/toggle` | POST | 启停规则 |
| `/api/rules/evaluate` | POST | 评估规则 |
| `/api/rules/scheduler` | GET | 调度器状态 |
| `/api/rules/scheduler` | POST | 启停调度器 |

---

## 语音能力现状

当前仓库已经不是“浏览器 Web Speech API 唯一路径”，而是：

- 浏览器负责录音
- 服务端 `/api/voice/transcribe` 接收音频
- `VoskASR` 本地完成转写
- `LanguageNormalizer` 做中英/口语/方言归一化
- 用户可通过 `/api/voice/feedback` 提交反馈并写回历史

---

## 隐私边界说明

HomeMind 当前采用的是：

- **端侧优先**
- **必要时可选云端协同**
- **端侧保留最终执行权**

云端路径下，上传的不是完整家庭原始数据，而是：

- 时间
- 温湿度
- 在家人数
- 当前场景
- Top-K 候选动作
- 少量偏好摘要

执行前还会经过端侧命令校验。

---

## 当前测试

当前仓库已覆盖的测试包括：

- 路由与命令校验
- 结构化上下文与偏好持久化
- TAP 引擎与规则接口
- CLI / Web mock 主流程
- 语音反馈历史

运行：

```bash
python -m unittest tests.test_tap_engine tests.test_routing_validation tests.test_context_privacy tests.test_mock_flows tests.test_voice_feedback -v
```

---

## 后续可继续扩展

目前还适合继续做的方向：

- 规则编辑 UI 进一步完善
- 更丰富的 TAP 条件类型
- 更细粒度的隐私可视化
- 更精细的记忆摘要与偏好展示
- 更强的云端模型接入
- README / 答辩材料继续收口

---

## 相关文档

- [UPGRADE_PLAN.md](./UPGRADE_PLAN.md)
- [CONTEXT_PRIVACY_PLAN.md](./CONTEXT_PRIVACY_PLAN.md)
- [VOSK_ASR_PLAN.md](./VOSK_ASR_PLAN.md)
