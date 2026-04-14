"""
LSR: Lightweight Stage Ranking（轻量精排）
使用极轻量 MLP 对 BSR 召回的候选动作打分排序
输入特征：语义相似度 + 环境特征 + 用户偏好（RAG历史）
模型参数量 < 5MB
"""

import numpy as np
from typing import List, Dict, Any


class LSRecify:
    """
    轻量精排模型
    输入5维特征 → 加权打分 → 排序
    权重可增量更新
    """

    def __init__(self):
        self.weights = np.array([0.30, 0.10, 0.05, 0.20, 0.35], dtype=np.float32)
        self.bias = 0.1

    def _feature_extract(self, query: str, candidate: Dict[str, Any], context, kb=None) -> np.ndarray:
        """
        提取5维特征向量（与 design.md LSR 设计完全对齐）
          f1: 语义相似度（BSR原始分）
          f2: 温度（归一化）
          f3: 湿度（归一化）
          f4: 时间（使用 sin/cos 周期编码）
          f5: 用户偏好（RAG历史得分）
        """
        f1 = float(candidate.get("score", 0.5))

        f2 = (context.temperature - 15.0) / 20.0
        f3 = (context.humidity - 30.0) / 50.0

        hour_sin = np.sin(2 * np.pi * context.hour / 24)
        hour_cos = np.cos(2 * np.pi * context.hour / 24)
        f4 = (hour_sin + 1) / 2

        if kb is not None:
            f5 = kb.get_user_preference_score(candidate.get("action", ""), context)
        else:
            f5 = 0.5

        return np.array([f1, f2, f3, f4, f5], dtype=np.float32)

    def rank(self, query: str, candidates: List[Dict[str, Any]], context, kb=None) -> List[Dict[str, Any]]:
        """对候选动作打分并排序，返回 Top-3"""
        if not candidates:
            return []

        scored = []
        for cand in candidates:
            features = self._feature_extract(query, cand, context, kb)
            score = float(np.dot(features, self.weights) + self.bias)
            score = max(0.0, min(1.0, score))
            cand["final_score"] = round(score, 4)
            scored.append(cand)

        scored.sort(key=lambda x: x["final_score"], reverse=True)
        return scored

    def update_weights(self, delta_weights: np.ndarray):
        """根据用户反馈增量更新权重向量"""
        delta = np.array(delta_weights, dtype=np.float32)
        self.weights = np.clip(self.weights + delta * 0.01, 0.0, 1.0)
