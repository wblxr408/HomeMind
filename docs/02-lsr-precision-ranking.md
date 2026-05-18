# LSR 精排 — 面试拷打指南

## 模块定位

LSR（Lightweight Stage Ranking）是 HomeMind 五层架构的**第二层**，负责对 BSR 召回的候选动作进行加权评分排序，并注入上下文特征（温度/湿度/时间/用户偏好）。

```
用户输入 → BSR 候选召回 (top_k=5)
                     ↓
         LSR 精排 (加权特征 → final_score)
                     ↓
           LLM 决策 (最终命令)
```

---

## 核心接口

### `LSRecify.__init__()`
固定权重初始化：
```python
self.weights = np.array([0.30, 0.10, 0.05, 0.20, 0.35], dtype=np.float32)
#       f1(BSR分数)  f2(温度)  f3(湿度)  f4(时间)  f5(偏好分数)
self.bias = 0.1
```

### `LSRecify.rank(query, candidates, context, kb, session_store) -> List[Dict]`
输出每个候选追加 `final_score` 并按降序排列。

---

## 五大评分特征

### f1 — BSR 原始召回分数
```python
f1 = float(candidate.get("score", 0.5))
```
来自 BSR 三路召回的原始分，权重 **0.30**。

### f2 — 温度归一化
```python
f2 = (context.temperature - 15.0) / 20.0
```
- 温度范围 [15°C, 35°C] → 归一化到 [0, 1]
- 温度越高 → f2 越大 → 空调制冷相关候选分数↑
- 权重 **0.10**

### f3 — 湿度归一化
```python
f3 = (context.humidity - 30.0) / 50.0
```
- 湿度范围 [30%, 80%] → 归一化到 [0, 1]
- 权重 **0.10**

### f4 — 时间特征（正弦编码）
```python
hour_sin = np.sin(2 * np.pi * context.hour / 24.0)
f4 = (hour_sin + 1.0) / 2.0
```
- 24h 正弦编码，使相邻小时特征相似
- 22:00 → 睡眠模式候选↑，8:00 → 早安模式候选↑
- 权重 **0.20**

### f5 — 用户偏好分数
```python
f5 = kb.get_user_preference_score(candidate.get("action", ""), context) if kb else 0.5
```
- 来自知识库的用户习惯和反馈记录
- 权重 **0.35**（最高权重！说明个性化最重要）

### 最终分数
```python
score = f1*0.30 + f2*0.10 + f3*0.05 + f4*0.20 + f5*0.35 + 0.1
score = clip(score, 0.0, 1.0)
```

---

## 显式上下文注入

LSR 在评分前会注入两类**显式动作**，绕过分数计算直接干预排序：

### 1. 显式场景目标
检测查询中的场景关键词：
```python
_explicit_scene_target("切换到睡眠模式") → "切换睡眠模式"
_explicit_scene_target("我要睡觉") → "切换睡眠模式"
```
注入后：匹配动作 **+0.35**，其他场景切换动作 **-0.35**

### 2. 显式设备目标
```python
_explicit_device_target("关闭音响") → "关闭音响"
```
注入后：匹配动作 **+0.45**，同设备其他动作 **-0.35**（惩罚分心）

---

## 多轮上下文注入

LSR 的核心能力：**根据上一轮会话决定本轮隐式意图**。

```python
def _resolve_air_conditioner_followup(query, last_action):
    if "热" in query and last_action["device_action"] in {"on", "adjust"}:
        return "调低空调温度"  # 继续调低
    if "冷" in query and last_action["device_action"] in {"on", "adjust"}:
        return "调高空调温度"  # 反向调节
```

支持设备：空调、灯光、电视/音响、窗户、风扇。

**"暗" → 当前灯光开 → 调亮灯光**  
**"暗" → 当前灯光关 → 打开灯光**

---

## 面试核心问题清单

### 1. 为什么权重这样分配？
- `f5(偏好)=0.35` 最高 → 个性化最重要
- `f1(BSR)=0.30` 次高 → 原始召回质量
- `f4(时间)=0.20` → 时间上下文强相关
- `f2(温度)=0.10`、`f3(湿度)=0.05` → 环境参数辅助

### 2. 权重如何调优？
- 当前为经验值，可通过 RL 或人工反馈迭代
- `update_weights()` 支持增量更新：`weights += delta * 0.01`

### 3. 温度和湿度为什么用线性归一化？
- 简单高效，适合小模型场景
- 空调最常用温度范围是 16-30°C，归一化区间 [15, 35] 覆盖合理

### 4. 时间用正弦编码而非 one-hot？
- one-hot(24h) 太稀疏，维度爆炸
- 正弦编码使相邻时刻特征平滑（23:00 和 0:00 相近）
- 同理可用余弦：`cos(2π*hour/24)`

### 5. 显式注入为什么用固定加/减分（+0.35/+0.45）？
- 绕过模型计算，强制意图优先
- 0.45 > 0.35 说明设备意图比场景意图更强
- 可以直接覆盖任何分数的候选

### 6. 权重总和是多少？
```python
0.30 + 0.10 + 0.05 + 0.20 + 0.35 = 1.00  # 正好为 1
bias = 0.1
```
权重和为 1 是好的设计习惯，避免 scale 漂移。

### 7. 如果用户说"暗"，但上一轮是"打开电视"而非调灯光？
```python
# _last_device_from_session 会从 session_store 获取 last_normalized_input
# 如果 last_normalized_input 不含灯光相关词 → 不注入灯光候选
# _resolve_media_followup 会检测到"电视" → 处理"太吵"等媒体相关表达
```

### 8. 如何防止恶意用户操控分数？
- 偏好分 f5 来自长期历史累计，短期内无法刷分
- 每个动作的偏好分有上限 `min(1.0, ...)`
