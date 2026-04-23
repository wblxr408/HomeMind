# HomeMind 落地情况审计

更新日期：2026-04-23

## 目的

本文档用于核对 `README.md` / `design.md` 中宣称的能力与当前仓库实际实现状态，区分：

- 已落地
- 部分落地
- 未落地

同时补充“相对上一版审计的进展同步”，避免旧结论与当前代码状态不一致。

## 审计范围

- 文档声明：`README.md`、`design.md`
- 主入口：`main.py`、`run_web.py`
- Web 服务：`web/server.py`、`web/client/index.html`
- 核心模块：`core/bsr/`、`core/lsr/`、`core/llm/`、`core/dqn/`、`core/rag/`、`core/security.py`
- 工具与模拟：`tools/`、`demo/`
- 依赖：`requirements.txt`、`requirements-web.txt`

## 审计结论摘要

与 2026-04-22 版审计相比，仓库确实有一批跟进动作已经发生，但“进度已推进”不等于“系统已稳定落地”。

当前结论如下：

- 已明确跟进：`device_map` 相关 CLI 阻塞已消失、`run_web.py` 已按模式初始化协议网关、Web 前端已接入浏览器语音识别、天气查询已接入 Open-Meteo + TTL 缓存、加密存储代码已新增、`requirements-web.txt` 已补充核心依赖声明。
- 仍然部分落地：RAG 闭环、DQN 学习闭环、协议适配层、Web AI 流水线、语音能力、天气工具、加密存储。
- 仍未落地：真实本地 LLM 推理、多轮任务延续/历史指代恢复/定时任务、声纹识别/多用户个性化。
- 新的关键阻塞：`core/dqn/policy.py`、`core/rag/knowledge_base.py`、`core/security.py` 当前存在语法错误；`web/server.py` 里仍有 BSR / LSR 调用签名不匹配，以及 `/api/kb/add`、`/api/dqn/feedback` REST 接口未打通。

更准确的项目定位建议调整为：

> HomeMind 当前是一个持续推进中的智能家居 Agent 原型。部分文档差异已经开始补齐，但主链路仍未达到“稳定可运行、能力闭环完整”的状态。

## 一、相对上一版审计，已同步跟进的事项

### 1. CLI 侧 `device_map` 阻塞已修复

状态：已跟进

说明：

- 上一版审计中提到 `core/llm/decision.py` 的 mock 分支存在 `device_map` 未定义问题。
- 当前该问题已不存在，mock 路径改为通过 `scene_map` / 规则分支直接返回结构化决策。

证据：

- `core/llm/decision.py`
- `main.py`

影响：

- 旧审计里“CLI 因 `device_map` 报错而失败”的结论已过期。
- 但 CLI 仍无法运行，当前新的阻塞点已转为 `core/dqn/policy.py` 的语法错误。

### 2. 协议网关初始化已接入 `run_web.py`

状态：已跟进

说明：

- 上一版审计认为 `run_web.py` 只是声明 `simulated` / `real` 两种模式，但没有真正按模式初始化协议层。
- 当前 `run_web.py` 已实现 `init_protocol_gateway(mode)`，并在启动时将协议网关注入 Web Agent。

证据：

- `run_web.py`
- `tools/device_control.py`

影响：

- “协议模式未初始化”这一条旧结论已不再成立。
- 但真实协议本身仍主要是示例/模拟实现，因此只能从“未接线”上升到“部分落地”。

### 3. Web 前端已接入浏览器端语音识别

状态：已跟进

说明：

- `web/client/index.html` 已使用 `SpeechRecognition / webkitSpeechRecognition`。
- 页面存在麦克风按钮、聆听状态、实时识别文本展示和自动提交逻辑。
- `web/server.py` 也预留了 `/api/voice/transcribe` 接口。

证据：

- `web/client/index.html`
- `web/server.py`
- `README.md`

影响：

- “语音识别完全没有前后端实现”这一结论已过期。
- 但当前只实现了浏览器 Web Speech API 路径，并未实现文档宣称的 `Whisper-tiny / faster-whisper` 本地转写，因此整体应从“未落地”上调为“部分落地”。

### 4. 天气 API 与缓存逻辑已新增

状态：已跟进

说明：

- `tools/info_query.py` 当前已包含 `_WeatherCache`。
- 天气查询已接入 Open-Meteo API，并带 30 分钟 TTL 内存缓存。
- 无法联网或请求失败时会降级为模拟天气数据。

证据：

- `tools/info_query.py`

影响：

- 上一版审计中“天气查询只是固定字符串，无真实 API、无缓存”的结论已过期。
- 但该能力目前仍是工具层实现，尚未完成系统级验证，因此更适合归类为“部分落地”而不是“完全落地”。

