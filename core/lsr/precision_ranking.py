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

        hour_sin = np.sin(2 * np.pi * context.hour / 24.0)
        hour_cos = np.cos(2 * np.pi * context.hour / 24.0)
        f4 = (hour_sin + 1.0) / 2.0

        if kb is not None:
            f5 = kb.get_user_preference_score(candidate.get("action", ""), context)
        else:
            f5 = 0.5

        return np.array([f1, f2, f3, f4, f5], dtype=np.float32)

    def _explicit_scene_target(self, query: str) -> str:
        scene_action_map = {
            "离家模式": "切换离家模式",
            "回家模式": "切换回家模式",
            "睡眠模式": "切换睡眠模式",
            "观影模式": "切换观影模式",
            "起床模式": "切换起床模式",
            "待客模式": "切换待客模式",
        }
        for scene, action in scene_action_map.items():
            if scene in query:
                return action

        colloquial_scene_map = {
            ("出门", "离家", "要走了", "我走了", "准备走", "马上走"): "切换离家模式",
            ("回家", "到家", "刚回来", "回来了"): "切换回家模式",
            ("睡觉", "睡了", "困", "睡眠"): "切换睡眠模式",
            ("观影", "看电影", "电影模式"): "切换观影模式",
            ("起床", "早安"): "切换起床模式",
            ("待客", "客人"): "切换待客模式",
        }
        for keywords, action in colloquial_scene_map.items():
            if any(keyword in query for keyword in keywords):
                return action
        return ""

    def _infer_device_from_action(self, action: str) -> str:
        for device in ("空调", "灯光", "电视", "风扇", "窗户", "音响", "热水器"):
            if device in action:
                return device
        return ""

    def _last_device_from_session(self, session_store=None) -> str:
        if session_store is None:
            return ""

        try:
            last_action = session_store.get_last_action()
        except Exception:
            last_action = {}
        device = str(last_action.get("device", "") or "")
        if device:
            return device

        try:
            runtime = session_store.get_runtime_context()
        except Exception:
            runtime = {}
        last_normalized = str(runtime.get("last_normalized_input", "") or "")
        return self._infer_device_from_action(last_normalized)

    def _explicit_device_target(self, query: str, session_store=None) -> str:
        direct_action_map = {
            "打开灯光": "打开灯光",
            "关闭灯光": "关闭灯光",
            "调亮灯光": "调亮灯光",
            "调暗灯光": "调暗灯光",
            "打开空调": "打开空调",
            "关闭空调": "关闭空调",
            "调高空调温度": "调高空调温度",
            "调低空调温度": "调低空调温度",
            "打开电视": "打开电视",
            "关闭电视": "关闭电视",
            "打开风扇": "打开风扇",
            "关闭风扇": "关闭风扇",
            "打开窗户": "打开窗户",
            "关闭窗户": "关闭窗户",
        }
        for phrase, action in direct_action_map.items():
            if phrase in query:
                return action

        colloquial_pairs = [
            (("开灯", "开一下灯", "把灯打开", "灯打开"), "打开灯光"),
            (("关灯", "关一下灯", "把灯关掉", "灯关掉"), "关闭灯光"),
            (("开空调", "把空调打开", "空调打开"), "打开空调"),
            (("关空调", "把空调关掉", "空调关掉"), "关闭空调"),
        ]
        for phrases, action in colloquial_pairs:
            if any(phrase in query for phrase in phrases):
                return action

        device = self._last_device_from_session(session_store)
        if device == "灯光":
            if any(token in query for token in ("再调亮", "再亮一点", "亮一点", "调亮一点", "再开亮一点")):
                return "调亮灯光"
            if any(token in query for token in ("再调暗", "再暗一点", "暗一点", "调暗一点")):
                return "调暗灯光"
        if device == "空调":
            if any(token in query for token in ("再调低", "低一点", "凉一点", "冷一点", "再冷一点")):
                return "调低空调温度"
            if any(token in query for token in ("再调高", "高一点", "热一点", "暖一点", "再热一点")):
                return "调高空调温度"
        return ""

    def _inject_explicit_candidate(self, candidates: List[Dict[str, Any]], explicit_action: str) -> List[Dict[str, Any]]:
        prepared = [dict(candidate) for candidate in candidates]
        if not explicit_action:
            return prepared
        if any(candidate.get("action", "") == explicit_action for candidate in prepared):
            return prepared
        prepared.append({
            "action": explicit_action,
            "source": "explicit_context",
            "score": 0.92,
        })
        return prepared

    def _explicit_device_penalty(self, explicit_action: str, candidate_action: str) -> float:
        if not explicit_action or not candidate_action or explicit_action == candidate_action:
            return 0.0

        explicit_device = self._infer_device_from_action(explicit_action)
        candidate_device = self._infer_device_from_action(candidate_action)
        if explicit_device and explicit_device == candidate_device:
            return 0.35
        return 0.0

    def rank(self, query: str, candidates: List[Dict[str, Any]], context, kb=None, session_store=None) -> List[Dict[str, Any]]:
        """对候选动作打分并排序，返回 Top-3"""
        if not candidates:
            return []

        scored = []
        explicit_scene_action = self._explicit_scene_target(query)
        explicit_device_action = self._explicit_device_target(query, session_store=session_store)
        prepared_candidates = self._inject_explicit_candidate(candidates, explicit_device_action)
        for cand in prepared_candidates:
            features = self._feature_extract(query, cand, context, kb)
            score = float(np.dot(features, self.weights) + self.bias)

            action = cand.get("action", "")
            if explicit_scene_action:
                if action == explicit_scene_action:
                    score += 0.35
                elif action.startswith("切换") and action != explicit_scene_action:
                    score -= 0.35
            if explicit_device_action:
                if action == explicit_device_action:
                    score += 0.45
                else:
                    score -= self._explicit_device_penalty(explicit_device_action, action)

            score = max(0.0, min(1.0, score))
            scored_candidate = dict(cand)
            scored_candidate["final_score"] = round(score, 4)
            scored.append(scored_candidate)

        scored.sort(key=lambda x: x["final_score"], reverse=True)
        return scored

    def update_weights(self, delta_weights: np.ndarray):
        """根据用户反馈增量更新权重向量"""
        delta = np.array(delta_weights, dtype=np.float32)
        self.weights = np.clip(self.weights + delta * 0.01, 0.0, 1.0)
