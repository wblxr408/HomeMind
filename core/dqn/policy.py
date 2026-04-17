"""
DQN 策略网络（强化学习主动推荐）
5维状态输入 → 32隐层 → 6维输出（对应6个场景动作）
参数量 < 1000，模型文件 < 5MB
"""

import numpy as np
from typing import Tuple, Optional, List, Dict
import logging
import pickle
import os

logger = logging.getLogger(__name__)

SCENES = {
    0: "睡眠模式",
    1: "待客模式",
    2: "离家模式",
    3: "观影模式",
    4: "起床模式",
    5: "无推荐",
}

REWARD_MAP = {
    "接受": 1.0,
    "忽略": 0.0,
    "拒绝": -0.5,
    "纠正": -1.0,
}


class QNetwork:
    """极轻量 Q 网络：5 → 32 → 6"""

    def __init__(self, seed: int = 42):
        rng = np.random.default_rng(seed)
        scale = 0.1
        self.W1 = rng.standard_normal((5, 32)) * scale
        self.b1 = np.zeros(32, dtype=np.float32)
        self.W2 = rng.standard_normal((32, 6)) * scale
        self.b2 = np.zeros(6, dtype=np.float32)

    def forward(self, x: np.ndarray) -> np.ndarray:
        h = np.tanh(x @ self.W1 + self.b1)
        q = h @ self.W2 + self.b2
        return q

    def parameters(self) -> List[np.ndarray]:
        return [self.W1, self.b1, self.W2, self.b2]

    def num_params(self) -> int:
        return sum(p.size for p in self.parameters())

    def load_state_dict(self, state: Dict[str, np.ndarray]):
        self.W1 = state["W1"]
        self.b1 = state["b1"]
        self.W2 = state["W2"]
        self.b2 = state["b2"]

    def state_dict(self) -> Dict[str, np.ndarray]:
        return {"W1": self.W1, "b1": self.b1, "W2": self.W2, "b2": self.b2}

    def copy_from(self, other: "QNetwork"):
        self.W1 = other.W1.copy()
        self.b1 = other.b1.copy()
        self.W2 = other.W2.copy()
        self.b2 = other.b2.copy()


class ReplayBuffer:
    """经验回放池，容量1000条，滚动覆盖"""

    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.buffer: List[Dict] = []
        self.position = 0

    def push(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray):
        if len(self.buffer) < self.capacity:
            self.buffer.append(None)
        self.buffer[self.position] = {
            "state": state, "action": action, "reward": reward, "next_state": next_state
        }
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int) -> List[Dict]:
        rng = np.random.default_rng()
        indices = rng.choice(len(self.buffer), min(batch_size, len(self.buffer)), replace=False)
        return [self.buffer[i] for i in indices]

    def __len__(self):
        return len(self.buffer)


