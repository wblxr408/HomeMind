# HomeMind 落地情况审计

更新日期：2026-04-22

## 目的

本文档用于核对 `README.md` / `design.md` 中宣称的能力与当前仓库实际实现状态，区分：

- 已落地
- 部分落地
- 未落地

同时给出证据位置、运行验证结果与建议修复优先级，便于后续排期。

## 审计范围

- 文档声明：`README.md`、`design.md`
- 主入口：`main.py`、`run_web.py`
- Web 服务：`web/server.py`、`web/client/index.html`
- 核心模块：`core/bsr/`、`core/lsr/`、`core/llm/`、`core/dqn/`、`core/rag/`
- 工具与模拟：`tools/`、`demo/`
- 依赖：`requirements.txt`、`requirements-web.txt`

## 审计结论摘要

当前仓库更接近“架构原型 + 演示版”，并非文档描述中的“完整端侧落地版本”。

结论如下：

- 已落地：基础的规则/向量召回框架、轻量排序框架、知识库封装、DQN 骨架、Web UI 壳子、设备模拟器。
- 部分落地：RAG 闭环、DQN 学习闭环、Web 可视化流水线、协议适配层。
- 未落地：真实 LLM 本地推理、语音识别、真实智能家居协议接入主流程、加密存储、天气 API 与缓存、多轮任务延续能力。
- 当前代码还存在若干接口签名不匹配与运行时错误，导致部分“看起来已经有模块”的能力实际不可用。

## 一、已落地

### 1. BSR 候选召回骨架

状态：已落地

说明：

- 已实现规则召回、向量召回、历史召回三路结构。
- 候选动作池、规则映射、去重逻辑均已存在。

证据：

- `core/bsr/candidate_recall.py`
- `core/constants.py`

备注：

- 结构已具备，但仍以演示型规则和有限动作池为主。

### 2. LSR 轻量精排骨架

状态：已落地

说明：

- 已实现 5 维特征打分。
- 包含温度、湿度、时间、用户偏好等特征。

证据：

- `core/lsr/precision_ranking.py`

备注：

- 当前更像线性打分器，不是文档中强调的成熟“轻量 MLP”效果级实现。

### 3. 本地知识库封装

状态：已落地

说明：

- 已实现 `KnowledgeBase`。
- 支持 ChromaDB 不可用时回退到内存模式。
- 提供查询、写入、上下文拼接、偏好得分等接口。

证据：

- `core/rag/knowledge_base.py`

备注：

- 接口存在，但是否形成可靠学习效果，仍受上层调用逻辑影响。

### 4. DQN 基础骨架

状态：已落地

说明：

- 已实现极轻量 Q 网络、经验池、推荐、反馈记录、保存/加载。
- 已注入合成预训练样本。

证据：

- `core/dqn/policy.py`

备注：

- 有“能运行的骨架”，但距离稳定的真实主动推荐系统还有明显距离。

### 5. Web 面板与设备模拟器

状态：已落地

说明：

- 已有前端控制台页面。
- 已有设备状态展示、场景按钮、文本输入、WebSocket 通信壳子。
- 已有设备模拟器和场景模拟器。

证据：

- `web/client/index.html`
- `web/server.py`
- `demo/device_simulator.py`
- `demo/simulator.py`

## 二、部分落地

### 1. RAG 闭环

状态：部分落地

说明：

- 已有知识写入接口与知识检索接口。
- CLI 主流程中也会写回知识库。

证据：

- `tools/kb_write.py`
- `core/rag/knowledge_base.py`
- `main.py`

问题：

- `main.py` 在每次执行后无条件按“接受”写入知识库，不等于真实用户反馈学习。
- Web 端 `/api/kb/add` 调用的是 `kb_writer.write(...)`，但 `KBWriter` 没有这个方法，接口未打通。

证据：

- `main.py` 第 156 行附近
- `web/server.py` 第 655-664 行附近
- `tools/kb_write.py`

结论：

- “知识库写回”这件事存在，但“基于真实反馈形成可靠 RAG 闭环”还未真正成立。

### 2. DQN 学习闭环

状态：部分落地

说明：

- 已有 `record_feedback()`、经验回放池、轻量更新逻辑。
- Web 与 CLI 也都尝试提供主动推荐/反馈入口。

证据：

- `core/dqn/policy.py`
- `tools/dqn_feedback.py`
- `main.py`
- `web/server.py`

问题：

- Web 端 `/api/dqn/feedback` 的参数传递与 `DQNFeedback.record()` 定义不匹配，接口未打通。
- 主动推荐逻辑存在，但配套的真实用户交互闭环不完整。