### 5. 加密存储代码已新增并接入 KnowledgeBase / DQN

状态：已跟进

说明：

- 新增了 `core/security.py`，提供 `EncryptedStorage`、`save_pickle()`、`load_pickle()` 等加密读写封装。
- `KnowledgeBase` 与 `DQNPolicy` 已尝试通过 `get_encrypted_storage()` 接入该能力。

证据：

- `core/security.py`
- `core/rag/knowledge_base.py`
- `core/dqn/policy.py`
- `requirements-web.txt`

影响：

- “仓库中完全没有任何加密读写逻辑”的旧结论已过期。
- 但当前实现仍存在两个问题：
  1. 相关文件本身存在语法错误，当前无法稳定导入。
  2. ChromaDB 持久化目录本身并未实现透明加密。

因此该项目前只能判定为“部分落地”。

### 6. 依赖声明已补齐一部分

状态：已跟进

说明：

- `requirements-web.txt` 当前已声明 `sentence-transformers`、`chromadb`、`cryptography`。
- 这与上一版审计中“关键依赖尚未声明”的状态已有明显改善。

证据：

- `requirements-web.txt`

影响：

- 依赖声明层面有进展。
- 但当前是否已在实际运行环境安装，不在本文档结论中直接推定。

## 二、当前状态复核

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

- 当前是线性加权打分器，不是文档中强调的成熟“轻量 MLP”效果级实现。

### 3. Web 面板与设备模拟器

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

### 4. RAG 闭环

状态：部分落地

说明：

- 已有知识写入接口与知识检索接口。
- CLI 主流程中会按置信度将结果写回知识库，不再是旧版本中“无条件按接受写入”。
- `KnowledgeBase` 还新增了备份/恢复接口，并尝试接入加密存储。

证据：

- `tools/kb_write.py`
- `core/rag/knowledge_base.py`
- `main.py`

问题：

- `main.py` 当前仍是根据置信度代理反馈，不能等同于真实用户反馈闭环。
- Web 端 `/api/kb/add` 调用的是 `kb_writer.write(...)`，但 `KBWriter` 并没有该方法，REST 接口仍未打通。
- `core/rag/knowledge_base.py` 当前存在语法错误，导致模块本身不可稳定导入。

结论：

- “知识库写回”能力在设计和代码层面都有推进。
- 但“基于真实反馈形成稳定 RAG 闭环”仍未成立，而且当前文件语法错误使这一模块实际上处于不可用状态。

### 5. DQN 学习闭环

状态：部分落地

说明：

- 已有 `QNetwork`、`ReplayBuffer`、`recommend()`、`record_feedback()`、保存/加载等骨架。
- CLI 主流程与 WebSocket 内部流程都已尝试接 DQN 反馈链路。
- DQN 当前也尝试接入加密存储。

证据：

- `core/dqn/policy.py`
- `tools/dqn_feedback.py`
- `main.py`
- `web/server.py`

问题：

- `web/server.py` 的 `/api/dqn/feedback` 参数传递与 `DQNFeedback.record()` 定义仍不匹配，REST 接口未打通。
- `core/dqn/policy.py` 当前存在语法错误，导致该模块无法导入。
- 主动推荐逻辑存在，但真实用户交互闭环仍不完整。

结论：

- DQN 模块较上一版有进一步代码补充，但依然只能算“骨架存在、主链路未稳定”。

### 6. 协议适配层

状态：部分落地

说明：

- 已有 Matter、MQTT、小米、Home Assistant 等协议类定义。
- 已有统一网关类 `SmartHomeGateway`。
- `run_web.py` 已按模式初始化协议网关。
- `DeviceController` 已支持从网关同步设备状态和向网关推送状态。

证据：

- `core/protocols/smart_home_gateway.py`
- `run_web.py`
- `tools/device_control.py`

问题：

- 各协议实现多数仍是模拟连接、模拟设备发现、模拟状态。
- 当前尚无一个真实协议路径被验证为生产可用。

结论：

- 协议层已经从“接口设计 + 未接线”提升为“已接主流程的适配骨架”。
- 但距离真实设备接入可用仍有明显差距。

### 7. Web AI 流水线展示

状态：部分落地

说明：

- 前端已经具备 BSR / LSR / LLM / 执行 四步可视化界面。
- 后端也会发出流水线事件。

证据：

- `web/client/index.html`
- `web/server.py`

问题：

- `BSR.recall()` 调用时仍多传了 `top_k` 参数，而函数签名并不接受该参数。
- `LSR.rank()` 调用参数顺序仍然错误，当前代码传的是 `(candidates, query, context)`，而实际签名是 `(query, candidates, context, kb=None)`。
- 这意味着 Web 端完整 AI 链路依然不稳定。

