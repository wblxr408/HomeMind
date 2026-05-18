# HomeMind 整体架构 — 面试拷打指南

## 系统定位

HomeMind 是一个本地优先的智能家居 AI Agent，支持：
- **本地运行**：边缘设备（树莓派/迷你主机）即可运行
- **多设备控制**：空调、灯光、电视、音响、风扇、窗户
- **多场景切换**：睡眠、离家、回家、观影、待客、起床等模式
- **定时自动化**：TAP 规则引擎
- **主动推荐**：DQN 强化学习场景推荐
- **隐私优先**：数据不出本地，敏感信息云端过滤

---

## 五层架构

```
┌─────────────────────────────────────────────────────────────┐
│                    交互层 (Interaction)                      │
│            Flask Web API / CLI / 语音输入                    │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   安全层 (Security)                         │
│     InjectionDetector  │  PrivacyRedactor  │  Safety     │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   路由层 (InferenceRouter)                  │
│  classify_intent()  →  decide_route()                      │
│  chat | action | clarify | automation | unsupported        │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   候选召回 (BSR)                           │
│  规则召回  │  向量召回  │  历史召回                          │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   精排层 (LSR)                             │
│  5维加权评分  │  显式上下文注入  │  多轮会话追踪              │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   决策层 (LLM Decision)                    │
│  Mock  │  Ollama  │  llama_cpp  │  OpenAI               │
│  plan_intent()  →  decide_local() / decide_cloud()        │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   执行层 (Execution)                         │
│  CommandValidator  │  TAPEngine  │  DeviceController      │
└──────────────────────────┬────────────────────────────────┘
                           │
┌──────────────────────────▼────────────────────────────────┐
│                   学习层 (Learning)                         │
│  DQN Policy  │  KnowledgeBase  │  PreferenceStore        │
│  SessionStore  │  FeedbackStore                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 完整请求流程

### 场景1：显式命令 "关闭音响"

```
用户输入: "关闭音响"
      ↓
LanguageNormalizer: "关闭音响" (zh, 0.92)
      ↓
Safety / InjectionDetector: 通过
      ↓
Router.classify_intent(): "action_command"
      ↓
Router.decide_route(): 无需候选 → route="local"
      ↓
BSR.recall(): ["关闭音响"] (rule)
      ↓
LSR.rank(): final_score=0.91
      ↓
LLMDecision.decide_local(): action="设备控制", device="音响", device_action="off"
      ↓
CommandValidator.validate(): valid=True, risk_level="low"
      ↓
RuntimeSecurityChain.evaluate(): allowed=True, effect="allow"
      ↓
DeviceController.execute(): 关闭音响
      ↓
SessionStore.update_from_decision()
PreferenceStore.record_action_accept()
KnowledgeBase.update_feedback()
      ↓
返回: {"status": "success", "message": "已关闭音响"}
```

### 场景2：模糊输入 "有点闷"

```
用户输入: "有点闷"
      ↓
LanguageNormalizer: "有点闷" (zh, 0.5)
      ↓
Router.classify_intent(): "action_command" (comfort_request)
      ↓
Router.decide_route(): 无候选 → clarification?
      ↓
BSR.recall(): ["打开空调", "打开风扇", "打开窗户"] (vector)
      ↓
LSR.rank():
  - "打开空调": f1*0.30 + f2(高温)*0.10 + f5(偏好)*0.35 = 0.85
  - "打开风扇": 0.72
      ↓
LLMDecision.decide_local(): action="设备控制", device="空调", device_action="on"
      ↓
CommandValidator.validate(): valid=True, risk_level="low"
      ↓
Execution → 返回: {"status": "success", "message": "已打开空调，温度设为26°C"}
```

---

## 数据流

### 记忆数据流
```
用户交互 → SessionStore (短期)
              ↓
       PreferenceStore (长期)
              ↓
         KnowledgeBase (RAG检索)
              ↓
       BSR历史召回 / LSR偏好评分 / LLM上下文
```

### DQN 学习流
```
定时触发 → DQN.recommend(context)
              ↓
         主动推荐场景 → 用户反馈
              ↓
         DQN.record_feedback()
              ↓
         PreferenceStore.record_dqn_feedback()
              ↓
         定期增量学习 (每50次或每日)
```

---

## 关键设计决策

### 1. 为什么本地优先？
- 隐私：用户数据不出家门
- 延迟：局域网内 <100ms
- 可靠性：断网也能用
- 成本：无云服务费用

### 2. 为什么 BSR → LSR → LLM 三阶段？
- **BSR**：快速召回，O(n) 规则 + 向量
- **LSR**：精打细算，5维加权特征
- **LLM**：最终决策，处理边界case
- 分层解耦：各层可独立优化

### 3. 为什么用 Double DQN 而非 RLHF？
- 智能家居动作空间小（9个场景）
- Double DQN 收敛快、计算轻
- RLHF 需要大量人工标注反馈
- DQN 可增量学习，适合持续优化

### 4. 为什么 RAG 用本地向量库？
- ChromaDB 是轻量级向量数据库
- 可完全离线运行
- 支持增量索引更新
- 比 FAISS 更易部署

### 5. 安全多层防御的设计哲学？
- **纵深防御**：每一层都有可能发现并阻止问题
- **零信任**：每次命令都验证身份和能力
- **渐进式自主**：从需要确认到自动执行，需要信任积累
- **隐私优先**：数据最小化，敏感信息不过云

---

## 面试高频问题

### Q: 如果向量模型加载失败会怎样？
A: BSR 向量路返回空，规则和历史路仍正常工作。LSR 和 LLM 决策不受影响，只是少了向量召回的候选。

### Q: 多轮对话如何处理上下文？
A: SessionStore 记录 last_action 和 recent_turns。LSR 的 `_resolve_follow_up_from_last_action` 根据上一轮设备状态推断本轮意图。

### Q: 如何保证热水器的安全执行？
A: 三层保护：
1. CommandValidator：`risk_level="medium"`，60°C以上才"high"
2. InferenceRouter：高风险设备强制 clarify
3. RuntimeSecurityChain：高风险命令要求 trust_level="verified"

### Q: 系统如何处理升级和数据迁移？
A:
1. 加密存储 → 明文兼容迁移（SessionStore / PreferenceStore）
2. CHROMA_AVAILABLE 检测 → 无 ChromaDB 时用内存
3. PyTorch → NumPy fallback（DQN）
4. MODEL_AVAILABLE 检测 → 无向量模型时用规则

### Q: 为什么 confidence_threshold 默认是 0.75？
A: 来自 GLOBAL_CONFIG，默认值平衡精确率和召回率。可通过环境变量 `CONFIDENCE_THRESHOLD` 调整。

### Q: 如何扩展支持新设备？
A: 需要修改以下位置：
1. `core/constants.py` → ACTION_POOL
2. `core/bsr/candidate_recall.py` → rule_map
3. `core/lsr/precision_ranking.py` → DEVICE_ALIASES / direct_action_map
4. `core/llm/decision.py` → DEVICE_ACTION_MAP
5. `core/execution/command_validator.py` → DEVICE_ACTIONS / PARAM_RANGES
6. `core/router/inference_router.py` → 路由支持