证据：

- `web/server.py` 第 624-633 行附近
- `tools/dqn_feedback.py`

结论：

- DQN 模块有雏形，但距离文档中描述的“独立主动感知并稳定增量学习”还有差距。

### 3. 协议适配层

状态：部分落地

说明：

- 已有 Matter、MQTT、小米、Home Assistant、模拟协议等类定义。
- 也有统一网关类 `SmartHomeGateway`。

证据：

- `core/protocols/smart_home_gateway.py`

问题：

- 协议层多数实现仍是模拟连接、模拟设备发现、模拟状态。
- `run_web.py` 虽声明支持 `simulated` / `real` 两种模式，但实际并没有按模式初始化协议网关。
- 当前主流程依然直接调用 `DeviceController` 内存状态，而非协议层。

证据：

- `run_web.py`
- `web/server.py`
- `tools/device_control.py`

结论：

- 协议层目前更像“接口设计 + 示例实现”，不是已接入主流程的真实设备控制方案。

### 4. Web AI 流水线展示

状态：部分落地

说明：

- 前端已经具备 BSR / LSR / LLM / 执行 四步可视化界面。
- 后端也尝试逐步发出流水线事件。

证据：

- `web/client/index.html`
- `web/server.py`

问题：

- `BSR.recall()` 调用时多传了 `top_k` 参数，和函数签名不匹配。
- `LSR.rank()` 调用参数顺序错误。
- 这会让 Web 端完整 AI 链路不稳定，甚至直接降级。

证据：

- `web/server.py` 第 180 行附近
- `core/bsr/candidate_recall.py`
- `web/server.py` 第 197 行附近
- `core/lsr/precision_ranking.py`

结论：

- UI 壳子完成度较高，但后端主链路还没稳定跑通。

## 三、未落地

### 1. 本地 LLM 推理

状态：未落地

文档声明：

- `README.md` 写明核心推理采用 `llama.cpp + Qwen2.5-0.5B`
- `design.md` 也将其列为核心技术栈

证据：

- `README.md`
- `design.md`

实际情况：

- 主入口默认使用 `LLMDecider()`，而其默认后端是 `mock`。
- Web 端也显式写死 `backend="mock"`。
- `llama-cpp-python` 仍是注释依赖，没有默认安装。

证据：

- `main.py`
- `web/server.py`
- `core/llm/decision.py`
- `requirements-web.txt`

额外问题：

- `mock` 分支自身还有变量名错误，`device_map` 未定义，导致 CLI 主流程实际会报错。

证据：

- `core/llm/decision.py` 第 141 行附近

结论：

- 当前没有真正落地“本地端侧大模型推理”。

### 2. 语音识别

状态：未落地

文档声明：

- 文档多处写明使用 `Whisper-tiny` / `faster-whisper`

证据：

- `README.md`
- `design.md`

实际情况：

- 前端“语音指令”区域其实是文本输入框。
- 没有麦克风采集、录音、音频上传、浏览器语音识别或服务端转写接口。
- 依赖中也没有 `whisper` / `faster-whisper`。

证据：

- `web/client/index.html`
- `web/server.py`
- `requirements-web.txt`

结论：

- 语音识别目前停留在方案文档层面。

### 3. 多轮任务延续 / 历史指代恢复 / 定时任务

状态：未落地

文档与演示声明：

- 支持“像昨天晚上那样”
- 支持“我要出门了 → 预计几点回来 → 提前开空调”

证据：

- `README.md`
- `design.md`
- `demo/simulator.py`

实际情况：

- 这些能力主要存在于演示脚本文案中。
- 动作池、主流程、工具层中没有真正的历史状态恢复机制、调度系统或多轮对话状态机。

证据：

- `demo/simulator.py`
- `core/constants.py`
- `core/bsr/candidate_recall.py`
- `main.py`

结论：

- 属于演示场景，尚未成为实际系统能力。

### 4. 加密存储

状态：未落地

文档声明：

- 知识库和 DQN 经验回放池采用本地加密存储

证据：

- `design.md`

实际情况：

- 知识库存储直接使用 ChromaDB 持久化目录或内存。
- DQN 使用 `pickle` 明文落盘。
- 仓库中没有任何加密读写逻辑。

证据：

- `core/rag/knowledge_base.py`
- `core/dqn/policy.py`

结论：

- 未实现。

### 5. 天气 API 与本地缓存

状态：未落地

文档声明：

- 唯一外部请求为天气 API，结果本地缓存

证据：

- `design.md`

实际情况：

- 天气查询返回固定字符串，没有真实 API 请求，也没有缓存。

证据：