结论：

- UI 展示壳子已完成。
- 但主链路未稳定跑通，仍应判定为部分落地。

### 8. 语音识别

状态：部分落地

文档声明：

- 文档多处写明使用 `Whisper-tiny` / `faster-whisper`。

实际情况：

- 浏览器端语音识别已实现，前端可通过 Web Speech API 采集和转写。
- 服务器端 `/api/voice/transcribe` 只是预留接口，未接入 `faster-whisper`。
- 依赖中仍未默认启用 `faster-whisper`。

证据：

- `web/client/index.html`
- `web/server.py`
- `README.md`
- `requirements-web.txt`

结论：

- 相比上一版，应从“未落地”调整为“部分落地”。
- 但和文档宣称的本地 Whisper 路径仍不一致。

### 9. 天气 API 与本地缓存

状态：部分落地

文档声明：

- 唯一外部请求为天气 API，结果本地缓存。

实际情况：

- `InfoQuery` 已实现 Open-Meteo 请求、内存缓存和失败降级。
- 当前该能力停留在工具层，尚未完成系统级稳定性验证。

证据：

- `tools/info_query.py`

结论：

- 相比上一版应从“未落地”调整为“部分落地”。

### 10. 加密存储

状态：部分落地

文档声明：

- 知识库和 DQN 经验回放池采用本地加密存储。

实际情况：

- 已新增 `core/security.py`。
- `DQNPolicy` 的保存/加载、`KnowledgeBase` 的备份/恢复已尝试调用加密存储。
- 但对应文件当前存在语法错误，且 ChromaDB 持久化本体仍不是透明加密存储。

证据：

- `core/security.py`
- `core/rag/knowledge_base.py`
- `core/dqn/policy.py`

结论：

- 相比上一版应从“未落地”调整为“部分落地”。
- 但还不能视为真正完成。

## 三、未落地

### 1. 本地 LLM 推理

状态：未落地

文档声明：

- `README.md` 写明核心推理采用 `llama.cpp + Qwen2.5-0.5B`
- `design.md` 也将其列为核心技术栈

实际情况：

- 主入口默认使用 `LLMDecider()`，其默认后端仍是 `mock`。
- Web 端也显式写死 `backend="mock"`。
- `llama-cpp-python` 仍是注释依赖，没有默认安装路径。

证据：

- `main.py`
- `web/server.py`
- `core/llm/decision.py`
- `requirements-web.txt`

结论：

- `device_map` 旧 bug 虽然已修复，但这不改变“真实本地 LLM 尚未落地”的判断。

### 2. 多轮任务延续 / 历史指代恢复 / 定时任务

状态：未落地

文档与演示声明：

- 支持“像昨天晚上那样”
- 支持“我要出门了 → 预计几点回来 → 提前开空调”

实际情况：

- 这些能力仍主要存在于演示脚本文案中。
- 动作池、主流程、工具层中没有真正的历史状态恢复机制、调度系统或多轮对话状态机。

证据：

- `README.md`
- `design.md`
- `demo/simulator.py`
- `core/constants.py`
- `main.py`

结论：

- 仍未落地。

### 3. 声纹识别 / 家庭成员识别

状态：未落地

文档声明：

- 通过声纹识别区分家庭成员，为每个成员维护独立偏好知识库和 DQN 策略。

实际情况：

- 仓库中没有声纹模型、用户画像体系、多用户知识库分层或多用户策略管理实现。

证据：

- `README.md`
- `design.md`

结论：

- 仍未落地。

## 四、运行验证结果

### 1. 静态编译验证

验证方式：

- 对关键文件执行 `py_compile.compile(..., doraise=True)`。

结果：

- 可编译：
  - `core/llm/decision.py`
  - `main.py`
  - `web/server.py`
  - `tools/info_query.py`
- 不可编译：
  - `core/dqn/policy.py`
  - `core/security.py`
  - `core/rag/knowledge_base.py`

判定：

- 当前仓库并非“局部功能有缺陷”而已，而是存在会阻断导入的语法错误。

### 2. CLI 主流程验证

验证方式：

- 运行：
  - `from main import HomeMindAgent`
  - `agent = HomeMindAgent()`
  - `agent.process("有点闷")`

结果：

- 导入阶段即因 `core/dqn/policy.py` 语法错误失败。

判定：

- CLI 仍不是稳定可运行状态。
- 但当前阻塞点已不是上一版审计中的 `device_map`，而是新的语法错误。

涉及文件：

- `main.py`
- `core/dqn/policy.py`

### 3. Web 查询主流程验证

验证方式：

