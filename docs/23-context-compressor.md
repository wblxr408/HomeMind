# Context Compressor — 面试拷打指南

## 核心问题：为什么需要上下文压缩？

**场景**：HomeMind 的 session history + preference + RAG context 可能长达数万 token，DeepSeek API 的上下文窗口有限（32K/128K），且 token 费用按量计费。直接塞给 LLM 既浪费又昂贵。

关键需求：不是简单截断，而是**语义压缩**——优先保留与当前查询最相关的内容。

---

## 技术选型：压缩策略对比

### 方案对比

| 维度 | 固定比率截断 | TF-IDF 重排 | **Query-Conditioned（最终）** |
|------|-------------|-------------|-------------------------------|
| **语义感知** | 无 | 词级 | 语义级（embedding）|
| **压缩质量** | 差 | 中 | 高 |
| **实现复杂度** | 极低 | 中 | 中高 |
| **token 估算** | 固定 | 固定 | tiktoken 精确 |
| **冷启动** | 无问题 | 无问题 | 需 embedding 模型 |
| **与 LLM 集成** | 简单 | 简单 | 需 pipeline |

**选择 Query-Conditioned 的原因**：
- 智能家居场景下，用户问"打开空调"时，偏好中的"喜欢24度"比历史记录中的"3天前关灯"重要得多
- 固定截断会丢失关键信息，TF-IDF 只看词频不看语义
- embedding 语义相似度能捕捉"空调"和"制冷"的关联

---

## 四层压缩：为什么用层级而不是单一策略？

```
PASS     (< 500 chars)     → 直接透传
SELECT   (500-2000 chars) → 语义重排，不截断
COMPRESS (2000-4000 chars) → 重排 + Token Budget 分配
SUMMARIZE (> 4000 chars)  → 重排 + 摘要替换
```

### 为什么不用固定阈值？

固定阈值忽视了两个关键变量：**总 context 长度**和**用户查询**。

同样的 1000 tokens：
- 查询"当前温度" → 需要 context 中的温度信息
- 查询"播放音乐" → context 中的温度信息完全无关

层级设计的核心洞察：**压缩强度应该随 context 规模增大而增强**。

### 各层详解

**Level 0（PASS）**：小于 500 字符的 context 不压缩直接透传，因为压缩本身有开销（embedding 计算），对小 context 不值得。

**Level 1（SELECT）**：500-2000 字符之间，只做语义重排，不丢弃任何 block。重排后按相关性从高到低排列，LLM 读取时自然优先处理靠前的内容。

**Level 2（COMPRESS）**：2000-4000 字符，触发 Token Budget 分配。按 embedding 分数排序后贪心塞入 budget，超出预算的块暂时保留。

**Level 3（SUMMARIZE）**：超长 context，被预算淘汰的块用 LLM 摘要压缩。"[摘要] 用户近期偏好24度，共执行过12次空调操作" 替代数十条历史记录。

---

## SoftSelector：语义评分的权重设计

```python
score = cosine_sim(query_emb, block_emb) * 0.7 + block.value_score * 0.3
```

### 为什么不只用语义相似度？

纯语义相似度的问题：**高相关但不重要的内容会排挤重要内容**。

例如：
- query = "打开空调" → 历史记录"用户3月5日打开了空调" 相似度 0.95
- 但 `value_score = 0.3`（历史记录重要性低）

纯语义分：0.95 × 1.0 = 0.95（排第一）
组合分：0.95 × 0.7 + 0.3 × 0.3 = 0.665 + 0.09 = 0.755

如果 context 中的用户偏好 block（`value_score = 0.9`）语义相似度 0.7：
组合分：0.7 × 0.7 + 0.9 × 0.3 = 0.49 + 0.27 = 0.76（超过历史记录）

**为什么用 0.7/0.3？** 语义相似度是动态的（取决于 query），知识价值分是静态的（由 block 来源决定）。7:3 权重确保语义相似度占主导，同时知识价值分起到微调作用。

---

## Token 估算：tiktoken vs 经验公式

### 对比

| 方案 | 中文估算 | 英文估算 | 准确性 |
|------|----------|----------|--------|
| 经验公式（chars/1.5, chars/4） | 1.5 字符/token | 4 字符/token | 中等 |
| **tiktoken（最终选择）** | ~1.5 字符/token | ~4 字符/token | **高** |
| naive len(text) | 1 字符/token | 1 字符/token | 低估英文 |

**选择 tiktoken 的原因**：
- tiktoken 是 OpenAI 官方 token 计数库，与计费标准一致
- `cl100k_base` 编码覆盖中文和英文
- 有 fallback 经验公式，tiktoken 不可用时仍可用

---

## Summarizer：为什么用 LLM 摘要而不是提取？

### 对比

| 方案 | 准确性 | 延迟 | 成本 |
|------|--------|------|------|
| 抽取式（取第一条） | 低 | 无 | 无 |
| LLM 摘要（最终选择） | 高 | 中（1次 API 调用） | 低 |
| 微调蒸馏模型 | 高 | 低 | 训练成本高 |

**选择 LLM 摘要的原因**：
- 智能家居 context 有特定模式，LLM 摘要能理解"用户偏好24度，3月至今共开过8次"这类信息压缩
- 只有超长 context 才触发摘要，开销可控
- 有抽取式 fallback（`tiktoken` 不可用时），保证可用性

---

## 与 LLM 决策链的集成

```
用户查询 "有点热"
  ↓
ContextPipeline.process()
  ├─ 从 SessionStore 收集 blocks
  ├─ 从 PreferenceStore 收集 blocks
  ├─ 从 RAG 收集 blocks
  └─ ContextCompressor.compress(query, blocks, max_tokens=2048)
      ├─ < 500 chars → PASS
      ├─ 500-2000 chars → SELECT（语义重排）
      ├─ 2000-4000 chars → COMPRESS（预算分配）
      └─ > 4000 chars → SUMMARIZE（LLM摘要）
  ↓
压缩后 context → LLMDecider.decide_cloud()
```

**为什么不在 `decide_local` 中也用？** 本地 mock 不涉及 token 费用，且本地推理路径没有 token 上限，压缩反而增加延迟。

---

## 面试追问

**Q: embedding 模型加载很慢，端侧怎么处理？**

当前 fallback：embedding 不可用时返回 `[0.0] * 384` 全零向量，导致 cosine 相似度恒为 0，分数完全由 `value_score * 0.3` 决定，退化为简单重要性评分。这对大多数查询仍然合理（偏好 > 历史 > session）。

**Q: 如果 query 本身很短（如"热"），embedding 效果如何？**

短 query 的 embedding 语义表示弱，可能与多个 block 都产生中等相似度。此时 `value_score` 的 30% 权重更关键，确保偏好等高价值 block 不会被无关历史淹没。

**Q: 摘要 prompt 被丢弃的 block 顺序是什么？**

按 `SoftSelector.score()` 的分数排序，即从高到低。先处理最相关的丢弃内容，确保摘要中保留最重要的信息。
