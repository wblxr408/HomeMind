# 用户信息存储、内存控制与个性化实现说明

## 1. 目的

本文档用于记录 HomeMind 当前关于以下能力的实现状态：

- 用户信息的本地存储方式
- 端侧设备场景下的内存开销控制方式
- 用户个性化能力的当前实现
- 当前尚未完全实现的个性化边界


## 2. 当前用户信息存储分层

HomeMind 当前不是把所有用户数据都放在一处，而是按用途拆成了三层：

### 2.1 短期会话记忆

文件：

- `core/memory/session_store.py`
- 持久化文件：`data/session_state.json`

主要存储内容：

- `current_scene`
- `last_user_input`
- `last_normalized_input`
- `last_action`
- `last_clarification`
- `last_route`
- `recent_turns`

作用：

- 维持当前会话上下文连续性
- 支持“再调亮”“继续”“刚刚那个”这类依赖上一轮语境的表达

当前控内存方式：

- `recent_turns` 有固定上限
- 默认仅保留最近 `8` 轮对话
- 新数据进入后，旧数据自动裁剪

结论：

- 这一层是轻量短期记忆，不承担长期历史存储职责


### 2.2 长期结构化偏好

文件：

- `core/memory/preference_store.py`
- 持久化文件：`data/preferences.json`

主要存储内容：

- 设备偏好
  - `preferred_temperature`
  - `preferred_brightness`
- 场景偏好
  - `accept_count`
  - `preferred_hour`
- 语言偏好
  - `dialect_terms`
- 推荐反馈统计

作用：

- 保存稳定结论，而不是保存原始对话流水
- 为后续动作排序提供个性化加权

当前控内存方式：

- 使用结构化 JSON 存储
- 只保留偏好结果，不保留大量原始交互文本

结论：

- 这是当前用户长期个性化的主存储层


### 2.3 长期经验记忆 / 本地知识库

文件：

- `core/rag/knowledge_base.py`

主要存储方式：

- 优先使用本地 ChromaDB：`data/chroma_db`
- 不可用时回退为内存 `memory_store`

主要存储内容：

- 高价值用户反馈
- 纠正记录
- 拒绝记录
- 聚合后的长期经验样本

作用：

- 为 BSR / LSR / RAG 提供历史经验参考
- 增强后续候选召回与偏好排序


## 3. 当前如何减小内存开销

HomeMind 当前的控内存思路，不是“全量保存所有历史”，而是“分层保存 + 限额保存 + 聚合保存”。

### 3.1 短期会话限长

实现位置：

- `core/memory/session_store.py`

机制：

- `recent_turns` 只保留最近 `max_recent_turns`
- 默认值为 `8`

收益：

- 保证上下文连续性
- 避免会话历史无限增长


### 3.2 长期知识库设硬上限

实现位置：

- `core/rag/knowledge_base.py`

机制：

- `KnowledgeBase(max_records=500)`
- 长期经验记录数量超过 `500` 后自动裁剪

收益：

- 控制端侧长期事件记忆的规模
- 防止内存和磁盘占用持续线性增长


### 3.3 重复事件按 key 聚合，不重复追加

实现位置：

- `core/rag/knowledge_base.py`
- `tools/kb_write.py`

机制：

- 使用 `memory_key` 标识一类事件
- 如果同类事件再次出现，则更新已有记录：
  - `count += 1`
  - 更新 `last_seen`
  - 更新 `value_score`
- 而不是继续 append 新条目

收益：

- 100 次相似行为，不会膨胀成 100 条原始长期记录
- 更适合端侧设备


### 3.4 低价值成功流水不写长期事件库

实现位置：

- `tools/kb_write.py`

机制：

- 普通成功反馈（如“接受”“忽略”）默认不写入长期事件记忆
- 当前主要写入长期记忆的是：
  - `拒绝`
  - `纠正`

收益：

- 避免把大量低价值成功执行变成长期垃圾数据
- 提高长期记忆密度


### 3.5 偏好与事件分离

当前策略：

- 稳定偏好放 `PreferenceStore`
- 高价值经验放 `KnowledgeBase`
- 会话态信息放 `SessionStore`

收益：

- 不同数据使用不同生命周期
- 让“偏好存储”和“事件存储”各自保持轻量


## 4. 当前用户个性化是如何实现的

### 4.1 设备参数偏好学习

实现位置：

- `core/memory/preference_store.py`

当前已实现：

- 用户多次接受空调结果后，记录偏好的空调温度
- 用户多次接受灯光结果后，记录偏好的灯光亮度

示例：

- 某家庭常把空调调到 `24°C`
- 系统会逐渐把 `24°C` 作为偏好参数


### 4.2 场景偏好学习

实现位置：

- `core/memory/preference_store.py`

当前已实现：

- 记录某个场景被接受的次数
- 记录该场景常被接受的时间段

示例：

- 某家庭晚上常接受睡眠模式
- 系统后续会在相近时段提高睡眠模式候选得分


### 4.3 语言习惯学习

实现位置：

- `core/memory/preference_store.py`
- `core/language/normalizer.py`

当前已实现：

- 把用户原始表达与归一化后的标准表达建立映射
- 支持口语、方言、家庭内部常用说法逐步被吸收

示例：

- 用户常说“热得慌”
- 系统会越来越稳定地归一化到“太热了”


### 4.4 历史偏好影响动作排序

实现位置：

- `core/lsr/precision_ranking.py`
- `core/rag/knowledge_base.py`

机制：

- LSR 的特征中包含用户偏好分数
- `get_user_preference_score()` 会综合：
  - 结构化偏好
  - 历史经验记录
  - 接受过的反馈

结果：

- 相同输入下，不同家庭可能会得到不同的候选排序


