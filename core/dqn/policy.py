"""
DQN policy network for proactive scene recommendation.

The model uses a small PyTorch Double DQN: 7 state features -> 64 hidden units
-> 9 scene actions. It stays lightweight enough for on-device incremental
updates while avoiding the fragile hand-written gradient path.
"""

import logging
import os
from typing import Dict, List, Tuple

import numpy as np
try:
    import torch
    import torch.nn as nn
    import torch.optim as optim
    TORCH_AVAILABLE = True
except Exception as exc:  # pragma: no cover - depends on local torch runtime
    torch = None
    nn = None
    optim = None
    TORCH_AVAILABLE = False
    TORCH_IMPORT_ERROR = exc

from core.security import get_encrypted_storage

logger = logging.getLogger(__name__)

STATE_DIM = 7
ACTION_DIM = 9

SCENES = {
    0: "睡眠模式",
    1: "待客模式",
    2: "离家模式",
    3: "观影模式",
    4: "起床模式",
    5: "无推荐",
    6: "工作模式",
    7: "早安模式",
    8: "晚归模式",
}

REWARD_MAP = {
    "接受": 1.0,
    "忽略": 0.0,
    "拒绝": -0.5,
    "纠正": -1.0,
}


_BaseModule = nn.Module if TORCH_AVAILABLE else object


class QNetwork(_BaseModule):
    """Tiny PyTorch Q network."""

    def __init__(
        self,
        seed: int = 42,
        state_dim: int = STATE_DIM,
        action_dim: int = ACTION_DIM,
        hidden_dim: int = 64,
    ):
        if TORCH_AVAILABLE:
            super().__init__()
            torch.manual_seed(seed)
        self.state_dim = state_dim
        self.action_dim = action_dim
        self.hidden_dim = hidden_dim
        if TORCH_AVAILABLE:
            self.net = nn.Sequential(
                nn.Linear(state_dim, hidden_dim),
                nn.Tanh(),
                nn.Linear(hidden_dim, hidden_dim),
                nn.Tanh(),
                nn.Linear(hidden_dim, action_dim),
            )
        else:
            rng = np.random.default_rng(seed)
            scale = 0.1
            self.W1 = rng.standard_normal((state_dim, hidden_dim)).astype(np.float32) * scale
            self.b1 = np.zeros(hidden_dim, dtype=np.float32)
            self.W2 = rng.standard_normal((hidden_dim, hidden_dim)).astype(np.float32) * scale
            self.b2 = np.zeros(hidden_dim, dtype=np.float32)
            self.W3 = rng.standard_normal((hidden_dim, action_dim)).astype(np.float32) * scale
            self.b3 = np.zeros(action_dim, dtype=np.float32)

    def forward(self, x):
        if not TORCH_AVAILABLE:
            array = np.asarray(x, dtype=np.float32)
            h1 = np.tanh(array @ self.W1 + self.b1)
            h2 = np.tanh(h1 @ self.W2 + self.b2)
            return h2 @ self.W3 + self.b3

        tensor_input = torch.as_tensor(x, dtype=torch.float32)
        was_vector = tensor_input.ndim == 1
        if was_vector:
            tensor_input = tensor_input.unsqueeze(0)
        output = self.net(tensor_input)
        if was_vector:
            output = output.squeeze(0)
        if isinstance(x, np.ndarray):
            return output.detach().cpu().numpy()
        return output

    def num_params(self) -> int:
        if TORCH_AVAILABLE:
            return sum(param.numel() for param in self.parameters())
        return sum(param.size for param in (self.W1, self.b1, self.W2, self.b2, self.W3, self.b3))

    def state_dict(self):
        if TORCH_AVAILABLE:
            return super().state_dict()
        return {
            "W1": self.W1,
            "b1": self.b1,
            "W2": self.W2,
            "b2": self.b2,
            "W3": self.W3,
            "b3": self.b3,
        }

    def load_state_dict(self, state):
        if TORCH_AVAILABLE:
            return super().load_state_dict(state)
        self.W1 = state["W1"]
        self.b1 = state["b1"]
        self.W2 = state["W2"]
        self.b2 = state["b2"]
        self.W3 = state["W3"]
        self.b3 = state["b3"]
        return None

    def copy_from(self, other: "QNetwork"):
        self.load_state_dict(other.state_dict())


