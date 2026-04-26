# HomeMind 测试执行与优化报告

## 1. 测试目标

本次测试从产品可用性、稳定性、异常恢复和回归保障角度出发，对 HomeMind 的核心链路进行了自动化测试补充与优化。

测试目标包括：

- 验证用户输入后，系统能够正确识别意图并执行设备或场景操作。
- 验证 RAG、BSR、LLM 决策、DQN 策略等核心模块在边界输入下保持稳定。
- 验证 Web API 在正常请求、异常请求和系统未初始化场景下返回可预期结果。
- 验证语音反馈 API 能够记录用户纠错，并被后续归一化逻辑复用。
- 验证 session、preferences 等本地持久化文件损坏时，系统能够回退到默认状态。
- 验证隐私上下文只暴露必要字段，不泄露用户历史明文。

## 2. 测试文件

本次新增和优化后的测试文件如下：

| 文件 | 覆盖范围 | 测试数量 |
|---|---|---:|
| `tests/test_core_product_coverage.py` | 核心模块测试：RAG、BSR、LLM、DQN | 12 |
| `tests/test_system_product_flows.py` | 系统测试、API 回归、语音反馈 API、离家意图回归 | 10 |
| `tests/test_resilience_product_coverage.py` | 数据损坏恢复、持久化韧性、知识库恢复兜底 | 4 |

项目原有测试共 26 个，本次新增和优化后补充 27 个，当前总测试数量为 53 个。

## 3. 单元测试

### 3.1 RAG / KnowledgeBase

覆盖文件：`tests/test_core_product_coverage.py`

已测试内容：

- 知识写入后可以通过关键词检索命中。
- category 过滤能够隔离不同类型知识。
- 无匹配 category 时返回空结果。
- 无可用上下文时 `get_context_prompt` 返回空字符串。
- 用户历史偏好能够提高候选动作偏好分数。

代表用例：

- `test_add_query_and_category_filter_use_memory_fallback`
- `test_empty_query_without_keyword_overlap_returns_no_context`
- `test_preference_score_uses_positive_feedback_history`

### 3.2 BSR 候选召回

覆盖文件：`tests/test_core_product_coverage.py`

已测试内容：

- 规则召回能够命中常见智能家居需求。
- 召回结果会去重。
- 召回数量受 `top_k` 限制。
- 用户历史记录可以被解析为候选动作。
- 无召回结果时返回安全兜底动作。

代表用例：

- `test_rule_recall_deduplicates_candidates_and_caps_top_k`
- `test_history_recall_extracts_actions_from_user_habits`
- `test_no_candidate_returns_safe_fallback`

### 3.3 LLM 决策模块

覆盖文件：`tests/test_core_product_coverage.py`

已测试内容：

- mock 决策器能够将候选动作映射为结构化设备控制命令。
- LLM 输出中夹杂说明文字时，仍能解析 JSON。
- 非法 JSON 输出会降级为低置信度兜底结果。

代表用例：

- `test_mock_decider_maps_top_device_candidate_to_structured_command`
- `test_parse_output_recovers_json_embedded_in_text`
- `test_parse_output_returns_low_confidence_fallback_for_invalid_json`

### 3.4 DQN 策略模块

覆盖文件：`tests/test_core_product_coverage.py`

已测试内容：

- ReplayBuffer 超出容量后保留最新数据。
- QNetwork 输出维度与 6 个场景动作空间一致。
- 用户反馈可以写入 DQN replay buffer。
- 推荐动作始终保持在合法动作范围内。

代表用例：

- `test_replay_buffer_keeps_latest_items_when_capacity_is_exceeded`
- `test_q_network_output_shape_matches_scene_action_space`
- `test_feedback_records_reward_and_preserves_valid_recommendation_range`

## 4. 模块测试

模块测试主要验证各模块在独立运行时的产品行为。

| 模块 | 验证点 |
|---|---|
| KnowledgeBase | 写入、查询、分类过滤、空结果、备份缺失恢复 |
| BSRecall | 规则召回、历史召回、兜底召回 |
| LLMDecider | 结构化命令生成、JSON 解析、异常降级 |
| DQNPolicy | 状态反馈、动作范围、经验池容量 |
| SessionStore | session 文件损坏后恢复默认结构 |
| PreferenceStore | preferences 文件损坏或字段结构错误后恢复默认结构 |
| VoiceFeedbackStore / LanguageNormalizer | 语音纠错记录可被后续归一化复用 |

这些测试使用 mock、临时测试文件或最小构造对象，避免依赖真实云端 LLM、真实 embedding 服务或外部网络。