- `tools/info_query.py`

结论：

- 未实现。

### 6. 声纹识别 / 家庭成员识别

状态：未落地

文档声明：

- 通过声纹识别区分家庭成员，为每个成员维护独立偏好知识库和 DQN 策略

证据：

- `README.md`
- `design.md`

实际情况：

- 仓库中没有声纹模型、用户画像体系、多用户知识库分层或多用户策略管理实现。

结论：

- 未实现。

## 四、运行验证结果

### 1. CLI 主流程验证

验证方式：

- 运行 `HomeMindAgent().process("有点闷")`

结果：

- 触发 `NameError: name 'device_map' is not defined`

判定：

- CLI 主流程当前不是稳定可运行状态。

涉及文件：

- `main.py`
- `core/llm/decision.py`

### 2. Web 查询主流程验证

验证方式：

- 运行 `HomeMindWebAgent().process_query("有点闷")`

结果：

- 返回 `{"status": "no_action", "message": "AI 模块未加载"}`

判定：

- 在当前仓库依赖状态下，Web 查询链路无法达到文档所述效果。

涉及文件：

- `web/server.py`
- `core/utils/embedding.py`
- `requirements.txt`
- `requirements-web.txt`

### 3. 当前环境依赖缺失

验证中观察到：

- `sentence_transformers` 缺失
- `chromadb` 缺失

影响：

- 向量召回与知识库能力无法按设计稳定工作
- Web Agent 很容易降级

## 五、关键差异总表


| 能力          | 文档表述                          | 当前状态                   | 判定   |
| ----------- | ----------------------------- | ---------------------- | ---- |
| 本地 LLM 推理   | llama.cpp + Qwen2.5-0.5B      | 默认 mock，且 mock 路径有 bug | 未落地  |
| 语音识别        | Whisper-tiny / faster-whisper | 无前后端实现、无依赖             | 未落地  |
| BSR 召回      | 三路召回                          | 已有实现                   | 已落地  |
| LSR 精排      | 轻量排序                          | 已有实现                   | 已落地  |
| RAG 知识库     | ChromaDB 持续学习                 | 有接口，有写入，但闭环不严谨         | 部分落地 |
| DQN 主动推荐    | 独立流程 + 反馈学习                   | 有骨架，Web 反馈未打通          | 部分落地 |
| 协议接入        | Matter/MQTT/小米/HA             | 有适配层，但未接主流程            | 部分落地 |
| 多轮任务延续      | 历史恢复、定时预热                     | 基本停留在演示脚本              | 未落地  |
| 天气 API + 缓存 | 真实外部请求 + 本地缓存                 | 固定返回文案                 | 未落地  |
| 本地加密存储      | 知识库与经验池加密                     | 无实现                    | 未落地  |
| 家庭成员声纹识别    | 多用户个性化                        | 无实现                    | 未落地  |


## 六、建议修复优先级

### P0：先修“能不能跑”

1. 修复 `core/llm/decision.py` 中 `device_map` 变量错误。
2. 修复 `web/server.py` 中 BSR / LSR 的接口调用错误。
3. 修复 `/api/kb/add` 与 `/api/dqn/feedback` 的方法签名问题。
4. 补齐最基本依赖，使当前仓库达到可运行状态。

### P1：把文本链路做实

1. 确认并接入真实 LLM 后端，至少先完成单一路径。
2. 保证 CLI 与 Web 使用同一套稳定的 BSR → LSR → LLM → 执行流程。
3. 将知识写回改为基于真实用户反馈，而不是默认“接受”。

### P2：补齐文档中最核心的差距

1. 接入语音识别。
2. 把协议层真正挂进主流程，至少打通一种真实设备协议。
3. 实现天气 API 与缓存。

### P3：再做增强能力

1. 多轮任务状态管理。
2. 历史指代恢复。
3. 定时任务与场景预热。
4. 多用户/声纹识别。
5. 加密存储。

## 七、整体判断

如果以“课程/方案展示原型”的标准看，这个仓库已经具备不错的结构和演示价值。

如果以 `README.md` / `design.md` 当前表述的“已完成端侧智能家居 Agent”标准看，当前实现明显偏乐观，至少以下几项还不能算真正落地：

- 真实 LLM 推理
- 语音识别
- 真实设备协议接入
- 多轮任务延续
- 加密存储
- 天气 API 与缓存

更准确的项目定位建议调整为：

> HomeMind 当前是一个具备 BSR / LSR / RAG / DQN 架构原型的智能家居演示系统，核心文本链路和学习闭环已有基础实现，但仍有若干关键模块停留在 mock、模拟器或未接入状态。

