# BSR 候选召回 — 面试拷打指南

## 模块定位

BSR（Broad Stage Recall）是 HomeMind 五层架构的**第一层**，负责从规则库、向量库和用户历史三个来源快速召回候选动作，为后续精排提供候选集。

```
用户输入
  ↓
BSR 候选召回 (rule + vector + history)
  ↓
LSR 精排 (加权评分 + 上下文注入)
  ↓
LLM 决策 (最终命令)
  ↓
执行层 (校验 + 执行)
```

---

## 核心接口

### `BSRecall.__init__(kb, top_k=5)`
- `kb`: 知识库实例（用于历史召回）
- `top_k`: 最大返回候选数量

### `BSRecall.recall(query, context) -> List[Dict]`
- 输入用户查询和上下文
- 合并三个召回来源，去重后取 top_k
- **无候选时返回兜底项**: `{"action": "无法理解", "source": "fallback", "score": 0.0}`

---

## 三路召回详解

### 1. 规则召回 (`_rule_recall`)

**原理**: 精确关键词匹配，无模型开销。

```python
self.rule_map = {
    "热": ["打开空调", "打开风扇"],
    "空调": ["打开空调", "关闭空调"],
    ...
}
```

- 匹配到关键词 → 对应 action 列表
- 固定 `score = 0.9`（高置信度规则优先）

**特点**:
- O(n*m) 线性扫描，`n`=关键词数，`m`=匹配字符数
- 无向量计算，纯 CPU
- 覆盖高频、明确指令

**面试追问**:
- Q: 规则优先级怎么设计？
- A: 当前所有规则同等优先级，多个规则匹配到的候选都会被加入去重队列

### 2. 向量召回 (`_vector_recall`)

**原理**: 用户查询 embedding 与候选动作池 embedding 做余弦相似度计算。

```python
action_embs = encode(ACTION_POOL)  # 预计算一次
query_emb = encode(query)
sims = np.dot(action_embs, query_emb)
top_indices = np.argsort(sims)[-4:][::-1]  # Top-4
```

- 相似度 > 0.25 才纳入候选
- 分数 = `clip(sims[idx], 0, 1)`（归一化余弦相似度）

**降级策略**:
```python
model = get_model()
if model is None:
    return []  # 向量模型不可用 → 向量路返回空
```

**关键细节**:
- ACTION_POOL embeddings **全局缓存** (`_ACTION_EMBS`)，避免重复计算
- 使用 `np.dot` 而非 `cosine_similarity`，因为都是单位向量时等价但更快

**面试追问**:
- Q: 为什么选 0.25 作为阈值？
- A: 这是实验确定的召回阈值，偏高会漏召回，偏低会引入噪声。0.25 在语义相似但不完全匹配的场景（如"有点闷"→"打开空调"）有较好召回率
- Q: ACTION_POOL 是什么？有哪些动作？
- A: 预定义候选动作池，包含设备控制（开关空调/灯光/电视/风扇/窗户/音响）和场景切换（睡眠/待客/离家/观影/起床/回家模式等）

### 3. 历史召回 (`_history_recall`)

**原理**: 查询知识库中 `category="用户习惯"` 的记录，提取曾被用户接受的动作。

```python
history_records = self.kb.query(query, top_k=3, category="用户习惯")
for record in history_records:
    accepted = record.get("accepted", False)
    score = 0.95 if accepted else 0.60
```

- 用户接受过的习惯 → 0.95 高分
- 用户拒绝过的习惯 → 0.60 低分

**面试追问**:
- Q: 为什么不直接复用历史决策？
- A: 需要去重合并 + 精排打分，历史只是候选之一
- Q: `accepted` 字段从哪来？
- A: 来自知识库的 `用户反馈` 记录，每次用户交互后更新

---

## 去重合并逻辑

```python
seen = set()
for route_cands in [rule, vector, history]:
    for candidate in route_cands:
        action = candidate["action"]
        if action not in seen:
            seen.add(action)
            candidates.append(candidate)
return candidates[:self.top_k]
```

- **按加入顺序保留**：rule → vector → history 的优先级
- 先匹配到的源决定最终 `source` 字段

---

## 与 LSR 的交互

BSR 输出格式（示例）:
```python
{
    "action": "打开空调",
    "source": "rule",      # rule | vector | history | fallback
    "keyword": "热",        # rule 时有
    "score": 0.9           # 原始召回分数
}
```

LSR 输入后计算 `final_score`，BSR 的 `score` 作为特征 f1 参与加权：
```
final_score = f1 * 0.30 + f2 * 0.10 + f3 * 0.05 + f4 * 0.20 + f5 * 0.35 + bias
```

---

## 边界与异常

| 场景 | 处理 |
|---|---|
| 向量模型加载失败 | 向量路返回空，规则和历史仍工作 |
| 历史记录为空 | 该路返回空列表 |
| 三路都无候选 | 返回兜底项 `{"action": "无法理解", "score": 0.0}` |
| 候选超 top_k | 截断 |

---

## 面试核心问题清单

1. **BSR 三路召回的设计动机是什么？**
   - 规则：高频明确指令，零延迟，高精度
   - 向量：语义泛化，覆盖"有点闷"等模糊表达
   - 历史：个性化，适应用户习惯

2. **三路优先级怎么确定？**
   - rule > vector > history（先加入先保留）
   - 因为规则最精确，历史最个性化但可能过时

3. **为什么需要 top_k 限制？**
   - 控制计算量，LSR 的加权评分对 top_k=5 有意义（信息密度）
   - 去重后 5 个候选足够精排决策

4. **和 LSR 的边界在哪？**
   - BSR 只负责"找候选"，不做排序
   - LSR 负责"给候选打分"，注入上下文特征

5. **如何扩展新设备/动作？**
   - 规则：在 `rule_map` 加关键词映射
   - 向量：在 `ACTION_POOL` 加新动作描述

6. **向量召回的 cosine threshold 为什么是 0.25？**
   - 低于 0.25 意味着语义相关性太弱
   - 在智能家居短命令场景，0.25 能召回"闷"→"空调"等隐含意图