class ReplayBuffer:
    """Rolling replay buffer."""

    def __init__(self, capacity: int = 1000):
        self.capacity = capacity
        self.buffer: List[Dict] = []
        self.position = 0

    def push(self, state: np.ndarray, action: int, reward: float, next_state: np.ndarray):
        if len(self.buffer) < self.capacity:
            self.buffer.append({})
        self.buffer[self.position] = {
            "state": state.astype(np.float32),
            "action": int(action),
            "reward": float(reward),
            "next_state": next_state.astype(np.float32),
        }
        self.position = (self.position + 1) % self.capacity

    def sample(self, batch_size: int) -> List[Dict]:
        rng = np.random.default_rng()
        indices = rng.choice(len(self.buffer), min(batch_size, len(self.buffer)), replace=False)
        return [self.buffer[i] for i in indices]

    def __len__(self):
        return len(self.buffer)


class DQNPolicy:
    """Double DQN policy with synthetic cold-start data and incremental updates."""

    def __init__(self, model_dir: str = "models", seed: int = 42):
        self.state_dim = STATE_DIM
        self.action_dim = ACTION_DIM
        self.q_net = QNetwork(seed=seed, state_dim=self.state_dim, action_dim=self.action_dim)
        self.target_net = QNetwork(seed=seed, state_dim=self.state_dim, action_dim=self.action_dim)
        self._sync_target()
        self.replay = ReplayBuffer()
        self.epsilon = 0.3
        self.epsilon_min = 0.05
        self.gamma = 0.95
        self.lr = 0.001
        self.optimizer = optim.Adam(self.q_net.parameters(), lr=self.lr) if TORCH_AVAILABLE else None
        self.update_counter = 0
        self.update_freq = 50
        self.target_sync_freq = 250
        self.model_dir = model_dir
        self._storage = get_encrypted_storage()
        os.makedirs(model_dir, exist_ok=True)
        if not TORCH_AVAILABLE:
            logger.warning("PyTorch unavailable, DQNPolicy using NumPy fallback: %s", TORCH_IMPORT_ERROR)
        self._load_if_exists()
        self._pretrain_if_needed()
        logger.info("DQNPolicy initialized, params=%s", self.q_net.num_params())

    def _pretrain_if_needed(self):
        if len(self.replay) > 0:
            return
        synthetic_data = [
            {"hour": 22, "members": 2, "temp": 25, "humidity": 60, "last_scene": 3, "day": 4, "action": 0},
            {"hour": 23, "members": 2, "temp": 24, "humidity": 55, "last_scene": 0, "day": 4, "action": 0},
            {"hour": 7, "members": 2, "temp": 22, "humidity": 50, "last_scene": 0, "day": 5, "action": 7},
            {"hour": 8, "members": 2, "temp": 23, "humidity": 50, "last_scene": 4, "day": 5, "action": 1},
            {"hour": 9, "members": 0, "temp": 26, "humidity": 45, "last_scene": 1, "day": 1, "action": 2},
            {"hour": 20, "members": 2, "temp": 27, "humidity": 65, "last_scene": 1, "day": 6, "action": 3},
            {"hour": 21, "members": 3, "temp": 26, "humidity": 60, "last_scene": 2, "day": 6, "action": 8},
            {"hour": 18, "members": 1, "temp": 28, "humidity": 55, "last_scene": 0, "day": 3, "action": 6},
        ]

        for sample in synthetic_data:
            state = self._manual_state_vector(
                sample["hour"],
                sample["members"],
                sample["temp"],
                sample["last_scene"],
                sample["day"],
                sample["humidity"],
            )
            self.replay.push(state, sample["action"], 0.8, state)

        logger.info("DQN synthetic cold-start data injected")

    def _manual_state_vector(self, hour, members, temp, last_scene, day, humidity=50.0, device_state_score=0.0) -> np.ndarray:
        return np.array([
            hour / 23.0,
            (temp - 15.0) / 20.0,
            members / 5.0,
            (last_scene + 1) / 8.0,
            day / 6.0,
            humidity / 100.0,
            device_state_score,
        ], dtype=np.float32)

    def _state_to_vector(self, context) -> np.ndarray:
        devices = getattr(context, "devices", {}) or {}
        device_state_score = sum(1 for value in devices.values() if value == "开") / max(len(devices), 1)
        return np.array([
            getattr(context, "hour", 12) / 23.0,
            (getattr(context, "temperature", 26.0) - 15.0) / 20.0,
            getattr(context, "members_home", 1) / 5.0,
            (getattr(context, "last_scene", -1) + 1) / 8.0,
            getattr(context, "day_of_week", 0) / 6.0,
            getattr(context, "humidity", 50.0) / 100.0,
            device_state_score,
        ], dtype=np.float32)

    def _sync_target(self):
        self.target_net.copy_from(self.q_net)

    def recommend(self, context) -> Tuple[int, float]:
        state = self._state_to_vector(context)
        q_values = self.q_net.forward(state)
        q_max = float(np.max(q_values))
        q_sum = float(np.sum(np.abs(q_values)))
        confidence = q_max / (q_sum + 1e-6)

        action_dim = getattr(self.q_net, "action_dim", len(q_values))
        if np.random.rand() < self.epsilon:
            action = int(np.random.randint(0, action_dim))
        else:
            action = int(np.argmax(q_values))

        return action, confidence

    def record_feedback(self, context, action: int, user_response: str) -> bool:
        reward = REWARD_MAP.get(user_response, 0.0)
        state = self._state_to_vector(context)
        next_state = state.copy()
        self.replay.push(state, action, reward, next_state)

        self.update_counter += 1
        if self.update_counter % self.update_freq == 0 and len(self.replay) >= 10:
            self._light_update()

        logger.info("DQN feedback recorded: action=%s, reward=%s, buffer=%s", action, reward, len(self.replay))
        return True

    def _light_update(self):
        """Run a Double DQN update using online action selection and target scoring."""
        if not TORCH_AVAILABLE:
            self._numpy_light_update()
            return

        optimizer = getattr(self, "optimizer", None)
        if optimizer is None:
            optimizer = optim.Adam(self.q_net.parameters(), lr=getattr(self, "lr", 0.001))
            self.optimizer = optimizer

        batch = self.replay.sample(16)
        states = torch.as_tensor(np.stack([exp["state"] for exp in batch]), dtype=torch.float32)
        actions = torch.as_tensor([exp["action"] for exp in batch], dtype=torch.long)
        rewards = torch.as_tensor([exp["reward"] for exp in batch], dtype=torch.float32)
        next_states = torch.as_tensor(np.stack([exp["next_state"] for exp in batch]), dtype=torch.float32)

        current_q = self.q_net(states).gather(1, actions.unsqueeze(1)).squeeze(1)
        with torch.no_grad():
            next_actions = self.q_net(next_states).argmax(1)
            next_q = self.target_net(next_states).gather(1, next_actions.unsqueeze(1)).squeeze(1)
            target_q = rewards + self.gamma * next_q

        loss = nn.functional.smooth_l1_loss(current_q, target_q)
        optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.q_net.parameters(), 1.0)
        optimizer.step()

        self.epsilon = max(self.epsilon_min, self.epsilon * 0.99)

        if self.update_counter % getattr(self, "target_sync_freq", self.update_freq * 5) == 0:
            self._sync_target()
            logger.info("TargetNet synced")

    def _numpy_light_update(self):
        """Fallback update used only when PyTorch cannot be imported."""
        batch = self.replay.sample(16)
        for exp in batch:
            state = exp["state"].astype(np.float32)
            action = int(exp["action"])
            reward = float(exp["reward"])
            next_state = exp["next_state"].astype(np.float32)

            next_actions = self.q_net.forward(next_state).argmax()
            next_q = float(self.target_net.forward(next_state)[next_actions])
            target_q = reward + self.gamma * next_q

            h1 = np.tanh(state @ self.q_net.W1 + self.q_net.b1)
            h2 = np.tanh(h1 @ self.q_net.W2 + self.q_net.b2)
            current_q = float(self.q_net.forward(state)[action])
            delta = target_q - current_q
            self.q_net.W3[:, action] += self.lr * delta * h2
            self.q_net.b3[action] += self.lr * delta

        self.epsilon = max(self.epsilon_min, self.epsilon * 0.99)
        if self.update_counter % getattr(self, "target_sync_freq", self.update_freq * 5) == 0:
            self._sync_target()

    def save(self, path: str = ""):
        if not path:
            path = os.path.join(self.model_dir, "dqn_policy.pkl")
        data = {
            "q_net": self.q_net.state_dict(),
            "target_net": self.target_net.state_dict(),
            "epsilon": self.epsilon,
            "state_dim": self.state_dim,
            "action_dim": self.action_dim,
        }
        self._storage.save_pickle(data, path)

    def _load_if_exists(self):
        path = os.path.join(self.model_dir, "dqn_policy.pkl")
        if not os.path.exists(path):
            return False
        try:
            data = self._storage.load_pickle(path)
            if not data:
                return False
            if data.get("state_dim") != self.state_dim or data.get("action_dim") != self.action_dim:
                logger.warning("DQN model dimensions changed; starting from a cold model")
                return False
            self.q_net.load_state_dict(data["q_net"])
            self.target_net.load_state_dict(data.get("target_net", data["q_net"]))
            self.epsilon = data.get("epsilon", self.epsilon)
            logger.info("DQN policy loaded from encrypted storage")
            return True
        except Exception as exc:
            logger.warning("DQN load failed: %s", exc)
        return False
