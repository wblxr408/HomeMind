# DQN Policy 主动策略 — 面试拷打指南

## 模块定位

DQN Policy 是 HomeMind 五层架构中的**主动学习层**，负责在用户不主动发指令时，根据当前环境上下文**主动推荐场景**。

```
定时触发 (scheduler)
        ↓
DQN.recommend(context) → (scene_idx, confidence)
        ↓
  高置信度(>0.8) → 直接执行
  低置信度 → 询问用户
        ↓
用户反馈(接受/拒绝/忽略)
        ↓
DQN.record_feedback() → 更新 replay buffer
        ↓
定期增量学习 (每 N 次交互或每日)
```

---

## 核心接口

### `DQNPolicy.__init__(model_dir, seed)`
初始化 Q网络、目标网络、replay buffer、epsilon 探索率。

### `DQNPolicy.recommend(context) -> (int, float)`
- 返回 `(scene_idx, confidence)` — 推荐的场景索引和置信度
- scene_idx=5 表示"无推荐"

### `DQNPolicy.record_feedback(context, action, user_response) -> bool`
- 用户对推荐响应后调用
- 记录到 replay buffer，触发增量学习

---

## 状态空间

**7 维状态向量**:
```python
state = [
    hour / 23.0,                          # 0: 时间
    (temperature - 15.0) / 20.0,        # 1: 温度
    members_home / 5.0,                    # 2: 在家人数
    (last_scene + 1) / 8.0,              # 3: 上一个场景
    day_of_week / 6.0,                   # 4: 星期几
    humidity / 100.0,                      # 5: 湿度
    device_state_score,                     # 6: 设备开启比例
]
```

**归一化原因**:
- 神经网络对输入 scale 敏感
- 所有特征归一化到 [0, 1]，收敛更快

---

## 动作空间

**9 个离散动作**:
| idx | 场景 |
|-----|------|
| 0 | 睡眠模式 |
| 1 | 待客模式 |
| 2 | 离家模式 |
| 3 | 观影模式 |
| 4 | 起床模式 |
| 5 | **无推荐** |
| 6 | 工作模式 |
| 7 | 早安模式 |
| 8 | 晚归模式 |

---

## 网络结构

```python
class QNetwork:
    # 输入: state_dim=7
    # 隐藏层: 64 units, Tanh 激活
    # 输出: action_dim=9 (Q值)
    net = [
        Linear(7, 64),
        Tanh(),
        Linear(64, 64),
        Tanh(),
        Linear(64, 9),
    ]
```

### Double DQN 更新

```python
# 计算当前 Q
current_q = q_net(states).gather(1, actions.unsqueeze(1))

# 计算目标 Q（用 online net 选择动作，target net 评估）
with torch.no_grad():
    next_actions = q_net(next_states).argmax(1)     # online 选择
    next_q = target_net(next_states).gather(1, next_actions.unsqueeze(1))  # target 评估
    target_q = rewards + gamma * next_q

# 梯度更新
loss = smooth_l1_loss(current_q, target_q)
loss.backward()
```

**为什么用 Double DQN？**
- 解决 Q 值过估计问题
- 在线网络选动作，目标网络评估，避免自举偏差

---

## 探索策略 (Epsilon-Greedy)

```python
if random() < epsilon:
    action = random.randint(0, action_dim)  # 探索
else:
    action = argmax(q_values)  # 利用
```

- epsilon 初始值: **0.30**（较高探索率）
- 每次更新: `epsilon *= 0.99`
- 下限: **0.05**

---

## 冷启动策略

```python
# 无 replay 数据时注入合成数据
synthetic_data = [
    {"hour": 22, "action": 0},  # 22:00 睡眠
    {"hour": 7, "action": 7},   # 7:00 早安
    {"hour": 9, "action": 2},   # 9:00 离家
    ...
]
```

**为什么要合成数据？**
- 新系统无用户交互数据
- 保证上线第一天就有基本策略
- 覆盖常见场景

---

## Replay Buffer

```python
class ReplayBuffer:
    capacity = 1000
    # 循环缓冲，覆盖旧数据
    def push(state, action, reward, next_state):
        buffer[position] = data
        position = (position + 1) % capacity
```

- **为什么用固定容量？** 防止内存无限增长
- **为什么循环覆盖？** 保留最新交互，最新交互最能反映当前偏好

---

## NumPy Fallback

当 PyTorch 不可用时（边缘设备），用 NumPy 实现简化版 Q 更新：

```python
# 仅更新 action 对应列的 W3 和 b3
delta = target_q - current_q
W3[:, action] += lr * delta * h2   # 单层梯度
b3[action] += lr * delta
```

**局限性**: NumPy fallback 是单层更新，无法反向传播多层，无法收敛到 Double DQN 的效果。

---

## 面试核心问题清单

### 1. DQN 和 Q-Learning 的区别？
- Q-Learning 是表格方法，DQN 用神经网络近似 Q 函数
- DQN 能处理高维连续状态空间（本例 7 维）

### 2. 为什么选 Double DQN 而非普通 DQN？
- 普通 DQN 用目标网络选动作，存在过估计
- Double DQN 用在线网络选、目标网络评，分离选择和评估
- 本场景动作空间只有 9 个，过估计问题不严重，但保留 Double DQN 是好习惯

### 3. Epsilon 为什么要衰减？
- 初期高探索（0.30）收集多样交互
- 后期低探索（0.05）利用已学知识
- `* 0.99` 每次递减 1%，约 110 次后接近下限

### 4. Replay Buffer 为什么随机采样？
- 打破时序相关性
- 使样本独立同分布（i.i.d.）
- 均匀采样可能导致某些状态被低估

### 5. 为什么场景索引映射是离散的？
```python
"睡眠模式": 0, "待客模式": 1, "离家模式": 2, ...
```
- 智能家居场景天然离散，不需要连续动作
- 离散动作空间适合 DQN 而非 Policy Gradient

### 6. 温度/时间归一化公式的直觉？
- `hour / 23.0`: 小时归一化到 [0, 1]，23:00≈1.0
- `(temp - 15) / 20`: 以 15°C 为基准，35°C 归一化为 1.0
- 覆盖智能家居常见温度范围

### 7. NumPy fallback 的精度损失有多大？
- 只更新最后一层，前面的层保持不变
- 约等于在线单步更新，无法学习深层特征
- 适合演示和边缘设备，生产环境建议用 PyTorch

### 8. 无推荐（action=5）的意义？
- 当 Q 值最大置信度低时返回 5
- 避免在不确定时强制推荐
- 防止打扰用户

### 9. 如何判断推荐时机？
```python
if confidence > 0.8:
    execute_directly()  # 高置信直接执行
else:
    ask_user()  # 低置信询问用户
```
- 置信度用 `q_max / q_sum` 表示 Q 值的"纯度"
- q_sum 很小说明所有 Q 值接近，难以区分最优
