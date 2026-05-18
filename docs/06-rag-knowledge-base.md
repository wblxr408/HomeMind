# RAG Knowledge Base — 面试拷打指南

## 模块定位

Knowledge Base 是 HomeMind 的**记忆与知识层**，以 RAG（检索增强生成）方式为 LLM 决策提供上下文知识，同时作为 BSR 历史召回和 LSR 偏好评分的数据来源。

```
用户交互
  ↓
KnowledgeBase.update_feedback() — 记录反馈
  ↓
KnowledgeBase.query() — RAG 检索
  ↓
LSR (偏好分数) + LLM (上下文知识) + BSR (历史召回)
```

---

## 核心接口

### `add(content, category, accepted, **metadata)`
向 memory_store 添加记录。

### `query(text, top_k, category, source_buckets, min_trust_score)`
向量检索 + 规则检索，返回 top_k 条相关知识。

### `get_context_prompt(user_query, context)`
构建 RAG 提示字符串，供 LLM 决策使用。

### `get_user_preference_score(candidate_action, context)`
查询用户对某个动作的偏好分数，供 LSR 使用。

---

## 数据存储分层

```
KnowledgeBase
├── preset_knowledge     # 出厂预设知识 (10条)
├── memory_store         # 用户交互累积知识 (最大500条)
├── time_series_store    # 时序摘要 (最大120条)
└── ChromaDB (可选)      # 向量数据库（未安装时用内存）
```

### Preset Knowledge（10条）
```
preset_01: 室内温度超过28°C时，打开空调降温效果最好
preset_02: 湿度超过70%时...
preset_03: 晚上22:00后大多数家庭成员进入睡眠...
...
preset_10: "有点闷"在温度28°C以上时，优先推荐开空调降温
```

### Trust 等级
```python
DEFAULT_TRUST = {
    "设备说明书": 0.98,   # 最高
    "纠正记录": 0.85,
    "时序摘要": 0.88,
    "场景规则": 0.90,
    "用户习惯": 0.72,
    "用户反馈": 0.68,
    "健康建议": 0.60,    # 最低
}
```

---

## 记录归一化

每条记录归一化后包含：
```python
{
    "content": "...",
    "category": "用户习惯",
    "accepted": True,
    "timestamp": "...",
    "first_seen": "...",
    "last_seen": "...",
    "count": 1,
    "value_score": 1.0,
    "record_id": "...",
    "source_bucket": "preferences",  # 按 category 分类
    "trust_score": 0.72,
    "trust_level": "low",             # high | medium | low
    "conflict": False,
    "conflict_reason": "",
}
```

---

## 向量检索（规则降级）

```python
def _search_pool(text, pool, top_k):
    # 向量模型可用时
    if model and pool:
        doc_embs = encode(pool)
        sims = cosine_similarity(query_emb, doc_embs)
        top_k = [pool[i] for i in argsort(sims)[-top_k:][::-1] if sims[i] > 0.1]
        return top_k

    # 向量模型不可用 → 规则检索（fallback）
    ascii_terms = [t for t in re.findall(r"[a-z0-9_]+", lower) if len(t) >= 2]
    cjk_chars = [c for c in lower if is_cjk(c)]
    for item in pool:
        score = (ASCII匹配数 * 4) + (CJK字符匹配数 * 1) + trust_score * 10
        if item["conflict"]: score -= 3
    # 按分数降序
```

**规则检索分数** = 精确字符匹配 + 信任度加成 - 冲突惩罚

---

## 冲突检测

```python
def _apply_conflict_detection(record):
    # 同一 fact_key 不同值 → 冲突
    for existing in memory_store:
        if same fact_key and different fact_value:
            existing["conflict"] = True
            record["conflict"] = True
```

**冲突记录在 RAG 检索时默认排除**：
```python
query(..., include_conflicted=False)
```

---

## 偏好分数计算

```python
def get_user_preference_score(action, context):
    score = 0.5

    # 1. PreferenceStore 中的设备偏好
    if ac_temp preference exists:
        score += 0.2

    # 2. KB 中用户习惯（接受次数）
    history = query(action, category="用户习惯")
    if history:
        accepted_count = sum(1 for h in history if h.accepted)
        score = min(1.0, 0.5 + accepted_count * 0.2)

    # 3. KB 中用户反馈
    feedback = query(action, category="用户反馈")
    if feedback:
        accepted = sum(1 for f in feedback if f.feedback == "接受")
        score = max(score, min(1.0, 0.5 + accepted * 0.15))

    return score
```

---

## RAG Prompt 构建

```python
def get_context_prompt(user_query, context):
    retrieved = query(user_query, top_k=3, min_trust_score=0.6, include_conflicted=False)
    # 注入实时上下文
    live_context = {
        "content": f"当前环境：时间={hour} 温度={temp} 湿度={hum} 场景={scene}",
        "trust_score": 0.92,  # live_context 最高信任度
    }
    chunks = [live_context] + retrieved
    # 语义压缩
    compressed = SemanticCompressor.compress(chunks, max_chars=2000)
    return to_context_string(compressed)
```

---

## 面试核心问题清单

### 1. 为什么信任度这样分配？
- 设备说明书（0.98）：厂家文档，最权威
- 场景规则（0.90）：预设场景逻辑
- 用户习惯（0.72）：累计行为，有一定个性化
- 用户反馈（0.68）：单次反馈，权重低

### 2. 冲突检测如何工作？
- 基于 `fact_key` 字段
- 同一 fact_key 不同值 → 双方标记 conflict=True
- 检索默认排除冲突记录，避免矛盾知识影响决策

### 3. ChromaDB 不可用时的降级策略？
- 用内存 `memory_store` 替代
- 向量检索降级为规则检索（字符匹配）
- ChromaDB 仍可存储（离线后仍可用），只是不用向量检索

### 4. 为什么要语义压缩？
- LLM 上下文有限
- max_tokens = 500，压缩到约 2000 字符
- SemanticCompressor 按 trust_level 优先级保留

### 5. `count()` 为什么返回 `memory_store + preset_knowledge`？
```python
def count():
    return len(self.memory_store) + len(self.preset_knowledge)
```
- preset_knowledge 固定 10 条
- memory_store 动态增长（最大 500 条）
- 测试期望初始值 = 10

### 6. 如何防止记忆污染？
- 信任度分级，低信任度记录影响小
- 冲突检测自动标记矛盾记录
- 定期 prune，删除低价值记录