class DQNPolicy:
    """
    DQN 策略网络
    离线预训练 + 端侧增量更新
    ε-greedy 探索策略（逐步衰减）
    """

    def __init__(self, model_dir: str = "models", seed: int = 42):
        self.q_net = QNetwork(seed)
        self.target_net = QNetwork(seed)
        self._sync_target()
        self.replay = ReplayBuffer()
        self.epsilon = 0.3
        self.epsilon_min = 0.05
        self.gamma = 0.95
        self.lr = 0.01
        self.update_counter = 0
        self.update_freq = 50
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)
        self._load_if_exists()
        self._pretrain_if_needed()
        logger.info(f"DQNPolicy 初始化，参数量: {self.q_net.num_params()}")

    def _pretrain_if_needed(self):
        """若回放池为空，用合成数据做离线预训练，覆盖基础场景规律"""
        if len(self.replay) > 0:
            return

        rng = np.random.default_rng(42)
        synthetic_data = [
            {"hour": 22, "members": 2, "temp": 25, "last_scene": 3, "day": 4, "action": 0},
            {"hour": 23, "members": 2, "temp": 24, "last_scene": 0, "day": 4, "action": 0},
            {"hour": 7,  "members": 2, "temp": 22, "last_scene": 0, "day": 5, "action": 4},
            {"hour": 8,  "members": 2, "temp": 23, "last_scene": 4, "day": 5, "action": 1},
            {"hour": 9,  "members": 0, "temp": 26, "last_scene": 1, "day": 1, "action": 2},
            {"hour": 20, "members": 2, "temp": 27, "last_scene": 1, "day": 6, "action": 3},
            {"hour": 21, "members": 3, "temp": 26, "last_scene": 2, "day": 6, "action": 1},
            {"hour": 18, "members": 1, "temp": 28, "last_scene": 0, "day": 3, "action": 3},
        ]

        for sample in synthetic_data:
            state = self._manual_state_vector(
                sample["hour"], sample["members"],
                sample["temp"], sample["last_scene"], sample["day"]
            )
            self.replay.push(state, sample["action"], 0.8, state)

        logger.info("DQN 离线预训练数据注入完成")

    def _manual_state_vector(self, hour, members, temp, last_scene, day) -> np.ndarray:
        return np.array([
            hour / 23.0,
            (temp - 15.0) / 20.0,
            members / 5.0,
            (last_scene + 1) / 5.0,
            day / 6.0,
        ], dtype=np.float32)

    def _state_to_vector(self, context) -> np.ndarray:
        """将环境上下文转为5维状态向量"""
        return np.array([
            context.hour / 23.0,
            (context.temperature - 15.0) / 20.0,
            context.members_home / 5.0,
            (context.last_scene + 1) / 5.0,
            context.day_of_week / 6.0,
        ], dtype=np.float32)

    def _sync_target(self):
        """同步 TargetNet（定期从 QNet 复制）"""
        self.target_net.copy_from(self.q_net)

    def recommend(self, context) -> Tuple[int, float]:
        """
        DQN 推荐：给定当前环境状态，输出推荐场景编号和置信度
        ε-greedy 探索
        """
        state = self._state_to_vector(context)
        q_values = self.q_net.forward(state)
        q_max = float(np.max(q_values))
        q_sum = float(np.sum(np.abs(q_values)))
        confidence = q_max / (q_sum + 1e-6)

        if np.random.rand() < self.epsilon:
            action = int(np.random.randint(0, 6))
        else:
            action = int(np.argmax(q_values))

        return action, confidence

    def record_feedback(self, context, action: int, user_response: str) -> bool:
        """记录用户对推荐场景的反馈，写入经验回放池"""
        reward = REWARD_MAP.get(user_response, 0.0)
        state = self._state_to_vector(context)
        next_state = state.copy()
        self.replay.push(state, action, reward, next_state)

        self.update_counter += 1
        if self.update_counter % self.update_freq == 0 and len(self.replay) >= 10:
            self._light_update()

        logger.info(f"DQN 反馈记录: action={action}, reward={reward}, buffer_size={len(self.replay)}")
        return True

    def _light_update(self):
        """增量更新策略网络（正确梯度下降法）"""
        batch = self.replay.sample(16)
        for exp in batch:
            s = exp["state"].astype(np.float32)
            a = int(exp["action"])
            r = float(exp["reward"])
            s_next = exp["next_state"].astype(np.float32)

            q_next_max = float(np.max(self.target_net.forward(s_next)))
            q_target = r + self.gamma * q_next_max

            h = np.tanh(s @ self.q_net.W1 + self.q_net.b1)
            q_current = self.q_net.forward(s)
            delta = q_target - q_current[a]

            grad_h = delta * self.q_net.W2[a]
            grad_tanh = grad_h * (1.0 - h ** 2)
            grad_W2_col = h * delta
            grad_W2 = np.zeros_like(self.q_net.W2)
            grad_W2[:, a] = grad_W2_col
            grad_b2 = np.zeros_like(self.q_net.b2)
            grad_b2[a] = delta
            grad_W1 = np.outer(s, grad_tanh)
            grad_b1 = grad_tanh

            self.q_net.W2 += self.lr * grad_W2
            self.q_net.b2 += self.lr * grad_b2
            self.q_net.W1 += self.lr * grad_W1
            self.q_net.b1 += self.lr * grad_b1

        self.epsilon = max(self.epsilon_min, self.epsilon * 0.99)

        if self.update_counter % (self.update_freq * 5) == 0:
            self._sync_target()
            logger.info("TargetNet 已同步")

    def save(self, path: str = ""):
        if not path:
            path = os.path.join(self.model_dir, "dqn_policy.pkl")
        with open(path, "wb") as f:
            pickle.dump({
                "q_net": self.q_net.state_dict(),
                "epsilon": self.epsilon,
            }, f)
        logger.info(f"DQN 策略已保存: {path}")

    def _load_if_exists(self):
        path = os.path.join(self.model_dir, "dqn_policy.pkl")
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    data = pickle.load(f)
                self.q_net.load_state_dict(data["q_net"])
                self.epsilon = data.get("epsilon", self.epsilon)
                self._sync_target()
                logger.info("DQN 策略从文件加载")
            except Exception as e:
                logger.warning(f"DQN 加载失败: {e}")