- 运行：
  - `from web.server import HomeMindWebAgent`
  - `agent = HomeMindWebAgent()`
  - `agent.process_query("有点闷")`

结果：

- 导入阶段同样被 `core/dqn/policy.py` 语法错误阻断。

判定：

- Web 主流程当前不可稳定启动。

涉及文件：

- `web/server.py`
- `core/dqn/policy.py`

### 4. 额外运行风险

在静态检查中还观察到以下未修问题：

- `web/server.py` 仍错误调用 `BSR.recall(..., top_k=5)`。
- `web/server.py` 仍错误调用 `LSR.rank(candidates, query, context)`。
- `/api/kb/add` 仍调用不存在的 `kb_writer.write(...)`。
- `/api/dqn/feedback` 仍以错误参数调用 `dqn_fb.record(...)`。

判定：

- 即便先修复语法错误，Web 主链路仍需继续修正接口和调用签名。

## 五、关键差异总表

| 能力 | 文档表述 | 当前状态 | 判定 |
| --- | --- | --- | --- |
| 本地 LLM 推理 | llama.cpp + Qwen2.5-0.5B | 默认仍为 mock，`device_map` 旧 bug 已修 | 未落地 |
| 语音识别 | Whisper-tiny / faster-whisper | 浏览器 Web Speech API 已接入，服务端 Whisper 未接 | 部分落地 |
| BSR 召回 | 三路召回 | 已有实现 | 已落地 |
| LSR 精排 | 轻量排序 | 已有实现 | 已落地 |
| RAG 知识库 | ChromaDB 持续学习 | 有检索/写回/备份设计，但 REST 未打通且文件有语法错误 | 部分落地 |
| DQN 主动推荐 | 独立流程 + 反馈学习 | 有骨架与反馈逻辑，但 REST 未打通且文件有语法错误 | 部分落地 |
| 协议接入 | Matter/MQTT/小米/HA | 网关已接入启动流程，控制器已支持同步，但多为模拟实现 | 部分落地 |
| Web AI 流水线 | BSR → LSR → LLM → 执行 可视化 | UI 已成形，后端调用签名仍错误 | 部分落地 |
| 多轮任务延续 | 历史恢复、定时预热 | 基本停留在演示脚本 | 未落地 |
| 天气 API + 缓存 | 真实外部请求 + 本地缓存 | 工具层已实现 Open-Meteo + TTL 缓存 | 部分落地 |
| 本地加密存储 | 知识库与经验池加密 | 已有代码接入，但文件有语法错误且未覆盖 ChromaDB 持久化 | 部分落地 |
| 家庭成员声纹识别 | 多用户个性化 | 无实现 | 未落地 |

## 六、建议修复优先级

### P0：先修“能不能导入、能不能启动”

1. 修复 `core/dqn/policy.py` 中的语法错误。
2. 修复 `core/security.py` 中的语法错误。
3. 修复 `core/rag/knowledge_base.py` 中的语法错误。

### P1：修主链路调用错误

1. 修复 `web/server.py` 中 BSR / LSR 的接口调用签名。
2. 修复 `/api/kb/add` 与 `/api/dqn/feedback` 的方法签名问题。
3. 在修复后重新做 CLI / Web smoke test。

### P2：把已推进的能力做实

1. 将知识写回改为基于真实用户反馈，而不是用置信度代替反馈。
2. 至少打通一条稳定的协议接入路径。
3. 为天气接口与加密存储补充最小可运行验证。

### P3：补齐文档中仍明显缺失的核心能力

1. 接入真实本地 LLM 后端。
2. 接入服务端语音识别，或明确将文档改为“浏览器端语音输入”。
3. 实现多轮任务状态管理 / 历史指代恢复 / 定时任务。
4. 实现多用户 / 声纹识别。

## 七、整体判断

如果以“课程 / 方案展示原型”的标准看，HomeMind 的结构化设计、模块拆分和演示表达仍然有价值。

如果以 `README.md` / `design.md` 当前表述的“端侧智能家居 Agent 已完成落地”标准看，当前仓库仍明显偏乐观，原因不再只是“个别能力缺失”，还包括：

- 若干能力虽已开始补实现，但还没有闭环打通。
- 关键模块当前存在语法错误，主程序无法稳定启动。
- 文档宣称与当前实际能力仍有明显落差，尤其是本地 LLM、Whisper 路径、多轮任务与多用户个性化。

更准确的当前定位建议为：

> HomeMind 当前是一个正在补齐文档差异的智能家居 Agent 原型。BSR / LSR / Web UI 等基础框架已具备，协议、语音、天气、加密等能力也已开始跟进，但主链路仍需先完成语法修复、接口修复和运行验证，才能进入“稳定可用”的阶段。