## 5. 集成测试

覆盖文件：`tests/test_system_product_flows.py`

已测试内容：

- 用户输入“热”后，系统完整经过候选召回、精排、决策、命令校验和设备执行。
- 执行成功后，空调状态变为开启。
- 执行成功后，session 文件被写入。
- 执行成功后，知识库记录用户反馈，形成学习闭环。
- 低置信度未知请求不会强行执行，而是返回澄清问题。

代表用例：

- `test_main_agent_hot_request_updates_device_state_and_memory`
- `test_low_confidence_unknown_request_asks_for_clarification`

## 6. 系统测试

覆盖文件：`tests/test_system_product_flows.py`

已测试内容：

- Web API `/api/query` 对空 query 返回 400。
- Web API `/api/rules/evaluate` 对非法时间格式返回 400。
- Web API 在 Agent 未初始化时返回 500。
- `/api/privacy/status` 返回隐私状态时，只包含最小上下文字段。
- 隐私状态中不包含 `recent_turns`、`last_user_input` 等用户历史明文字段。
- `/api/voice/feedback` 在缺少语音源文本时返回 400。
- `/api/voice/feedback` 能记录 corrected 反馈，并被后续归一化逻辑复用。
- 用户输入“我要走了”时切换离家模式，而不是错误开启空调。

代表用例：

- `test_query_endpoint_rejects_empty_user_input`
- `test_rule_evaluate_rejects_invalid_time_format`
- `test_privacy_status_exposes_minimal_context_after_query`
- `test_endpoints_return_500_when_agent_is_not_initialized`
- `test_voice_feedback_requires_source_text`
- `test_voice_feedback_corrected_text_is_recorded_and_reused`
- `test_query_leave_home_phrase_switches_away_scene_not_air_conditioner`

## 7. 韧性测试

覆盖文件：`tests/test_resilience_product_coverage.py`

本轮优化新增了韧性测试，重点覆盖真实产品中容易出现的本地数据异常。

已测试内容：

- `SessionStore` 遇到损坏 JSON 文件时，回退到默认 session 结构。
- `PreferenceStore` 遇到损坏 JSON 文件时，回退到默认 preference 结构。
- `PreferenceStore` 遇到错误字段类型时，自动归一为合法结构。
- `KnowledgeBase.restore` 在备份文件缺失时返回 `False`，不会抛异常中断流程。

代表用例：

- `test_session_store_recovers_from_corrupted_json`
- `test_preference_store_recovers_from_corrupted_json`
- `test_preference_store_normalizes_wrong_field_shapes`
- `test_knowledge_base_restore_returns_false_for_missing_backup`

## 8. 回归测试

当前回归测试覆盖：

- 核心模块行为回归。
- 主 Agent 端到端链路回归。
- Web API 正常与异常处理回归。
- 隐私最小化回归。
- DQN 反馈学习边界回归。
- 语音反馈纠错回归。
- “我要走了”离家意图回归，防止误开空调。
- 本地持久化文件损坏恢复回归。

全量测试结果：

```text
python -m pytest -q
53 passed in 0.57s
```

## 9. 优化结果

本轮优化前：

```text
44 passed
```

本轮优化后：

```text
53 passed
```

新增覆盖点：

- 新增 `tests/test_resilience_product_coverage.py`，补充 4 个数据韧性测试。
- 扩展 `tests/test_system_product_flows.py`，新增 2 个语音反馈 API 测试。
- 增加“我要走了”离家意图回归测试，防止再次误执行空调控制。
- 增加坏历史语音反馈不会覆盖高置信度内置规则的回归测试。
- 修复测试临时目录策略，避免 Windows 权限问题影响 pytest 收集。
- 更新测试报告，使文档与当前自动化测试结果一致。

## 10. 测试结论

本次优化后，HomeMind 当前自动化测试共 53 个，全部通过。

从产品角度看，当前已覆盖：

- 核心智能家居控制链路。
- 知识库写入与检索链路。
- 候选召回与兜底链路。
- LLM 结构化决策链路。
- DQN 反馈学习链路。
- Web API 正常与异常响应。
- 语音反馈纠错链路。
- 离家意图识别链路。
- 本地数据损坏恢复能力。
- 隐私上下文最小化。

后续仍建议继续补充：

- 真实音频文件的 ASR 端到端测试。
- 浏览器端 UI 自动化测试。
- 并发请求和长时间运行稳定性测试。
- 大文档导入和 RAG 性能测试。
- 真实云端 LLM 的发布前冒烟测试。
