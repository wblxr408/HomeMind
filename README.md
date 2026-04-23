# HomeMind：家庭端侧模糊意图理解智能体

![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)
![Architecture](https://img.shields.io/badge/Architecture-5--Layer-orange.svg)

**赛题方向：** 兴享智家（2026中兴捧月）
**技术架构：** 端侧 AI Agent，RAG + DQN 强化学习
**部署方式：** 全本地运行，数据永不离开设备

---

## 项目概述

HomeMind 是一个运行在家庭端侧设备上的智能家居大脑，能理解"有点闷"、"像昨天晚上那样"等模糊自然语言指令，通过 RAG 保证回答可信，通过 DQN 强化学习主动感知用户习惯并推荐场景。越用越懂你，且数据永不离开本地设备。

### 核心能力

| 能力 | 说明 |
|------|------|
| **模糊意图理解** | 理解"有点闷"、"像昨天晚上那样"等自然语言，而非精确指令 |
| **RAG 知识库** | 本地 ChromaDB 持久化，持续学习用户偏好，形成 RAG 闭环 |
| **DQN 主动推荐** | 强化学习感知用户习惯，ε-greedy 策略独立于用户指令流程 |
| **全本地运行** | 数据永不离开设备，无隐私风险 |

---

## 快速开始

### 安装依赖

```bash
# 基础依赖
pip install -r requirements.txt

# Web 服务依赖（可选，需要 Web 界面时安装）
pip install -r requirements-web.txt
```

### 运行方式

**方式一：命令行交互**

```bash
python main.py
```

输入示例：
```
有点闷
我要出门了
像昨天晚上那样
```

**方式二：Web 控制面板**

```bash
python web/server.py
# 访问 http://localhost:5000
```

---

## 技术架构

### 五层架构

```
┌──────────────────────────────────────────────────────────┐
│                         交互层                             │
│       语音输入（浏览器 Web Speech API）  文字输入   环境上下文注入     │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│                       BSR 层（广召回）                      │
│                      候选召回（Top-K 3~5）                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   规则召回   │  │  向量召回    │  │  用户历史    │   │
│  │  关键词匹配  │  │  MiniLM     │  │    RAG      │   │
│  └──────────────┘  └──────────────┘  └──────────────┘   │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│                       LSR 层（轻量精排）                     │
│                      轻量打分（Top-1/3）                   │
│  ┌──────────────────────────────────────────────────┐   │
│  │  特征：语义相似度 + 温度 + 湿度 + 时间周期 + RAG偏好 │   │
│  │  权重向量 [0.30, 0.10, 0.05, 0.20, 0.35]         │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────┬────────────────────────────────┘
                          │
┌─────────────────────────▼────────────────────────────────┐
│                         理解层                            │
│   ┌──────────────────────────┐  ┌─────────────────────┐  │
│   │  LLM 决策与参数生成       │  │   DQN 场景推荐      │  │
│   │  Mock/LlamaCpp/OpenAI    │  │   5→32→6 网络      │  │
│   │  置信度评估 + 主动澄清     │  │   ε-greedy 探索    │  │
│   └──────────────┬───────────┘  └──────────┬──────────┘  │
│                  │     ←→  RAG 检索增强       │            │
└──────────────────┼────────────────────────────┼────────────┘
                   │                            │
┌──────────────────▼────────────────────────────▼────────────┐
│                         执行层                             │
│      任务规划 → 工具调用（设备控制/信息查询/场景切换）         │
│                      ↓ 用户反馈收集                        │
└──────────────────────────┬─────────────────────────────────┘
                           │
┌──────────────────────────▼─────────────────────────────────┐
│                         学习层                             │
│   知识库增量更新（ChromaDB写回）  DQN经验回放池 → 策略更新  │
│              ↑──────────────────────────────────────↑      │
│                        RAG 闭环 + DQN 闭环                  │
└───────────────────────────────────────────────────────────┘
```

### 核心模块

| 模块 | 文件 | 职责 |
|------|------|------|
| **BSR** | `core/bsr/candidate_recall.py` | 三路融合召回：规则 + 向量 + 历史 |
| **LSR** | `core/lsr/precision_ranking.py` | 5维特征加权打分排序 |
| **LLM** | `core/llm/decision.py` | 意图识别、置信度评估、参数生成 |
| **DQN** | `core/dqn/policy.py` | 强化学习策略网络，主动场景推荐 |
| **RAG** | `core/rag/knowledge_base.py` | ChromaDB 知识库，RAG 检索增强 |
| **Embedding** | `core/utils/embedding.py` | MiniLM 统一向量服务（单例） |

### 双轨容错机制

| 轨道 | 路径 | 说明 |
|------|------|------|
| **主轨** | BSR → LSR → LLM 决策 | 正常流程 |
| **备轨** | 规则树兜底 | 置信度 < 阈值时自动降级 |

### 两层核心闭环

**RAG 闭环：** 用户纠正 → ChromaDB 写入 → 下次优先检索 → 模型不再犯同样错误

**DQN 闭环：** 环境状态 → 策略推理 → 推荐 → 用户响应（奖励信号）→ 经验回放池 → 增量更新

---

## 目录结构

```
HomeMind/
├── main.py                      # 命令行入口（HomeMindAgent）
├── web/
│   ├── server.py                # Web 服务（Flask + SocketIO）
│   └── client/
│       └── index.html           # 控制面板前端
├── core/                        # 核心架构层
│   ├── constants.py             # 共享常量（场景索引/动作池）
│   ├── config.py                # 配置管理
│   ├── security.py              # 加密存储
│   ├── utils/
│   │   └── embedding.py         # MiniLM 统一向量服务
│   ├── bsr/
│   │   └── candidate_recall.py # BSR：三路融合召回
│   ├── lsr/
│   │   └── precision_ranking.py# LSR：轻量精排（5维特征）
│   ├── llm/
│   │   └── decision.py          # LLM：决策（Mock/LlamaCpp/OpenAI）
│   ├── dqn/
│   │   └── policy.py            # DQN：策略网络（5→32→6）
│   └── rag/
│       └── knowledge_base.py    # RAG：ChromaDB 知识库
├── tools/                       # 工具函数层
│   ├── device_control.py        # 设备控制（空调/灯光/电视/风扇/音响/窗户/热水器）
│   ├── info_query.py            # 信息查询（温湿度/历史/天气/日程/偏好）
│   ├── scene_switch.py          # 场景切换（睡眠/待客/离家/观影/起床/回家）
│   ├── kb_write.py              # 知识库写入
│   └── dqn_feedback.py         # DQN 反馈记录
├── demo/                        # 演示与仿真
│   ├── simulator.py             # 交互演示 + HomeSimulator + AutoSimulation
│   ├── device_simulator.py      # 设备状态模拟
│   └── context.py               # HomeContext 数据类
├── data/                        # 数据目录
│   └── chroma_db/               # ChromaDB 持久化存储
└── models/                      # 模型目录
    └── dqn_policy.pkl           # DQN 策略模型
```

---

## Web API 接口

### REST API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/status` | GET | 获取系统状态（上下文 + 设备状态） |
| `/api/query` | POST | 自然语言查询接口 |
| `/api/devices/<device>/control` | POST | 设备控制 |
| `/api/scenes/<scene>/switch` | POST | 场景切换 |
| `/api/info/<info_type>` | GET | 信息查询 |
| `/api/dqn/recommend` | GET | DQN 主动推荐 |
| `/api/dqn/feedback` | POST | DQN 反馈提交 |
| `/api/kb/query` | POST | 知识库查询 |
| `/api/kb/add` | POST | 添加知识 |

### WebSocket 事件

| 事件 | 方向 | 说明 |
|------|------|------|
| `user_input` | 客户端 → 服务端 | 发送自然语言指令 |
| `device_control` | 客户端 → 服务端 | 设备控制 |
| `scene_switch` | 客户端 → 服务端 | 场景切换 |
| `pipeline_update` | 服务端 → 客户端 | 推理流水线状态更新 |
| `message` | 服务端 → 客户端 | Agent 响应消息 |

---

## Web 控制面板

访问 `http://localhost:5000/web/client/index.html` 打开控制面板：

- **传感器数据**：实时显示温度、湿度、在家人数
- **设备控制**：灯光、空调、电视等设备开关控制
- **场景模式**：睡眠/观影/工作/离家/早安/晚归一键切换
- **语音输入**：支持浏览器 Web Speech API 语音指令
- **推理可视化**：实时展示 BSR → LSR → LLM → 执行 流水线状态

---

## 技术选型

| 模块 | 技术选型 | 规格 |
|------|----------|------|
| 核心推理 | llama.cpp（C++）+ Qwen2.5-0.5B | INT4，~500MB |
| 语音识别 | faster-whisper · Whisper-tiny | FP16，~150MB |
| 文本向量化 | all-MiniLM-L6-v2 | FP32，~30MB |
| 向量数据库 | ChromaDB（本地） | ~50MB |
| 强化学习 | 自研轻量 DQN | <5MB |
| **合计** | | **~1.24GB（4GB设备可运行）** |

---

## 典型交互流程

### 流程一：模糊意图 → BSR → LSR → LLM → 执行 → RAG 写回

```
用户：  "有点闷"
────────────────────────────────────────────────────────
BSR     规则召回: 闷→空调/风扇/开窗（score=0.9）
        向量召回: 打开空调(0.82)、打开窗户(0.61)
        RAG历史: 28°C以上偏好开空调（score=0.85）
        → 候选集: [打开空调, 打开风扇, 打开窗户]
────────────────────────────────────────────────────────
LSR     特征打分: 打开空调(0.92) > 打开风扇(0.65) > 打开窗户(0.58)
        → 精排结果: 打开空调
────────────────────────────────────────────────────────
LLM     RAG上下文注入 → confidence=0.91
        → decision: {action:"设备控制", device:"空调", device_action:"on", params:{temp:26}}
────────────────────────────────────────────────────────
执行    device_control(空调, on, temp=26)
        设备状态同步到 simulator
────────────────────────────────────────────────────────
RAG写回 知识库写入: 用户接受28°C开空调的决策
```

### 流程二：DQN 主动推荐（独立流程）

```
环境感知: 时间22:15，在家2人，上次场景：观影模式
────────────────────────────────────────────────────────
DQN推理  状态向量 → Q值输出 → ε-greedy 选择
        Q值: 睡眠模式=0.89 > 待客=0.12 > 离家=0.05
────────────────────────────────────────────────────────
推荐     置信度0.89 > 0.8 → 直接执行
        scene_switch(睡眠模式)
────────────────────────────────────────────────────────
反馈     用户响应"好的" → reward=+1.0
        经验写入回放池（累计第47条）
────────────────────────────────────────────────────────
更新     每50条触发轻量梯度更新
        ε 衰减 → 探索减少，利用增加
```

---

## 配置说明

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `HOMEMIND_MODE` | 运行模式 | `simulated` |
| `LLM_BACKEND` | LLM 后端 | `mock` |
| `LLM_MODEL_PATH` | 本地模型路径 | - |

### LLM 后端配置

```python
# Mock 模式（默认，开发/演示）
LLMDecider(backend="mock")

# Llama.cpp 模式（本地推理）
LLMDecider(backend="llama_cpp", model_path="./models/qwen2.5-0.5b-int4.gguf")

# OpenAI 兼容模式（云端 API）
LLMDecider(backend="openai", api_base="https://api.openai.com/v1", api_key="sk-...")
```

---

## 扩展方向

| 方向 | 说明 |
|------|------|
| 多模态扩展 | 加入视觉模态，感知房间人员状态，扩展 DQN 状态空间 |
| LoRA 轻量微调 | 存储余量充足时对 LLM 做增量更新 |
| 多设备协同 | 不同房间共享知识库和 DQN 模型，实现全屋智能 |
| 家庭成员识别 | 声纹区分不同成员，维护独立偏好知识库 |

---

## 优化记录

### 架构优化

| 优化项 | 文件 | 说明 |
|--------|------|------|
| 统一 Embedding 服务 | `core/utils/embedding.py` | 合并 BSR 和 RAG 各自的独立单例 |
| 共享常量中心 | `core/constants.py` | 统一管理场景索引和动作池 |
| 场景索引常量引用 | `main.py`, `demo/simulator.py` | 引用 constants.py 避免硬编码 |

### 功能修复

| 优化项 | 文件 | 说明 |
|--------|------|------|
| DQN 梯度计算修正 | `core/dqn/policy.py` | 完全重写梯度计算逻辑 |
| LLM Mock 返回结构统一 | `core/llm/decision.py` | 场景切换返回 scene 字段 |
| 回家模式场景映射补全 | `demo/simulator.py` | 补充回家模式的设备状态配置 |
| BSR 历史召回动态权重 | `core/bsr/candidate_recall.py` | 基于接受率动态计算权重 |

### 健壮性提升

| 优化项 | 文件 | 说明 |
|--------|------|------|
| process 异常处理 | `main.py` | 各分支添加 try-except |
| Fallback Embedding | `core/utils/embedding.py` | 模型加载失败时返回随机向量 |
| ChromaDB 内存回退 | `core/rag/knowledge_base.py` | ChromaDB 不可用时使用内存模式 |

---

## 致谢

本项目基于以下开源技术构建：

- [sentence-transformers](https://github.com/UKPLab/sentence-transformers) - MiniLM 向量模型
- [ChromaDB](https://github.com/chroma-core/chroma) - 本地向量数据库
- [Flask](https://github.com/pallets/flask) - Web 服务框架
- [Socket.IO](https://socket.io/) - 实时通信
